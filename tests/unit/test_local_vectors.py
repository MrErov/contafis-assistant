"""Backend vectorial local (numpy + JSON) compatible con el subconjunto de Chroma."""

from __future__ import annotations

import json
import math

import pytest

from contafis_assistant.rag.local_vectors import LocalVectorCollection


def test_add_and_count_roundtrip(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)

    collection.add(
        ids=["a", "b", "c"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        documents=["doc a", "doc b", "doc c"],
        metadatas=[{"n": 1}, {"n": 2}, {"n": 3}],
    )

    assert collection.count() == 3


def test_query_orders_by_cosine_distance(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(
        ids=["cercano", "medio", "lejano"],
        embeddings=[[1.0, 0.0], [0.7, 0.7], [0.0, 1.0]],
        documents=["cercano", "medio", "lejano"],
        metadatas=[{"id": "cercano"}, {"id": "medio"}, {"id": "lejano"}],
    )

    result = collection.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=3,
        include=["documents", "metadatas", "distances"],
    )

    assert result["ids"] == [["cercano", "medio", "lejano"]]
    distances = result["distances"][0]
    assert distances == sorted(distances)
    assert math.isclose(distances[0], 0.0, abs_tol=1e-9)
    assert math.isclose(distances[2], 1.0, abs_tol=1e-9)
    assert result["documents"] == [["cercano", "medio", "lejano"]]
    assert result["metadatas"] == [[{"id": "cercano"}, {"id": "medio"}, {"id": "lejano"}]]


def test_query_respects_n_results(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(
        ids=["a", "b", "c"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        documents=["a", "b", "c"],
        metadatas=[{}, {}, {}],
    )

    result = collection.query(query_embeddings=[[1.0, 0.0]], n_results=2)

    assert len(result["ids"][0]) == 2


def test_query_on_empty_collection_returns_empty_lists(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)

    result = collection.query(query_embeddings=[[1.0, 0.0]], n_results=5)

    assert result == {
        "ids": [[]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }


def test_persistence_reopens_existing_collection(tmp_path):
    first = LocalVectorCollection(tmp_path, "col", dimensions=2)
    first.add(
        ids=["a"],
        embeddings=[[1.0, 0.0]],
        documents=["doc a"],
        metadatas=[{"document_id": "doc-1"}],
    )

    reopened = LocalVectorCollection(tmp_path, "col", dimensions=2)

    assert reopened.count() == 1
    result = reopened.query(query_embeddings=[[1.0, 0.0]], n_results=1)
    assert result["ids"] == [["a"]]
    assert result["metadatas"] == [[{"document_id": "doc-1"}]]


def test_collections_are_isolated_by_name(tmp_path):
    LocalVectorCollection(tmp_path, "uno", dimensions=2).add(
        ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}]
    )
    LocalVectorCollection(tmp_path, "dos", dimensions=2).add(
        ids=["b"], embeddings=[[0.0, 1.0]], documents=["b"], metadatas=[{}]
    )

    assert LocalVectorCollection(tmp_path, "uno").count() == 1
    assert LocalVectorCollection(tmp_path, "dos").count() == 1
    assert LocalVectorCollection(tmp_path, "tres").count() == 0


def test_add_requires_embeddings(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)

    with pytest.raises(ValueError, match="embeddings"):
        collection.add(ids=["a"], documents=["a"], metadatas=[{}])


def test_add_rejects_dimension_mismatch(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)

    with pytest.raises(ValueError, match="dimensiones"):
        collection.add(ids=["a"], embeddings=[[1.0, 0.0, 0.0]], documents=["a"], metadatas=[{}])


def test_declared_dimensions_are_enforced_on_reopen(tmp_path):
    LocalVectorCollection(tmp_path, "col", dimensions=2).add(
        ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}]
    )

    with pytest.raises(ValueError, match="dimensiones"):
        LocalVectorCollection(tmp_path, "col", dimensions=3)


