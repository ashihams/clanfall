import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

import networkx as nx

from config import config
from models import ActionType, FactType, GameEvent, SemanticFact

logger = logging.getLogger("clanfall.knowledge_graph")

# How strongly each fact type implicates its subject. Confirmed alibis are
# exculpatory, so they rank low when the question is "who is suspicious".
FACT_TYPE_WEIGHT: Dict[FactType, float] = {
    FactType.ALIBI_CONTRADICTION: 1.0,
    FactType.LAST_SEEN_WITH: 0.85,
    FactType.PRESENCE_NEAR_BODY: 0.7,
    FactType.ALIBI_CONFIRMED: 0.3,
}


def player_node(name: str) -> str:
    return f"player:{name}"


def room_node(name: str) -> str:
    return f"room:{name}"


def event_node(seq: int) -> str:
    return f"event:{seq}"


def fact_node(fact_id: str) -> str:
    return f"fact:{fact_id}"


@dataclass
class GraphOps:
    """
    What one event or fact adds to the graph, shared by every backend so they stay
    structurally identical. Nodes are created only if missing (attrs set on create);
    an edge endpoint not listed in `nodes` is created bare. Edges with
    `requires_target` are skipped when the target does not already exist.
    """

    nodes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    edges: List[Tuple[str, str, Dict[str, Any]]] = field(default_factory=list)
    conditional_edges: List[Tuple[str, str, Dict[str, Any]]] = field(default_factory=list)
    flags: List[Tuple[str, str, Any]] = field(default_factory=list)

    def node(self, key: str, **attrs: Any) -> None:
        self.nodes.append((key, attrs))

    def edge(self, src: str, dst: str, relation: str, t: Optional[float] = None, requires_target: bool = False) -> None:
        attrs: Dict[str, Any] = {"relation": relation}
        if t is not None:
            attrs["t"] = t
        (self.conditional_edges if requires_target else self.edges).append((src, dst, attrs))


def event_ops(event: GameEvent) -> GraphOps:
    ops = GraphOps()
    if not event.is_public:
        return ops
    ev = event_node(event.seq)
    loc = room_node(event.location) if event.location else None
    if event.action == ActionType.MATCH_START:
        ops.node(loc, type="Room", name=event.location)
        for p in event.detail.get("players", []):
            ops.node(player_node(p), type="Player", name=p)
            ops.edge(player_node(p), loc, "WAS_IN", event.timestamp)
    elif event.action == ActionType.MOVE:
        ops.node(player_node(event.actor), type="Player", name=event.actor)
        ops.node(loc, type="Room", name=event.location)
        ops.edge(player_node(event.actor), loc, "WAS_IN", event.timestamp)
    elif event.action == ActionType.TASK:
        ops.node(loc, type="Room", name=event.location)
        ops.edge(player_node(event.actor), loc, "DID_TASK_IN", event.timestamp)
    elif event.action == ActionType.REPORT:
        ops.node(ev, type="BodyFound", name=f"{event.target}'s body", t=event.timestamp)
        ops.node(loc, type="Room", name=event.location)
        ops.node(player_node(event.target), type="Player", name=event.target)
        ops.edge(player_node(event.actor), ev, "REPORTED", event.timestamp)
        ops.edge(ev, loc, "FOUND_IN", event.timestamp)
        ops.edge(ev, player_node(event.target), "VICTIM", event.timestamp)
    elif event.action == ActionType.CLAIM:
        ops.node(loc, type="Room", name=event.location)
        ops.edge(player_node(event.actor), loc, "CLAIMED", event.claimed_at)
    elif event.action == ActionType.VOTE and event.target:
        ops.edge(player_node(event.actor), player_node(event.target), "VOTED_FOR", event.timestamp)
    elif event.action == ActionType.EJECT and event.target:
        ops.flags.append((player_node(event.target), "ejected", True))
    return ops


def fact_ops(fact: SemanticFact) -> GraphOps:
    ops = GraphOps()
    fn = fact_node(fact.fact_id)
    ops.node(fn, type="Fact", name=fact.fact_type.value, fact_type=fact.fact_type.value)
    ops.node(player_node(fact.subject), type="Player", name=fact.subject)
    ops.edge(fn, player_node(fact.subject), "ABOUT")
    if fact.room:
        ops.node(room_node(fact.room), type="Room", name=fact.room)
        ops.edge(fn, room_node(fact.room), "IN_ROOM")
    for p in fact.related_players:
        ops.node(player_node(p), type="Player", name=p)
        ops.edge(fn, player_node(p), "INVOLVES")
    for seq in fact.source_event_seqs:
        ops.edge(fn, event_node(seq), "DERIVED_FROM", requires_target=True)
    return ops


