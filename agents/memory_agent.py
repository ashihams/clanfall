import time
from typing import Any, Dict, List, Optional, Tuple

from config import config
from memory.knowledge_graph import event_node, player_node, room_node
from memory.match_memory import MatchMemory
from models import HybridMemoryContext, RetrievedFact


def minmax_normalize(values: List[float]) -> List[float]:
    """Scale a score list to [0, 1] across the candidate set."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def distance_to_similarity(distance: float) -> float:
    """Cosine distance -> similarity in [0, 1]."""
    return max(0.0, min(1.0, 1.0 - float(distance)))


def fuse_hybrid_scores(
    vector_items: List[Dict[str, Any]],
    graph_items: List[Dict[str, Any]],
    w_vector: float = 0.6,
    w_graph: float = 0.4,
) -> List[Dict[str, Any]]:
    """
    Convert distance -> similarity, min-max normalize each list, then fuse.

    vector_items: [{key, distance? | vector_similarity, ...}]
    graph_items:  [{key, graph_relevance}]
    """
    kg_map = {item["key"]: float(item["graph_relevance"]) for item in graph_items}
    vec_raw: List[float] = []
    for item in vector_items:
        if item.get("distance") is not None:
            vec_raw.append(distance_to_similarity(item["distance"]))
        else:
            vec_raw.append(float(item.get("vector_similarity", 0.0)))
    graph_raw = [kg_map.get(item.get("key"), 0.0) for item in vector_items]
    vec_norm = minmax_normalize(vec_raw)
    graph_norm = minmax_normalize(graph_raw)

    fused: List[Dict[str, Any]] = []
    for i, item in enumerate(vector_items):
        vs, gr = vec_norm[i], graph_norm[i]
        fused.append({
            **item,
            "vector_similarity": round(vs, 4),
            "graph_relevance": round(gr, 4),
            "final_score": round(w_vector * vs + w_graph * gr, 4),
        })
    fused.sort(key=lambda x: x["final_score"], reverse=True)
    return fused


class MemoryAgent:
    """
    Memory Agent:
    Hybrid retrieval over the live match's memory — vector similarity over
    semantic facts (Tencent VectorDB / local fallback) fused with knowledge-graph
    proximity to the incident under discussion (0.6 x vector + 0.4 x graph).
    """

    def __init__(self, memory: MatchMemory):
        self.memory = memory
        self.w_vector = config.memory.hybrid_weights.vector_similarity
        self.w_graph = config.memory.hybrid_weights.graph_relevance
        self.top_k = config.memory.top_k_facts
        self.last_context: Optional[HybridMemoryContext] = None

    def evidence_query(self, stage: str) -> Tuple[str, List[str]]:
        body = self.memory.working.last_body
        if not body:
            return "Whose story contradicts the record? suspicious alibi contradiction", []
        victim, room = body["victim"], body["room"]
        focus = [event_node(body["event_seq"]), room_node(room), player_node(victim)]
        if stage == "voting":
            query = (
                f"Whose alibi contradicts the record about {victim}'s death in {room}? "
                f"Who was in {room} near {victim}'s body?"
            )
        else:
            query = f"Who was in {room} right before {victim}'s body was found? Who was {victim} last seen with?"
        return query, focus

    def retrieve(
        self,
        query_text: str,
        focus: Optional[List[str]] = None,
        top_k: Optional[int] = None,
        alive_only: bool = False,
    ) -> HybridMemoryContext:
        start = time.perf_counter()
        sem = self.memory.semantic
        candidates = sem.search(query_text, top_k=max(sem.count(), 1), match_id=self.memory.episodic.match_id)
        if alive_only:
            alive = set(self.memory.working.alive)
            candidates = [(f, d) for f, d in candidates if f.subject in alive]

        vector_items = [{"key": f.fact_id, "distance": d, "fact": f} for f, d in candidates]
        graph_items = self.memory.graph.fact_relevance([f.fact_id for f, _ in candidates], focus or [])
        fused = fuse_hybrid_scores(vector_items, graph_items, self.w_vector, self.w_graph)

        k = top_k or self.top_k
        context = HybridMemoryContext(
            query=query_text,
            retrieved_facts=[
                RetrievedFact(
                    fact=item["fact"],
                    vector_similarity=item["vector_similarity"],
                    graph_relevance=item["graph_relevance"],
                    final_score=item["final_score"],
                )
                for item in fused[:k]
            ],
            graph_paths=[g for g in graph_items[:k]],
            retrieval_latency_ms=round((time.perf_counter() - start) * 1000, 2),
            vector_engine=self.memory.vector_store.engine,
        )
        self.last_context = context
        return context

    def retrieve_for_stage(self, stage: str) -> HybridMemoryContext:
        query, focus = self.evidence_query(stage)
        return self.retrieve(query, focus=focus, alive_only=True)
