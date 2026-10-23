from __future__ import annotations

from contafis_assistant.domain.contracts import ObligationsResult, Source
from contafis_assistant.domain.enums import CorpusStatus, Intent, Status, WarningCode
from contafis_assistant.rag.retriever import RagResult
from contafis_assistant.rag.tool import query_normativa

SOURCE = Source(
    document_id="doc-1",
    document="Ley de IVA",
    page=4,
    section="Artículo 10",
    locator="p.4",
    chunk_id="c1",
    score=1 / 61,
    collection="contafis_normativa_v1",
)


def _codes(warnings):
    return {warning.code for warning in warnings}


def _erp_result():
    return ObligationsResult(total=1, returned=0, truncated=False)


class FakeRetriever:
    def __init__(self, result: RagResult):
        self.result = result
        self.queries = []

    def retrieve(self, query: str) -> RagResult:
        self.queries.append(query)
        return self.result


def test_normative_tool_with_fake_retriever_returns_sources():
    retriever = FakeRetriever(
        RagResult(
            sources=[SOURCE],
            warnings=[],
            corpus_status=CorpusStatus.ok,
        )
    )

    result = query_normativa("¿cuál es la alícuota de IVA?", retriever=retriever)

    assert result.status == Status.ok
    assert result.sources == [SOURCE]
    assert result.data == []
    assert result.tools_used == ["query_normativa"]
    assert retriever.queries == ["¿cuál es la alícuota de IVA?"]


def test_rag_only_without_corpus_is_upstream_error():
    retriever = FakeRetriever(
        RagResult(
            sources=[],
            warnings=[],
            corpus_status=CorpusStatus.missing,
        )
    )

    result = query_normativa("¿cuál es la alícuota de IVA?", retriever=retriever)

    assert result.status == Status.upstream_error
    assert result.sources == []
    assert WarningCode.corpus_unavailable in _codes(result.warnings)
    assert WarningCode.manifest_mismatch not in _codes(result.warnings)


def test_rag_only_with_manifest_mismatch_adds_warning():
    retriever = FakeRetriever(
        RagResult(
            sources=[],
            warnings=[],
            corpus_status=CorpusStatus.mismatch,
        )
    )

    result = query_normativa("¿cuál es la alícuota de IVA?", retriever=retriever)

    assert result.status == Status.upstream_error
    assert result.sources == []
    assert WarningCode.corpus_unavailable in _codes(result.warnings)
    assert WarningCode.manifest_mismatch in _codes(result.warnings)


def test_rag_only_without_signal_is_insufficient_context():
    retriever = FakeRetriever(
        RagResult(
            sources=[],
            warnings=[],
            corpus_status=CorpusStatus.ok,
        )
    )

    result = query_normativa("tema inexistente", retriever=retriever)

    assert result.status == Status.insufficient_context
    assert result.sources == []
    assert WarningCode.retrieval_low_score in _codes(result.warnings)


def test_mixed_low_rag_keeps_erp_data_and_returns_partial():
    erp_data = [_erp_result()]
    retriever = FakeRetriever(
        RagResult(
            sources=[],
            warnings=[],
            corpus_status=CorpusStatus.ok,
        )
    )

    result = query_normativa(
        "IVA de mis obligaciones",
        retriever=retriever,
        intent=Intent.mixta,
        erp_data=erp_data,
    )

    assert result.status == Status.partial
    assert result.data == erp_data
    assert result.sources == []
    assert WarningCode.retrieval_low_score in _codes(result.warnings)


def test_mixed_corpus_down_keeps_erp_data_and_returns_partial():
    erp_data = [_erp_result()]
    retriever = FakeRetriever(
        RagResult(
            sources=[],
            warnings=[],
            corpus_status=CorpusStatus.missing,
        )
    )

    result = query_normativa(
        "IVA de mis obligaciones",
        retriever=retriever,
        intent=Intent.mixta,
        erp_data=erp_data,
    )

    assert result.status == Status.partial
    assert result.data == erp_data
    assert result.sources == []
    assert WarningCode.source_unavailable in _codes(result.warnings)


def test_mixed_with_rag_and_erp_is_ok():
    erp_data = [_erp_result()]
    retriever = FakeRetriever(
        RagResult(
            sources=[SOURCE],
            warnings=[],
            corpus_status=CorpusStatus.ok,
        )
    )

    result = query_normativa(
        "IVA de mis obligaciones",
        retriever=retriever,
        intent=Intent.mixta,
        erp_data=erp_data,
    )

    assert result.status == Status.ok
    assert result.sources == [SOURCE]
    assert result.data == erp_data
