"""Backend vectorial local (numpy + JSON) compatible con el subconjunto de Chroma.

Se usa cuando ``VECTOR_BACKEND=local``. Chroma sigue siendo el backend por
defecto en producción; este backend permite operar el RAG en equipos donde el
binding nativo de Chroma no puede cargarse (por ejemplo, bloqueado por la
política de integridad de código de Windows).

El archivo de persistencia guarda todas las colecciones del directorio para
mantener la misma semántica que ``PersistentClient`` de Chroma, donde varias
colecciones comparten una ruta. Las escrituras se serializan por ruta y se
relee el archivo antes de guardar para no pisar colecciones abiertas en otra
instancia del mismo proceso.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Sequence

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - depende del entorno de despliegue
    raise RuntimeError(
        "numpy es obligatorio para el backend vectorial local; "
        "instálelo con `pip install numpy`"
    ) from exc

_FILE_NAME = "local_vectors.json"
_FORMAT_VERSION = 1
_DEFAULT_INCLUDE = ("documents", "metadatas", "distances")
_PATH_LOCKS: dict[str, RLock] = {}
_PATH_LOCKS_GUARD = RLock()


def _path_lock(path: Path) -> RLock:
    """Lock compartido por todas las instancias que escriben en una ruta."""
    key = str(path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _normalize_embeddings(raw: Any) -> list[list[float]]:
    if raw is None:
        return []
    try:
        array = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("Los embeddings deben ser numéricos") from exc
    if array.size == 0:
        return []
    if array.ndim == 1:
        # Un único vector plano: se acepta por comodidad, aunque Chroma exige
        # una lista de listas.
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise ValueError("Los embeddings deben ser una lista de vectores")
    return array.tolist()


def _validate_entry(entry: Any, dimensions: int | None) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("Entrada del índice vectorial local inválida")

    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("Entrada del índice vectorial local sin id válido")

    document = entry.get("document", "")
    if document is None:
        document = ""
    if not isinstance(document, str):
        raise ValueError(f"La entrada {identifier!r} tiene un documento inválido")

    metadata = entry.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"La entrada {identifier!r} tiene metadatos inválidos")

    embedding = entry.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError(f"La entrada {identifier!r} no tiene embedding")
    vector: list[float] = []
    for value in embedding:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"La entrada {identifier!r} tiene un embedding inválido")
        vector.append(float(value))
    if dimensions is not None and len(vector) != dimensions:
        raise ValueError(
            f"La entrada {identifier!r} tiene dimensiones ({len(vector)}) distintas "
            f"a las declaradas ({dimensions})"
        )

    return {
        "id": identifier,
        "document": document,
        "metadata": dict(metadata),
        "embedding": vector,
    }


def _cosine_distances(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = float(np.linalg.norm(query))
    if query_norm == 0.0:
        return np.ones(matrix.shape[0], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1)
    dots = matrix @ query
    safe_norms = np.where(norms == 0.0, 1.0, norms)
    distances = 1.0 - (dots / (safe_norms * query_norm))
    return np.where(norms == 0.0, 1.0, distances).astype(np.float64)


class LocalVectorCollection:
    """Colección vectorial persistida en JSON con distancia coseno.

    Implementa ``add``, ``count`` y ``query`` con la misma forma de retorno que
    usa ``RagRetriever``: listas por consulta para ``ids``, ``documents``,
    ``metadatas`` y ``distances``.
    """

    def __init__(
        self,
        path: str | Path,
        name: str,
        *,
        dimensions: int | None = None,
    ) -> None:
        if not name or not name.strip():
            raise ValueError("El nombre de la colección no puede estar vacío")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("dimensions debe ser positivo")

        self._path = Path(path)
        self._name = name
        self._file = self._path / _FILE_NAME
        self._lock = RLock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._dimensions = dimensions
        self._load()

    @property
    def name(self) -> str:
        return self._name

    def _read_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"No se pudo leer el índice vectorial local {self._file}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"El índice vectorial local {self._file} no tiene el formato esperado"
            )
        version = payload.get("version", _FORMAT_VERSION)
        if version != _FORMAT_VERSION:
            raise ValueError(
                f"Versión del índice vectorial local no soportada: {version!r}"
            )
        collections = payload.get("collections")
        if not isinstance(collections, dict):
            raise ValueError(
                f"El índice vectorial local {self._file} no tiene el formato esperado"
            )
        return payload

    def _load(self) -> None:
        if not self._file.is_file():
            return
        payload = self._read_payload()
        collections = payload["collections"]
        data = collections.get(self._name)
        if not isinstance(data, dict):
            return

        stored_dimensions = data.get("dimensions")
        if stored_dimensions is not None:
            if not isinstance(stored_dimensions, int) or stored_dimensions <= 0:
                raise ValueError(
                    f"Dimensiones inválidas en el índice local: {stored_dimensions!r}"
                )
            if self._dimensions is not None and self._dimensions != stored_dimensions:
                raise ValueError(
                    f"dimensiones del índice local ({stored_dimensions}) distintas a las "
                    f"configuradas ({self._dimensions}); reingiera con el modelo correcto"
                )
            self._dimensions = stored_dimensions

        entries = data.get("entries")
        if not isinstance(entries, list):
            raise ValueError(f"La colección {self._name!r} no tiene una lista de entradas")
        for raw_entry in entries:
            entry = _validate_entry(raw_entry, self._dimensions)
            if self._dimensions is None:
                self._dimensions = len(entry["embedding"])
            self._entries[entry["id"]] = entry

    def _read_collections_for_write(self) -> dict[str, Any]:
        if not self._file.is_file():
            return {}
        payload = self._read_payload()
        return dict(payload["collections"])

    def _save(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        collections = self._read_collections_for_write()
        existing = collections.get(self._name)
        merged: dict[str, dict[str, Any]] = {}
        if isinstance(existing, dict):
            existing_entries = existing.get("entries")
            if isinstance(existing_entries, list):
                for entry in existing_entries:
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                        merged[entry["id"]] = entry
        # Las entradas en memoria ganan sobre las del disco para el mismo id.
        merged.update(self._entries)
        collections[self._name] = {
            "dimensions": self._dimensions,
            "entries": list(merged.values()),
        }
        payload = {"version": _FORMAT_VERSION, "collections": collections}
        temporary = self._file.with_name(f"{self._file.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._file)

    def add(
        self,
        ids: Sequence[str],
        embeddings: Any = None,
        metadatas: Sequence[dict[str, Any]] | None = None,
        documents: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Inserta o reemplaza entradas. Las dimensiones deben ser coherentes."""
        if kwargs:
            raise NotImplementedError(
                "El backend local no soporta los parámetros de add: "
                + ", ".join(sorted(kwargs))
            )

        identifiers = [str(identifier) for identifier in ids]
        if not identifiers:
            return

        vectors = _normalize_embeddings(embeddings)
        if not vectors:
            raise ValueError(
                "embeddings es obligatorio para el backend local; "
                "la ingesta debe usar un embedder"
            )
        if len(vectors) != len(identifiers):
            raise ValueError("ids y embeddings deben tener la misma longitud")

        dimension = len(vectors[0])
        if dimension == 0:
            raise ValueError("Los embeddings no pueden estar vacíos")
        if any(len(vector) != dimension for vector in vectors):
            raise ValueError("Todos los embeddings deben tener la misma dimensión")
        if self._dimensions is not None and dimension != self._dimensions:
            raise ValueError(
                f"dimensiones del embedder ({dimension}) distintas a las declaradas "
                f"({self._dimensions}); revise EMBEDDING_MODEL y EMBEDDING_DIMENSIONS"
            )

        texts = list(documents) if documents is not None else [""] * len(identifiers)
        metadatas = list(metadatas) if metadatas is not None else [{}] * len(identifiers)
        if len(texts) != len(identifiers) or len(metadatas) != len(identifiers):
            raise ValueError("ids, documents y metadatas deben tener la misma longitud")

        with self._lock:
            with _path_lock(self._path):
                previous_entries = dict(self._entries)
                previous_dimensions = self._dimensions
                try:
                    if self._dimensions is None:
                        self._dimensions = dimension
                    for identifier, vector, metadata, text in zip(
                        identifiers, vectors, metadatas, texts
                    ):
                        self._entries[identifier] = {
                            "id": identifier,
                            "document": text,
                            "metadata": dict(metadata or {}),
                            "embedding": vector,
                        }
                    self._save()
                except Exception:
                    self._entries = previous_entries
                    self._dimensions = previous_dimensions
                    raise

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def query(
        self,
        query_embeddings: Any = None,
        n_results: int = 10,
        include: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[list[Any]]]:
        if kwargs:
            raise NotImplementedError(
                "El backend local no soporta los parámetros de query: "
                + ", ".join(sorted(kwargs))
            )
        if include is not None and isinstance(include, str):
            raise ValueError("include debe ser una lista de campos")

        vectors = _normalize_embeddings(query_embeddings)
        if not vectors:
            raise ValueError("query_embeddings es obligatorio")
        if self._dimensions is not None:
            for vector in vectors:
                if len(vector) != self._dimensions:
                    raise ValueError(
                        f"dimensiones de la consulta ({len(vector)}) distintas a las "
                        f"declaradas ({self._dimensions})"
                    )

        requested = set(_DEFAULT_INCLUDE if include is None else include)
        unsupported = requested - set(_DEFAULT_INCLUDE) - {"ids"}
        if unsupported:
            raise NotImplementedError(
                "El backend local no soporta los campos de include: "
                + ", ".join(sorted(unsupported))
            )
        limit = max(int(n_results), 0)

        with self._lock:
            entries = list(self._entries.values())

        ids_batch: list[list[str]] = []
        documents_batch: list[list[str]] = []
        metadatas_batch: list[list[dict[str, Any]]] = []
        distances_batch: list[list[float]] = []

        if entries:
            matrix = np.asarray([entry["embedding"] for entry in entries], dtype=np.float64)
        else:
            matrix = np.empty((0, self._dimensions or 0), dtype=np.float64)

        for vector in vectors:
            if not entries or limit == 0:
                selected: list[dict[str, Any]] = []
                distances: list[float] = []
            else:
                query = np.asarray(vector, dtype=np.float64)
                computed = _cosine_distances(query, matrix)
                order = np.argsort(computed, kind="stable")[:limit]
                selected = [entries[int(index)] for index in order]
                distances = [float(computed[int(index)]) for index in order]

            ids_batch.append([entry["id"] for entry in selected])
            if "documents" in requested:
                documents_batch.append([str(entry.get("document") or "") for entry in selected])
            if "metadatas" in requested:
                metadatas_batch.append(
                    [dict(entry.get("metadata") or {}) for entry in selected]
                )
            if "distances" in requested:
                distances_batch.append(distances)

        result: dict[str, list[list[Any]]] = {"ids": ids_batch}
        if "documents" in requested:
            result["documents"] = documents_batch
        if "metadatas" in requested:
            result["metadatas"] = metadatas_batch
        if "distances" in requested:
            result["distances"] = distances_batch
        return result

    def close(self) -> None:
        """Compatibilidad con ``CorpusManager.close``; no hay recursos abiertos."""