def proximity_score(fact_type: str, hops_to_focus: List[Optional[int]]) -> float:
    """fact-type weight x mean proximity 1/(1+hops); unreachable (or > 4 hops) counts as 5 hops."""
    type_w = FACT_TYPE_WEIGHT.get(FactType(fact_type), 0.5)
    if not hops_to_focus:
        return round(type_w * 0.5, 4)
    proximity = sum(1.0 / (1.0 + (h if h is not None and h <= 4 else 5)) for h in hops_to_focus) / len(hops_to_focus)
    return round(type_w * proximity, 4)


@runtime_checkable
class GraphBackend(Protocol):
    def add_event(self, event: GameEvent) -> None:
        ...

    def add_fact(self, fact: SemanticFact) -> None:
        ...

    def fact_relevance(self, fact_ids: Iterable[str], focus: Iterable[str]) -> List[Dict[str, Any]]:
        ...

    def get_summary(self) -> Dict[str, Any]:
        ...

    def reset(self) -> None:
        ...


class NetworkXBackend:
    """
    Embedded, in-memory typed multigraph: player <-> room <-> event <-> fact.
    Never persisted — AeroCortex saved its graph to JSON so it survived across
    missions; Clanfall's graph must not survive a match.
    """

    engine = "networkx"

    def __init__(self):
        self._lock = threading.Lock()
        self.g = nx.MultiDiGraph()

    def _node(self, node_id: str, **attrs) -> None:
        if not self.g.has_node(node_id):
            self.g.add_node(node_id, **attrs)

    def _apply(self, ops: GraphOps) -> None:
        with self._lock:
            for key, attrs in ops.nodes:
                self._node(key, **attrs)
            for src, dst, attrs in ops.edges:
                self.g.add_edge(src, dst, **attrs)
            for src, dst, attrs in ops.conditional_edges:
                if self.g.has_node(dst):
                    self.g.add_edge(src, dst, **attrs)
            for key, attr, value in ops.flags:
                if self.g.has_node(key):
                    self.g.nodes[key][attr] = value

    def add_event(self, event: GameEvent) -> None:
        self._apply(event_ops(event))

    def add_fact(self, fact: SemanticFact) -> None:
        self._apply(fact_ops(fact))

    def fact_relevance(self, fact_ids: Iterable[str], focus: Iterable[str]) -> List[Dict[str, Any]]:
        """
        graph_relevance = fact-type weight x mean proximity (1 / (1 + hops))
        from the fact node to each focus node (e.g. the body, its room, the victim).
        """
        with self._lock:
            undirected = self.g.to_undirected(as_view=True)
            focus_nodes = [n for n in focus if undirected.has_node(n)]
            results = []
            for fact_id in fact_ids:
                fn = fact_node(fact_id)
                if not undirected.has_node(fn):
                    continue
                if focus_nodes:
                    lengths = nx.single_source_shortest_path_length(undirected, fn, cutoff=4)
                    hops = [lengths.get(n) for n in focus_nodes]
                    path = self._path_labels(undirected, fn, focus_nodes[0])
                else:
                    hops, path = [], []
                results.append({
                    "key": fact_id,
                    "graph_relevance": proximity_score(self.g.nodes[fn]["fact_type"], hops),
                    "path": path,
                })
            results.sort(key=lambda r: r["graph_relevance"], reverse=True)
            return results

    def _path_labels(self, undirected, src: str, dst: str) -> List[str]:
        try:
            nodes = nx.shortest_path(undirected, src, dst)
        except nx.NetworkXNoPath:
            return []
        return [self.g.nodes[n].get("name", n) for n in nodes]

    def edges_for(self, player: str) -> List[Dict[str, Any]]:
        node = player_node(player)
        if not self.g.has_node(node):
            return []
        out = []
        for _, dst, data in self.g.out_edges(node, data=True):
            out.append({"relation": data.get("relation"), "to": self.g.nodes[dst].get("name", dst), "t": data.get("t")})
        return out

    def export(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "nodes": [{"id": n, **d} for n, d in self.g.nodes(data=True)],
                "edges": [{"source": u, "target": v, **d} for u, v, d in self.g.edges(data=True)],
            }

    def get_summary(self) -> Dict[str, Any]:
        return {
            "engine": "NetworkX (in-memory)",
            "engine_id": self.engine,
            "nodes_count": self.g.number_of_nodes(),
            "edges_count": self.g.number_of_edges(),
        }

    def ping(self) -> bool:
        return True

    def reset(self) -> None:
        with self._lock:
            self.g.clear()


