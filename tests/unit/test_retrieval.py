from __future__ import annotations

import pytest

from contafis_assistant.domain.enums import CorpusStatus, WarningCode
from contafis_assistant.rag.bm25 import Bm25Document, Bm25Hit, Bm25Index
from contafis_assistant.rag.fusion import DenseHit, cosine_similarity, fuse_results
from contafis_assistant.rag.retriever import RagRetriever


def _dense(
    chunk_id: str,
    distance: float,
    *,
    document_id: str = "doc-1",
    page: int | None = 1,
    section: str | None = "Artículo 1",
    locator: str | None = None,
    collection: str = "contafis_normativa_v1",
) -> DenseHit:
    metadata = {
        "document": f"Documento {document_id}",
        "collection": collection,
    }
    if page is not None:
        metadata["page"] = page
    if section is not None:
        metadata["section"] = section
    if locator is not None:
        metadata["locator"] = locator
    return DenseHit(
        chunk_id=chunk_id,
        document_id=document_id,
        distance=distance,
        text=f"texto {chunk_id}",
        metadata=metadata,
    )


def _bm25(
    chunk_id: str,
    score: float,
    *,
    document_id: str = "doc-1",
    page: int | None = 1,
) -> Bm25Hit:
    metadata = {
        "document": f"Documento {document_id}",
        "collection": "contafis_normativa_v1",
    }
    if page is not None:
        metadata["page"] = page
    return Bm25Hit(
        chunk_id=chunk_id,
        document_id=document_id,
        score=score,
        text=f"texto {chunk_id}",
        metadata=metadata,
    )


def _codes(warnings):
    return {warning.code for warning in warnings}


def test_chroma_distance_is_converted_to_cosine_similarity():
    assert cosine_similarity(0.0) == pytest.approx(1.0)
    assert cosine_similarity(0.75) == pytest.approx(0.25)

    result = fuse_results([_dense("c1", 0.75)], [])

    assert result.sources == []
    assert result.has_signal is False
    assert WarningCode.retrieval_low_score in _codes(result.warnings)


def test_distance_is_not_compared_directly_with_the_threshold():
    accepted = fuse_results([_dense("c1", 0.70)], [])

    assert len(accepted.sources) == 1
    assert accepted.sources[0].chunk_id == "c1"


@pytest.mark.parametrize(
    ("dense_distance", "bm25_score", "has_signal"),
    [
        (0.71, None, False),
        (0.70, None, True),
        (None, 0.99, False),
        (None, 1.00, True),
    ],
)
def test_dense_or_bm25_thresholds(dense_distance, bm25_score, has_signal):
    dense_hits = [] if dense_distance is None else [_dense("c1", dense_distance)]
    bm25_hits = [] if bm25_score is None else [_bm25("c1", bm25_score)]

    result = fuse_results(dense_hits, bm25_hits)

    assert result.has_signal is has_signal
    assert bool(result.sources) is has_signal


def test_threshold_is_applied_before_rrf_and_rank_is_recomputed():
    result = fuse_results(
        [_dense("bajo", 0.90), _dense("alto", 0.50)],
        [],
    )

    assert [source.chunk_id for source in result.sources] == ["alto"]
    assert result.sources[0].score == pytest.approx(1 / 61)


def test_no_signal_does_not_invent_sources():
    result = fuse_results([], [])

    assert result.sources == []
    assert result.has_signal is False
    assert _codes(result.warnings) == {WarningCode.retrieval_low_score}


def test_rrf_uses_k_60_and_returns_top_5_with_fused_score():
    dense_hits = [_dense(f"c{i}", 0.10 + i * 0.05) for i in range(6)]

    result = fuse_results(dense_hits, [])

    assert [source.chunk_id for source in result.sources] == [
        "c0",
        "c1",
        "c2",
        "c3",
        "c4",
    ]
    assert [source.score for source in result.sources] == pytest.approx(
        [1 / 61, 1 / 62, 1 / 63, 1 / 64, 1 / 65]
    )


def test_same_chunk_in_both_rankings_sums_rrf_contributions():
    result = fuse_results(
        [_dense("compartido", 0.10)],
        [_bm25("compartido", 2.0)],
    )

    assert len(result.sources) == 1
    assert result.sources[0].score == pytest.approx(2 / 61)


def test_source_is_completed_with_metadata_and_page_is_one_based():
    result = fuse_results(
        [_dense("c1", 0.10, page=3, section="Artículo 7", locator="p.3")],
        [],
        collection="contafis_normativa_v1",
    )

    source = result.sources[0]
    assert source.document_id == "doc-1"
    assert source.document == "Documento doc-1"
    assert source.page == 3
    assert source.section == "Artículo 7"
    assert source.locator == "p.3"
    assert source.chunk_id == "c1"
    assert source.collection == "contafis_normativa_v1"