def test_repeated_id_replaces_the_previous_entry(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["viejo"], metadatas=[{}])
    collection.add(ids=["a"], embeddings=[[0.0, 1.0]], documents=["nuevo"], metadatas=[{}])

    assert collection.count() == 1
    result = collection.query(query_embeddings=[[0.0, 1.0]], n_results=1)
    assert result["documents"] == [["nuevo"]]


def test_two_collections_do_not_overwrite_each_other(tmp_path):
    first = LocalVectorCollection(tmp_path, "uno", dimensions=2)
    second = LocalVectorCollection(tmp_path, "dos", dimensions=2)

    first.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])
    second.add(ids=["b"], embeddings=[[0.0, 1.0]], documents=["b"], metadatas=[{}])

    assert LocalVectorCollection(tmp_path, "uno").count() == 1
    assert LocalVectorCollection(tmp_path, "dos").count() == 1


def test_unsupported_query_filters_fail_loudly(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])

    with pytest.raises(NotImplementedError, match="where"):
        collection.query(
            query_embeddings=[[1.0, 0.0]], n_results=1, where={"document_id": "doc-1"}
        )


def test_unsupported_include_entries_fail_loudly(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])

    with pytest.raises(NotImplementedError, match="embeddings"):
        collection.query(
            query_embeddings=[[1.0, 0.0]], n_results=1, include=["embeddings"]
        )


def test_unsupported_add_arguments_fail_loudly(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)

    with pytest.raises(NotImplementedError, match="uris"):
        collection.add(
            ids=["a"],
            embeddings=[[1.0, 0.0]],
            documents=["a"],
            metadatas=[{}],
            uris=["file:///a"],
        )


def test_invalid_top_level_payload_is_rejected(tmp_path):
    (tmp_path / "local_vectors.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="formato"):
        LocalVectorCollection(tmp_path, "col")


def test_unknown_format_version_is_rejected(tmp_path):
    payload = {"version": 999, "collections": {}}
    (tmp_path / "local_vectors.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="[Vv]ersión"):
        LocalVectorCollection(tmp_path, "col")


def test_entry_without_embedding_is_rejected(tmp_path):
    payload = {
        "version": 1,
        "collections": {
            "col": {"dimensions": 2, "entries": [{"id": "a", "document": "a", "metadata": {}}]}
        },
    }
    (tmp_path / "local_vectors.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="embedding"):
        LocalVectorCollection(tmp_path, "col")


def test_entry_with_mixed_dimensions_is_rejected(tmp_path):
    payload = {
        "version": 1,
        "collections": {
            "col": {
                "dimensions": 2,
                "entries": [
                    {"id": "a", "document": "a", "metadata": {}, "embedding": [1.0, 0.0]},
                    {"id": "b", "document": "b", "metadata": {}, "embedding": [1.0, 0.0, 0.0]},
                ],
            }
        },
    }
    (tmp_path / "local_vectors.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="dimensiones"):
        LocalVectorCollection(tmp_path, "col")


def test_failed_save_rolls_back_in_memory_state(tmp_path, monkeypatch):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])

    def broken_save() -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(collection, "_save", broken_save)

    with pytest.raises(OSError, match="disco lleno"):
        collection.add(ids=["b"], embeddings=[[0.0, 1.0]], documents=["b"], metadatas=[{}])

    assert collection.count() == 1


def test_query_accepts_a_flat_vector(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])

    result = collection.query(query_embeddings=[1.0, 0.0], n_results=1)

    assert result["ids"] == [["a"]]
    assert math.isclose(result["distances"][0][0], 0.0, abs_tol=1e-9)


def test_zero_vector_query_returns_distance_one(tmp_path):
    collection = LocalVectorCollection(tmp_path, "col", dimensions=2)
    collection.add(ids=["a"], embeddings=[[1.0, 0.0]], documents=["a"], metadatas=[{}])

    result = collection.query(query_embeddings=[[0.0, 0.0]], n_results=1)

    assert result["distances"] == [[1.0]]