def build_graph_backend() -> Optional[GraphBackend]:
    """Neo4j when configured and reachable (GRAPH_BACKEND=auto|neo4j), else None (NetworkX only)."""
    mode = config.memory.graph_backend
    nj = config.memory.neo4j
    if mode not in ("auto", "neo4j") or not (nj.enabled and nj.uri and nj.password):
        return None
    try:
        from memory.neo4j_graph import Neo4jBackend

        backend = Neo4jBackend()
        logger.info("Neo4j connected over %s, scope %s", backend.transport, backend.scope)
        return backend
    except Exception as exc:
        logger.warning("Neo4j init failed, using NetworkX: %s", exc)
        return None


class KnowledgeGraph:
    """
    Facade over the graph backend: Neo4j when configured (writes mirrored to the
    embedded NetworkX graph, which takes over if Neo4j fails mid-match), else
    NetworkX alone. reset() always wipes the remote too, even after a failover.
    """

    def __init__(self, backend: Optional[GraphBackend] = None, use_config: bool = True):
        self._nx = NetworkXBackend()
        self._remote: Optional[GraphBackend] = backend if backend is not None else (
            build_graph_backend() if use_config else None)
        if self._remote is self._nx:
            self._remote = None
        self._backend: GraphBackend = self._remote or self._nx

    @property
    def engine(self) -> str:
        return getattr(self._backend, "engine", "custom")

    def _fallback(self, exc: Exception, op: str) -> None:
        if self._backend is not self._nx:
            logger.warning("Graph backend %s failed (%s); falling back to NetworkX", op, exc)
            self._backend = self._nx

    def health(self) -> Dict[str, Any]:
        remote_status = "unset"
        if self._remote is not None:
            try:
                remote_status = "ok" if self._remote.ping() else "down"
            except Exception:
                remote_status = "down"
        return {
            "engine": self.engine,
            "remote": getattr(self._remote, "engine", "none") if self._remote is not None else "none",
            "remote_status": remote_status,
            "transport": getattr(self._remote, "transport", None),
            "scope": getattr(self._remote, "scope", None),
        }

    def add_event(self, event: GameEvent) -> None:
        try:
            self._backend.add_event(event)
        except Exception as exc:
            self._fallback(exc, "add_event")
        if self._backend is not self._nx:
            self._nx.add_event(event)

    def add_fact(self, fact: SemanticFact) -> None:
        try:
            self._backend.add_fact(fact)
        except Exception as exc:
            self._fallback(exc, "add_fact")
        if self._backend is not self._nx:
            self._nx.add_fact(fact)

    def fact_relevance(self, fact_ids: Iterable[str], focus: Iterable[str]) -> List[Dict[str, Any]]:
        fact_ids, focus = list(fact_ids), list(focus)
        try:
            return self._backend.fact_relevance(fact_ids, focus)
        except Exception as exc:
            self._fallback(exc, "fact_relevance")
            return self._nx.fact_relevance(fact_ids, focus)

    def edges_for(self, player: str) -> List[Dict[str, Any]]:
        return self._nx.edges_for(player)

    def export(self) -> Dict[str, Any]:
        return self._nx.export()

    def get_summary(self) -> Dict[str, Any]:
        try:
            return self._backend.get_summary()
        except Exception:
            return self._nx.get_summary()

    def reset(self) -> int:
        """Wipe both graphs; returns how many nodes are still left in the remote (0 = verified empty)."""
        left = 0
        if self._remote is not None:
            try:
                self._remote.reset()
            except Exception as exc:
                logger.warning("Graph backend reset failed: %s", exc)
            try:
                left = int(self._remote.get_summary()["nodes_count"])
            except Exception:
                left = -1
            try:
                self._backend = self._remote if self._remote.ping() else self._nx
            except Exception:
                self._backend = self._nx
        self._nx.reset()
        return left
