import atexit
import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional

from config import config
from memory.write_behind import WriteBehind
from models import GameEvent, SemanticFact

logger = logging.getLogger("clanfall.document_store")

COLLECTIONS = ("episodic_events", "semantic_facts", "evidence")


class MongoMemoryStore:
    """
    MongoDB document store for the match's memory: every episodic event, semantic
    fact and evidence item is written here as it happens (write-behind, so the
    pipeline never waits on the network). In-process memory stays the read path.

    The cluster may be shared, so every document carries engine=<scope> and lives in
    the Clanfall database; reset() deletes exactly that scope and then counts it on
    the server, so the wipe is verified rather than assumed.
    """

    name = "mongodb"

    def __init__(self, settings=None, client: Any = None, scope: Optional[str] = None):
        self.settings = settings or config.memory.mongo
        self.scope = scope or f"{self.settings.scope_prefix}-{uuid.uuid4().hex[:8]}"
        if client is None:
            if not self.settings.uri:
                raise RuntimeError("MONGO_URI is not set")
            from pymongo import MongoClient

            client = MongoClient(
                self.settings.uri, appname="clanfall",
                serverSelectionTimeoutMS=self.settings.timeout_ms,
                connectTimeoutMS=self.settings.timeout_ms,
            )
        self.client = client
        self.db = client[self.settings.database]
        self.client.admin.command("ping")
        self._lock = threading.Lock()
        self._ids: Dict[str, set] = {c: set() for c in COLLECTIONS}
        self._residual = 0
        self._evidence_seq = 0
        self._status = "ok"
        self._writer = WriteBehind("mongo")
        for name in COLLECTIONS:
            try:
                self.db[name].create_index([("engine", 1), ("match_id", 1)])
            except Exception as exc:
                logger.info("Mongo index on %s not created: %s", name, exc)
        atexit.register(self._drop_quietly)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _put(self, collection: str, doc_id: str, doc: Dict[str, Any]) -> None:
        doc = {**doc, "_id": f"{self.scope}:{doc_id}", "engine": self.scope, "stored_at": time.time()}
        with self._lock:
            self._ids[collection].add(doc["_id"])
        try:
            self._writer.submit(self._upsert, collection, doc)
        except Exception as exc:
            self._mark_down(exc)

    def _upsert(self, collection: str, doc: Dict[str, Any]) -> None:
        self.db[collection].replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def _mark_down(self, exc: Exception) -> None:
        if self._status != "down":
            logger.warning("MongoDB write failed; memory keeps running in-process: %s", exc)
        self._status = "down"

    def record_event(self, event: GameEvent) -> None:
        self._put("episodic_events", f"{event.match_id}:{event.seq}", event.model_dump(mode="json"))

    def record_fact(self, fact: SemanticFact) -> None:
        self._put("semantic_facts", fact.fact_id, fact.model_dump(mode="json"))

    def record_evidence(self, evidence: Any, match_id: Optional[str]) -> None:
        with self._lock:
            self._evidence_seq += 1
            seq = self._evidence_seq
        doc = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else dict(evidence)
        self._put("evidence", f"{match_id}:{seq}", {**doc, "match_id": match_id})

    # ------------------------------------------------------------------
    # Reads / health
    # ------------------------------------------------------------------

    def count(self) -> int:
        with self._lock:
            return sum(len(ids) for ids in self._ids.values()) + self._residual

    def server_count(self) -> int:
        return sum(self.db[c].count_documents({"engine": self.scope}) for c in COLLECTIONS)

    def flush(self, timeout: Optional[float] = None) -> None:
        self._writer.flush(timeout=timeout)

    def ping(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except Exception as exc:
            logger.warning("MongoDB ping failed: %s", exc)
            return False

    def health(self) -> Dict[str, Any]:
        reachable = self.ping()
        return {
            "status": "ok" if reachable and self._status == "ok" else "down",
            "database": self.settings.database,
            "scope": self.scope,
            "documents": self.count(),
        }

    # ------------------------------------------------------------------
    # Wipe
    # ------------------------------------------------------------------

    def reset(self) -> int:
        """Delete this scope's documents; returns how many are verifiably left on the server."""
        try:
            self._writer.flush(timeout=30)
        except Exception as exc:
            logger.warning("MongoDB had a failed write before the wipe (wiping anyway): %s", exc)
            self._writer.discard_pending_error()
        pending = self.count()
        try:
            for name in COLLECTIONS:
                self.db[name].delete_many({"engine": self.scope})
            left = self.server_count()
        except Exception as exc:
            logger.warning("MongoDB wipe failed: %s", exc)
            left = pending or 1
        with self._lock:
            for ids in self._ids.values():
                ids.clear()
            self._evidence_seq = 0
            self._residual = left
        if left == 0 and self.ping():
            self._status = "ok"
        return left

    def _drop_quietly(self) -> None:
        try:
            self._writer.flush(timeout=5)
        except Exception:
            pass
        try:
            for name in COLLECTIONS:
                self.db[name].delete_many({"engine": self.scope})
        except Exception:
            pass


def build_memory_store() -> Optional[MongoMemoryStore]:
    mg = config.memory.mongo
    if not (mg.enabled and mg.uri):
        return None
    try:
        store = MongoMemoryStore()
        logger.info("MongoDB connected: database %s, scope %s", mg.database, store.scope)
        return store
    except Exception as exc:
        logger.warning("MongoDB init failed, memory stays in-process only: %s", exc)
        return None
