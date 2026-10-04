import atexit
import hashlib
import logging
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from config import config

logger = logging.getLogger("clanfall.vector_store")


def empty_query_result() -> Dict[str, Any]:
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class DeterministicOfflineEmbedding:
    """
    Lightweight deterministic hashed bag-of-words embedding (64-dim by default).
    Identical across every backend, so Tencent VectorDB, Chroma and the
    in-memory store all rank the same documents the same way.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    @classmethod
    def name(cls) -> str:
        return "deterministic_offline_embedding"

    def get_config(self) -> dict:
        return {"dim": self.dim}

    @classmethod
    def build_from_config(cls, cfg: dict) -> "DeterministicOfflineEmbedding":
        return cls(dim=cfg.get("dim", 64))

    def _embed_texts(self, texts: Any) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for text in texts:
            cleaned = text.lower()
            for ch in ",:-_.'\"()?!":
                cleaned = cleaned.replace(ch, " ")
            tokens = cleaned.split()
            vec = [0.0] * self.dim
            for i, token in enumerate(tokens):
                h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
                idx1 = h % self.dim
                idx2 = (h >> 7) % self.dim
                weight = 1.0 / (math.log(i + 2))
                vec[idx1] += weight
                vec[idx2] += 0.5 * weight
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            embeddings.append([round(x / norm, 6) for x in vec])
        return embeddings

    def __call__(self, input: Any) -> Any:
        return self._embed_texts(input)

    def embed_query(self, input: Any) -> Any:
        return self._embed_texts(input)

    def embed_documents(self, input: Any) -> Any:
        return self._embed_texts(input)

    def is_legacy(self) -> bool:
        return True


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _flat_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if value is None:
            continue
        out[str(key)] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return out


@runtime_checkable
class VectorBackend(Protocol):
    name: str

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        ...

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        ...

    def count(self) -> int:
        ...

    def ping(self) -> bool:
        ...

    def reset(self) -> None:
        ...


class InMemoryBackend:
    """Pure-Python cosine store. Zero dependencies, process-local, gone on reset."""

    name = "memory"

    def __init__(self, embedding_fn: Optional[DeterministicOfflineEmbedding] = None):
        self.embedding_fn = embedding_fn or DeterministicOfflineEmbedding(dim=config.memory.embedding_dim)
        self._rows: Dict[str, Dict[str, Any]] = {}

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        vectors = self.embedding_fn(documents)
        for doc_id, doc, meta, vec in zip(ids, documents, metadatas, vectors):
            self._rows[str(doc_id)] = {"document": doc, "metadata": _flat_metadata(meta), "vector": vec}

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        if not self._rows:
            return empty_query_result()
        qvec = self.embedding_fn([query_text])[0]
        scored = sorted(
            ((doc_id, _cosine(qvec, row["vector"])) for doc_id, row in self._rows.items()),
            key=lambda item: item[1],
            reverse=True,
        )[: max(1, n_results)]
        return {
            "ids": [[doc_id for doc_id, _ in scored]],
            "documents": [[self._rows[doc_id]["document"] for doc_id, _ in scored]],
            "metadatas": [[dict(self._rows[doc_id]["metadata"]) for doc_id, _ in scored]],
            "distances": [[round(1.0 - sim, 6) for _, sim in scored]],
        }

    def count(self) -> int:
        return len(self._rows)

    def ping(self) -> bool:
        return True

    def reset(self) -> None:
        self._rows.clear()


class ChromaBackend:
    """
    Chroma *ephemeral* collection. AeroCortex used a PersistentClient so
    experience survived restarts; Clanfall must forget every match, so nothing
    is written to disk.
    """

    name = "chroma"

    def __init__(self, collection_name: Optional[str] = None, embedding_fn: Optional[DeterministicOfflineEmbedding] = None):
        import chromadb

        self.embedding_fn = embedding_fn or DeterministicOfflineEmbedding(dim=config.memory.embedding_dim)
        self.collection_name = collection_name or config.memory.chroma_collection
        self.client = chromadb.EphemeralClient()
        self.collection = self._create()

    def _create(self):
        return self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        self.collection.upsert(ids=ids, documents=documents, metadatas=[_flat_metadata(m) for m in metadatas])

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        total = self.collection.count()
        if total == 0:
            return empty_query_result()
        return self.collection.query(query_texts=[query_text], n_results=min(n_results, total))

    def count(self) -> int:
        return self.collection.count()

    def ping(self) -> bool:
        try:
            self.collection.count()
            return True
        except Exception:
            return False

    def reset(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._create()


class TencentVectorDBBackend:
    """
    Tencent Cloud VectorDB (tcvectordb SDK). Uses the same 64-dim deterministic
    embeddings as the local backends so mock mode and live mode rank identically.
    """

    name = "tencent"
    META_FIELDS = ("match_id", "fact_type", "subject", "room", "timestamp", "confidence", "rule_id", "related_players", "source_event_seqs")

    def __init__(self, settings=None, embedding_fn: Optional[DeterministicOfflineEmbedding] = None, client: Any = None):
        self.settings = settings or config.memory.tencent
        self.embedding_fn = embedding_fn or DeterministicOfflineEmbedding(dim=config.memory.embedding_dim)
        self.dim = self.embedding_fn.dim
        if client is None:
            if not (self.settings.url and self.settings.key):
                raise RuntimeError("TENCENT_VDB_URL / TENCENT_VDB_KEY are not set")
            import tcvectordb

            client = tcvectordb.VectorDBClient(
                url=self.settings.url,
                username=self.settings.username,
                key=self.settings.key,
                timeout=self.settings.timeout_s,
            )
        self.client = client
        self.db = self.client.create_database_if_not_exists(self.settings.database)
        self.collection = self.db.create_collection_if_not_exists(
            name=self.settings.collection, shard=1, replicas=0, index=self._index()
        )

    def _index(self):
        from tcvectordb.model.enum import FieldType, IndexType, MetricType
        from tcvectordb.model.index import FilterIndex, HNSWParams, Index, VectorIndex

        return Index(
            FilterIndex(name="id", field_type=FieldType.String, index_type=IndexType.PRIMARY_KEY),
            VectorIndex(
                name="vector",
                dimension=self.dim,
                index_type=IndexType.HNSW,
                metric_type=MetricType.COSINE,
                params=HNSWParams(m=16, efconstruction=200),
            ),
            FilterIndex(name="match_id", field_type=FieldType.String, index_type=IndexType.FILTER),
        )

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        from tcvectordb.model.document import Document

        vectors = self.embedding_fn(documents)
        docs = []
        for doc_id, doc, meta, vec in zip(ids, documents, metadatas, vectors):
            fields = _flat_metadata(meta)
            fields["text"] = doc
            docs.append(Document(id=str(doc_id), vector=vec, **fields))
        self.collection.upsert(documents=docs)

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        vec = self.embedding_fn([query_text])[0]
        hits = self.collection.search(vectors=[vec], limit=max(1, n_results), retrieve_vector=False)
        rows = hits[0] if hits else []
        ids, documents, metadatas, distances = [], [], [], []
        for row in rows:
            row = dict(row)
            ids.append(str(row.pop("id", "")))
            score = float(row.pop("score", 0.0) or 0.0)
            documents.append(str(row.pop("text", "")))
            row.pop("vector", None)
            metadatas.append(row)
            distances.append(round(max(0.0, min(2.0, 1.0 - score)), 6))
        return {"ids": [ids], "documents": [documents], "metadatas": [metadatas], "distances": [distances]}

    def count(self) -> int:
        return int(self.collection.count() or 0)

    def ping(self) -> bool:
        try:
            self.count()
            return True
        except Exception as exc:
            logger.warning("Tencent VectorDB ping failed: %s", exc)
            return False

    def reset(self) -> None:
        self.db.truncate_collection(self.settings.collection)


class PineconeBackend:
    """
    Pinecone serverless (AeroCortex's primary store), used when Tencent isn't configured.

    The index may be shared with other apps, so this engine only ever touches its own
    namespace ("<prefix>-<id>"); a wipe deletes that namespace and nothing else.
    Serverless Pinecone is eventually consistent, so the backend tracks the ids it wrote:
    count() is exact, queries wait briefly for fresh writes, and vectors from a wiped
    match that are still being deleted server-side are never returned.
    """

    name = "pinecone"

    def __init__(self, settings=None, embedding_fn: Optional[DeterministicOfflineEmbedding] = None,
                 index: Any = None, namespace: Optional[str] = None):
        self.settings = settings or config.memory.pinecone
        self.embedding_fn = embedding_fn or DeterministicOfflineEmbedding(dim=config.memory.embedding_dim)
        self.dim = self.embedding_fn.dim
        self.namespace = namespace or f"{self.settings.namespace_prefix}-{uuid.uuid4().hex[:8]}"
        self.index = index if index is not None else self._connect()
        self._ids: set = set()
        atexit.register(self._drop_namespace_quietly)

    def _connect(self) -> Any:
        if not self.settings.api_key:
            raise RuntimeError("PINECONE_API_KEY is not set")
        from pinecone import Pinecone, ServerlessSpec

        pc = Pinecone(api_key=self.settings.api_key)
        if self.settings.index not in [i["name"] for i in pc.list_indexes()]:
            pc.create_index(
                name=self.settings.index, dimension=self.dim, metric="cosine",
                spec=ServerlessSpec(cloud=self.settings.cloud, region=self.settings.region),
            )
        desc = pc.describe_index(self.settings.index)
        if int(desc.dimension) != self.dim or str(desc.metric) != "cosine":
            raise RuntimeError(f"index {self.settings.index} is {desc.dimension}-dim {desc.metric}; need {self.dim}-dim cosine")
        return pc.Index(host=desc.host)

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        vectors = self.embedding_fn(documents)
        payload = [
            {"id": str(doc_id), "values": vec, "metadata": {**_flat_metadata(meta), "text": doc}}
            for doc_id, doc, meta, vec in zip(ids, documents, metadatas, vectors)
        ]
        self.index.upsert(vectors=payload, namespace=self.namespace)
        self._ids.update(str(i) for i in ids)

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        if not self._ids:
            return empty_query_result()
        vec = self.embedding_fn([query_text])[0]
        top_k = max(1, n_results)
        want = min(top_k, len(self._ids))
        deadline = time.monotonic() + self.settings.freshness_wait_s
        while True:
            res = self.index.query(vector=vec, top_k=top_k, namespace=self.namespace, include_metadata=True)
            matches = [m for m in _field(res, "matches") or [] if str(_field(m, "id")) in self._ids]
            if len(matches) >= want or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        ids, documents, metadatas, distances = [], [], [], []
        for m in matches:
            meta = dict(_field(m, "metadata") or {})
            ids.append(str(_field(m, "id")))
            documents.append(str(meta.pop("text", "")))
            metadatas.append(meta)
            distances.append(round(max(0.0, min(2.0, 1.0 - float(_field(m, "score") or 0.0))), 6))
        return {"ids": [ids], "documents": [documents], "metadatas": [metadatas], "distances": [distances]}

    def count(self) -> int:
        return len(self._ids)

    def server_count(self) -> int:
        stats = self.index.describe_index_stats()
        ns = (_field(stats, "namespaces") or {}).get(self.namespace)
        return int(_field(ns, "vector_count") or 0) if ns else 0

    def ping(self) -> bool:
        try:
            self.index.describe_index_stats()
            return True
        except Exception as exc:
            logger.warning("Pinecone ping failed: %s", exc)
            return False

    def reset(self) -> None:
        self._ids.clear()
        self._delete_namespace()

    def _delete_namespace(self) -> None:
        try:
            self.index.delete(delete_all=True, namespace=self.namespace)
        except Exception as exc:
            if "not found" not in str(exc).lower() and "404" not in str(exc):
                raise

    def _drop_namespace_quietly(self) -> None:
        try:
            self._delete_namespace()
        except Exception:
            pass


def _field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _build_local(embedding_fn: DeterministicOfflineEmbedding, collection_name: Optional[str]) -> VectorBackend:
    if config.memory.vector_backend in ("auto", "chroma", "tencent", "pinecone"):
        try:
            return ChromaBackend(collection_name=collection_name, embedding_fn=embedding_fn)
        except Exception as exc:
            if config.memory.vector_backend == "chroma":
                logger.warning("Chroma unavailable, using in-memory vector store: %s", exc)
    return InMemoryBackend(embedding_fn=embedding_fn)


class VectorStore:
    """
    Vector facade: Tencent VectorDB primary when configured, else Pinecone (as in
    AeroCortex), else local only (Chroma ephemeral, else pure in-memory). Writes are
    mirrored to the local backend while a remote primary is active, so a mid-match
    outage loses nothing.
    """

    def __init__(
        self,
        collection_name: Optional[str] = None,
        remote_backend: Optional[VectorBackend] = None,
        local_backend: Optional[VectorBackend] = None,
    ):
        self.embedding_fn = DeterministicOfflineEmbedding(dim=config.memory.embedding_dim)
        self._local: VectorBackend = local_backend or _build_local(self.embedding_fn, collection_name)
        self._remote: Optional[VectorBackend] = remote_backend
        mode = config.memory.vector_backend
        if self._remote is None and mode in ("auto", "tencent"):
            self._remote = self._build_tencent()
        if self._remote is None and mode in ("auto", "pinecone"):
            self._remote = self._build_pinecone()
        self._active: VectorBackend = self._local
        self.reconnect()

    def _build_pinecone(self) -> Optional[VectorBackend]:
        pc = config.memory.pinecone
        if not (pc.enabled and pc.api_key):
            return None
        try:
            backend = PineconeBackend(embedding_fn=self.embedding_fn)
            logger.info("Pinecone connected: index %s, namespace %s", pc.index, backend.namespace)
            return backend
        except Exception as exc:
            logger.warning("Pinecone init failed, using %s: %s", self._local.name, exc)
            return None

    def _build_tencent(self) -> Optional[VectorBackend]:
        tc = config.memory.tencent
        if not (tc.url and tc.key):
            return None
        try:
            backend = TencentVectorDBBackend(embedding_fn=self.embedding_fn)
            logger.info("Tencent VectorDB connected: %s/%s", tc.database, tc.collection)
            return backend
        except Exception as exc:
            logger.warning("Tencent VectorDB init failed, using %s: %s", self._local.name, exc)
            return None

    @property
    def engine(self) -> str:
        return self._active.name

    def reconnect(self) -> str:
        if self._remote is not None:
            try:
                if self._remote.ping():
                    self._active = self._remote
                    return self.engine
            except Exception as exc:
                logger.warning("Remote vector reconnect failed: %s", exc)
        self._active = self._local
        return self.engine

    def _failover(self, exc: Exception) -> None:
        if self._active is not self._local:
            logger.warning("%s failed, falling back to %s: %s", self._active.name, self._local.name, exc)
            self._active = self._local

    def add_documents(self, ids: List[str], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
        if self._active is not self._local:
            try:
                self._active.add_documents(ids, documents, metadatas)
            except Exception as exc:
                self._failover(exc)
        self._local.add_documents(ids, documents, metadatas)

    def query(self, query_text: str, n_results: int = 3) -> Dict[str, Any]:
        try:
            result = self._active.query(query_text, n_results=n_results)
        except Exception as exc:
            self._failover(exc)
            return self._local.query(query_text, n_results=n_results)
        if self._active is not self._local:
            expected = min(max(1, n_results), self._local.count())
            if len(result["ids"][0]) < expected:
                logger.info("%s not yet consistent (%d/%d hits); answering from local mirror",
                            self._active.name, len(result["ids"][0]), expected)
                return self._local.query(query_text, n_results=n_results)
        return result

    def count(self) -> int:
        try:
            return self._active.count()
        except Exception as exc:
            self._failover(exc)
            return self._local.count()

    def health(self) -> Dict[str, Any]:
        remote_status = "unset"
        if self._remote is not None:
            remote_status = "ok" if self._remote.ping() else "down"
        remote_name = self._remote.name if self._remote is not None else None
        return {
            "engine": self.engine,
            "remote": remote_name or "none",
            "remote_status": remote_status,
            "tencent": remote_status if remote_name == "tencent" else "unset",
            "pinecone": remote_status if remote_name == "pinecone" else "unset",
            "namespace": getattr(self._remote, "namespace", None),
            "local": self._local.name,
            "ok": self._local.ping() or remote_status == "ok",
        }

    def reset(self) -> None:
        """Wipe every backend, not just the active one."""
        if self._remote is not None:
            try:
                self._remote.reset()
            except Exception as exc:
                logger.warning("Remote vector reset failed: %s", exc)
        self._local.reset()