def test_missing_page_metadata_yields_none_and_warning():
    result = fuse_results([_dense("c1", 0.10, page=None)], [])

    source = result.sources[0]
    assert source.page is None
    assert WarningCode.page_metadata_missing in _codes(result.warnings)


def test_documents_outside_the_manifest_are_not_returned():
    result = fuse_results(
        [_dense("c1", 0.10, document_id="doc-1"), _dense("c2", 0.05, document_id="doc-2")],
        [],
        allowed_document_ids={"doc-1"},
    )

    assert [source.document_id for source in result.sources] == ["doc-1"]


def test_only_document_outside_manifest_produces_no_source():
    result = fuse_results(
        [_dense("c2", 0.05, document_id="doc-2")],
        [],
        allowed_document_ids={"doc-1"},
    )

    assert result.sources == []


def test_bm25_search_returns_top_10_with_scores():
    documents = [
        Bm25Document(
            chunk_id=f"c{i}",
            document_id="doc-1",
            text="iva impuesto al valor agregado",
            metadata={"page": 1},
        )
        for i in range(12)
    ]
    index = Bm25Index.build(documents, version=1, collection="col")

    hits = index.search("iva", top_k=10)

    assert len(hits) == 10
    assert all(hit.score >= 0 for hit in hits)
    assert [hit.score for hit in hits] == sorted(
        [hit.score for hit in hits], reverse=True
    )


class FakeBm25Index:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, top_k=10):
        self.calls.append((query, top_k))
        return list(self.hits)


class FakeCorpus:
    def __init__(
        self,
        status: CorpusStatus = CorpusStatus.ok,
        *,
        bm25_index=None,
        allowed_document_ids=None,
        collection=None,
        collection_name: str = "col",
        manifest_titles=None,
    ):
        self.status = status
        self.bm25_index = bm25_index
        self.allowed_document_ids = allowed_document_ids
        self.collection = collection
        self.collection_name = collection_name
        self.manifest_titles = manifest_titles or {}


class FakeCollection:
    def __init__(self, response):
        self.response = response
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.response


def test_retriever_uses_top_10_for_dense_and_bm25():
    bm25_index = FakeBm25Index([_bm25("b1", 2.0)])
    collection = FakeCollection(
        {
            "ids": [["d1"]],
            "documents": [["texto d1"]],
            "metadatas": [[{"document_id": "doc-1", "page": 1, "collection": "col"}]],
            "distances": [[0.10]],
        }
    )
    corpus = FakeCorpus(
        bm25_index=bm25_index,
        allowed_document_ids={"doc-1"},
        collection=collection,
        collection_name="col",
    )
    dense_calls = []

    def dense_search(query, top_k):
        dense_calls.append((query, top_k))
        return [_dense("d1", 0.10)]

    retriever = RagRetriever(
        corpus,
        dense_search=dense_search,
        embedder=lambda texts: [[0.1, 0.2] for _ in texts],
    )

    result = retriever.retrieve("iva")

    assert dense_calls == [("iva", 10)]
    assert bm25_index.calls == [("iva", 10)]
    assert result.sources
    assert result.corpus_status == CorpusStatus.ok


def test_retriever_queries_chroma_with_embeddings_and_top_10():
    collection = FakeCollection(
        {
            "ids": [["d1"]],
            "documents": [["texto d1"]],
            "metadatas": [[{"document_id": "doc-1", "page": 2, "collection": "col"}]],
            "distances": [[0.10]],
        }
    )
    corpus = FakeCorpus(
        bm25_index=FakeBm25Index([]),
        allowed_document_ids={"doc-1"},
        collection=collection,
        collection_name="col",
    )
    retriever = RagRetriever(
        corpus,
        embedder=lambda texts: [[0.1, 0.2] for _ in texts],
    )

    result = retriever.retrieve("iva")

    assert collection.queries
    assert collection.queries[0]["n_results"] == 10
    assert collection.queries[0]["query_embeddings"] == [[0.1, 0.2]]
    assert result.sources[0].chunk_id == "d1"


def test_retriever_reports_invalid_corpus_without_querying():
    def dense_search(query, top_k):
        raise AssertionError("no debe consultar dense sin corpus")

    corpus = FakeCorpus(status=CorpusStatus.mismatch)
    retriever = RagRetriever(corpus, dense_search=dense_search)

    result = retriever.retrieve("iva")

    assert result.sources == []
    assert WarningCode.corpus_unavailable in _codes(result.warnings)
    assert WarningCode.manifest_mismatch in _codes(result.warnings)


def test_retriever_reports_low_score_without_sources():
    corpus = FakeCorpus(
        bm25_index=FakeBm25Index([]),
        allowed_document_ids={"doc-1"},
        collection=FakeCollection({"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}),
    )
    retriever = RagRetriever(
        corpus,
        dense_search=lambda query, top_k: [],
        embedder=lambda texts: [[0.1] for _ in texts],
    )

    result = retriever.retrieve("iva")

    assert result.sources == []
    assert WarningCode.retrieval_low_score in _codes(result.warnings)
