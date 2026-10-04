from typing import Any, Dict, List, Optional

from memory.document_store import MongoMemoryStore, build_memory_store
from memory.episodic_memory import EpisodicMemory
from memory.knowledge_graph import KnowledgeGraph
from memory.semantic_memory import SemanticMemory
from memory.vector_store import VectorStore
from memory.working_memory import WorkingMemory
from models import GameEvent, MeetingEvidence, SemanticFact

_FROM_CONFIG = object()


class MatchMemory:
    """
    All memory layers for exactly one match. reset() is the single wipe point —
    the API's /reset and the end-of-match flow both go through it. Remote stores
    (Pinecone vectors, Neo4j graph, MongoDB documents) are wiped here too, and the
    remote wipes are verified with server-side counts.
    """

    def __init__(
        self,
        working: Optional[WorkingMemory] = None,
        episodic: Optional[EpisodicMemory] = None,
        semantic: Optional[SemanticMemory] = None,
        graph: Optional[KnowledgeGraph] = None,
        vector_store: Optional[VectorStore] = None,
        store: Any = _FROM_CONFIG,
    ):
        self.working = working or WorkingMemory()
        self.episodic = episodic or EpisodicMemory()
        self.semantic = semantic or SemanticMemory(vector_store=vector_store)
        self.graph = graph or KnowledgeGraph()
        self.store: Optional[MongoMemoryStore] = build_memory_store() if store is _FROM_CONFIG else store

    @property
    def vector_store(self) -> VectorStore:
        return self.semantic.vector_store

    # Write-through to the document store; in-process layers stay the read path.

    def record_event(self, event: GameEvent) -> None:
        if self.store is not None:
            self.store.record_event(event)

    def record_facts(self, facts: List[SemanticFact]) -> None:
        if self.store is not None:
            for fact in facts:
                self.store.record_fact(fact)

    def record_evidence(self, evidence: MeetingEvidence) -> None:
        if self.store is not None:
            self.store.record_evidence(evidence, self.episodic.match_id)

    def stats(self) -> Dict[str, Any]:
        kg = self.graph.get_summary()
        return {
            "match_id": self.episodic.match_id,
            "working_events": self.working.event_count,
            "episodic_events": len(self.episodic),
            "semantic_facts": self.semantic.count(),
            "vector_documents": self.vector_store.count(),
            "graph_nodes": kg["nodes_count"],
            "graph_edges": kg["edges_count"],
            "evidence_items": len(self.working.evidence),
            "stored_documents": self.store.count() if self.store is not None else 0,
        }

    def is_empty(self) -> bool:
        s = self.stats()
        return s["match_id"] is None and all(
            s[k] == 0 for k in (
                "working_events", "episodic_events", "semantic_facts", "vector_documents",
                "graph_nodes", "graph_edges", "evidence_items", "stored_documents",
            )
        )

    def health(self) -> Dict[str, Any]:
        return {
            "vector_store": self.vector_store.health(),
            "graph": self.graph.health(),
            "document_store": self.store.health() if self.store is not None else {"status": "unset"},
        }

    def reset(self) -> Dict[str, Any]:
        before = self.stats()
        self.working.clear()
        self.episodic.clear()
        self.semantic.clear()
        graph_left = self.graph.reset()
        store_left = self.store.reset() if self.store is not None else 0
        after = self.stats()
        return {
            "before": before,
            "after": after,
            "wiped": self.is_empty() and graph_left == 0 and store_left == 0,
            "remote_left": {"graph_nodes": graph_left, "stored_documents": store_left},
        }
