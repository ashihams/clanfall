import atexit
import logging
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

from config import config
from memory.knowledge_graph import GraphOps, event_ops, fact_node, fact_ops, proximity_score
from memory.write_behind import WriteBehind
from models import GameEvent, SemanticFact

logger = logging.getLogger("clanfall.neo4j")

NODE_LABEL = "ClanfallNode"
TYPE_LABELS = {"Player", "Room", "BodyFound", "Fact"}
REL_TYPES = {
    "WAS_IN", "DID_TASK_IN", "REPORTED", "FOUND_IN", "VICTIM", "CLAIMED", "VOTED_FOR",
    "ABOUT", "IN_ROOM", "INVOLVES", "DERIVED_FROM",
}

RELEVANCE_QUERY = f"""
UNWIND $facts AS fk
MATCH (f:{NODE_LABEL} {{engine: $engine, key: fk}})
UNWIND $focus AS tk
MATCH (t:{NODE_LABEL} {{engine: $engine, key: tk}})
OPTIONAL MATCH p = shortestPath((f)-[*..15]-(t))
RETURN fk, tk, length(p) AS hops, [x IN nodes(p) | coalesce(x.name, x.key)] AS names
"""


def database_candidates(settings) -> List[str]:
    """
    The configured database first. Aura's credentials file names the database: older
    Free instances use "neo4j", newer ones use the instance id (the first label of the
    host), so that is tried next when the configured one does not exist.
    """
    names = [settings.database or "neo4j"]
    host = urlparse(settings.uri).hostname or ""
    if host.endswith(".databases.neo4j.io"):
        names.append(host.split(".", 1)[0])
    if "neo4j" not in names:
        names.append("neo4j")
    return list(dict.fromkeys(names))


def _is_missing_database(exc: Exception) -> bool:
    text = str(exc)
    return "DatabaseNotFound" in text or "database does not exist" in text.lower()


class BoltRunner:
    transport = "bolt"

    def __init__(self, settings):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(
            settings.uri, auth=(settings.user, settings.password),
            connection_timeout=settings.bolt_connect_timeout_s,
            connection_acquisition_timeout=settings.bolt_connect_timeout_s + 2,
            max_transaction_retry_time=settings.timeout_s,
        )
        try:
            self.driver.verify_connectivity()
            self.database = self._pick_database(settings)
        except Exception:
            self.driver.close()
            raise

    def _pick_database(self, settings) -> str:
        last: Optional[Exception] = None
        for name in database_candidates(settings):
            try:
                self.driver.execute_query("RETURN 1 AS ok", database_=name)
                return name
            except Exception as exc:
                if not _is_missing_database(exc):
                    raise
                last = exc
        raise last or RuntimeError("no Neo4j database found")

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        records, _, _ = self.driver.execute_query(cypher, params or {}, database_=self.database)
        return [r.data() for r in records]

    def close(self) -> None:
        self.driver.close()


class HttpRunner:
    """
    Neo4j Query API (POST /db/<db>/query/v2). Same Cypher as Bolt, but over HTTPS
    (Aura: port 443), for networks that block 7687. The API answers 202 even when a
    statement fails (only auth failures get 401), so errors are read from the body.
    Bookmarks are chained so every query reads its own previous writes.
    """

    transport = "http"

    def __init__(self, settings, client: Any = None):
        import httpx

        self.base = query_api_base(settings).rstrip("/")
        self.client = client or httpx.Client(auth=(settings.user, settings.password), timeout=settings.timeout_s)
        self._bookmarks: List[str] = []
        self._lock = threading.Lock()
        last: Optional[Exception] = None
        for name in database_candidates(settings):
            self.database = name
            self.url = f"{self.base}/db/{name}/query/v2"
            try:
                self.run("RETURN 1 AS ok")
                return
            except Exception as exc:
                if not _is_missing_database(exc):
                    raise
                last = exc
        raise last or RuntimeError("no Neo4j database found")

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # The Query API rejects literal line breaks in statements; Cypher treats spaces the same.
        body: Dict[str, Any] = {"statement": " ".join(cypher.split()), "parameters": params or {}}
        with self._lock:
            if self._bookmarks:
                body["bookmarks"] = list(self._bookmarks)
        resp = self.client.post(self.url, json=body, headers={"Accept": "application/json"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        errors = data.get("errors")
        payload = data.get("data")
        if resp.status_code >= 400 or errors or not isinstance(payload, dict):
            detail = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors or []) or resp.text[:200]
            raise RuntimeError(f"Neo4j Query API {resp.status_code} at {self.url}: {detail}")
        with self._lock:
            self._bookmarks = data.get("bookmarks") or self._bookmarks
        fields = payload.get("fields") or []
        return [dict(zip(fields, row)) for row in payload.get("values") or []]

    def close(self) -> None:
        self.client.close()


def query_api_base(settings) -> str:
    if settings.http_url:
        return settings.http_url
    parsed = urlparse(settings.uri)
    if parsed.scheme.endswith("+s") or parsed.scheme.endswith("+ssc"):
        return f"https://{parsed.hostname}"
    return f"http://{parsed.hostname}:7474"


def connect(settings) -> Any:
    transport = settings.transport
    if transport == "bolt":
        return BoltRunner(settings)
    if transport == "http":
        return HttpRunner(settings)
    try:
        return BoltRunner(settings)
    except Exception as exc:
        logger.info("Neo4j Bolt unreachable (%s); trying the HTTPS Query API", exc)
        return HttpRunner(settings)


def compile_ops(ops: GraphOps, known: Set[str]) -> Tuple[str, Dict[str, Any], Set[str], int]:
    """
    One Cypher statement for a GraphOps batch: MERGE nodes (attrs only on create),
    CREATE edges, SET flags. Returns (cypher, params, keys touched, edges created).
    Labels and relationship types come from fixed whitelists, never from input.
    """
    aliases: Dict[str, str] = {}
    merges: List[str] = []
    params: Dict[str, Any] = {}

    def alias(key: str, attrs: Optional[Dict[str, Any]] = None) -> str:
        if key in aliases:
            return aliases[key]
        a = f"n{len(aliases)}"
        aliases[key] = a
        params[f"{a}_key"] = key
        line = f"MERGE ({a}:{NODE_LABEL} {{engine: $engine, key: ${a}_key}})"
        if attrs:
            params[f"{a}_props"] = attrs
            label = attrs.get("type")
            sets = ([f"{a}:{label}"] if label in TYPE_LABELS else []) + [f"{a} += ${a}_props"]
            line += " ON CREATE SET " + ", ".join(sets)
        merges.append(line)
        return a

    for key, attrs in ops.nodes:
        alias(key, attrs)
    present = known | set(aliases)
    edges = list(ops.edges) + [e for e in ops.conditional_edges if e[1] in present]
    creates = []
    for i, (src, dst, attrs) in enumerate(edges):
        rel = attrs["relation"]
        if rel not in REL_TYPES:
            raise ValueError(f"unknown relation {rel}")
        a, b = alias(src), alias(dst)
        params[f"e{i}"] = attrs
        creates.append(f"CREATE ({a})-[:{rel} $e{i}]->({b})")
    sets = []
    for i, (key, attr, value) in enumerate(ops.flags):
        if key in present and attr.isidentifier():
            params[f"f{i}"] = value
            sets.append(f"SET {alias(key)}.{attr} = $f{i}")
    return "\n".join(merges + creates + sets), params, set(aliases), len(edges)


class Neo4jBackend:
    """
    Neo4j knowledge graph (AuraDB or self-hosted), same schema as the NetworkX graph.

    The database may be shared, so every node this engine writes carries the
    ClanfallNode label and engine=<scope>; reset() deletes exactly that scope and
    then counts it on the server to prove it is empty. Writes go through a serial
    write-behind queue; reads and wipes flush it first.
    """

    engine = "neo4j"

    def __init__(self, settings=None, runner: Any = None, scope: Optional[str] = None):
        self.settings = settings or config.memory.neo4j
        self.scope = scope or f"{self.settings.scope_prefix}-{uuid.uuid4().hex[:8]}"
        self._runner = runner if runner is not None else connect(self.settings)
        self.transport = getattr(self._runner, "transport", "custom")
        self._lock = threading.Lock()
        self._keys: Set[str] = set()
        self._fact_types: Dict[str, str] = {}
        self._edges = 0
        self._residual = 0
        self._writer = WriteBehind("neo4j")
        self._ensure_index()
        atexit.register(self._drop_quietly)

    def _ensure_index(self) -> None:
        try:
            self._runner.run(
                f"CREATE INDEX clanfall_node_scope IF NOT EXISTS FOR (n:{NODE_LABEL}) ON (n.engine, n.key)"
            )
        except Exception as exc:
            logger.info("Neo4j index not created (continuing without it): %s", exc)

    def _write(self, ops: GraphOps) -> None:
        with self._lock:
            cypher, params, touched, edge_count = compile_ops(ops, self._keys)
            if not cypher:
                return
            self._keys |= touched
            self._edges += edge_count
            for key, attrs in ops.nodes:
                if attrs.get("type") == "Fact":
                    self._fact_types.setdefault(key, attrs["fact_type"])
        params["engine"] = self.scope
        self._writer.submit(self._runner.run, cypher, params)

    def add_event(self, event: GameEvent) -> None:
        self._write(event_ops(event))

    def add_fact(self, fact: SemanticFact) -> None:
        self._write(fact_ops(fact))

    def fact_relevance(self, fact_ids: Iterable[str], focus: Iterable[str]) -> List[Dict[str, Any]]:
        self._writer.flush()
        with self._lock:
            facts = [(fid, fact_node(fid)) for fid in fact_ids if fact_node(fid) in self._fact_types]
            focus_nodes = [n for n in focus if n in self._keys]
        paths: Dict[Tuple[str, str], Tuple[Optional[int], List[str]]] = {}
        if facts and focus_nodes:
            rows = self._runner.run(RELEVANCE_QUERY, {
                "engine": self.scope, "facts": [fk for _, fk in facts], "focus": focus_nodes,
            })
            for row in rows:
                paths[(row["fk"], row["tk"])] = (row.get("hops"), row.get("names") or [])
        results = []
        for fid, fk in facts:
            hops = [paths.get((fk, t), (None, []))[0] for t in focus_nodes]
            path = paths.get((fk, focus_nodes[0]), (None, []))[1] if focus_nodes else []
            results.append({
                "key": fid,
                "graph_relevance": proximity_score(self._fact_types[fk], hops),
                "path": list(path),
            })
        results.sort(key=lambda r: r["graph_relevance"], reverse=True)
        return results

    def server_counts(self) -> Dict[str, int]:
        nodes = self._runner.run(
            f"MATCH (n:{NODE_LABEL} {{engine: $engine}}) RETURN count(n) AS c", {"engine": self.scope})
        edges = self._runner.run(
            f"MATCH (:{NODE_LABEL} {{engine: $engine}})-[r]->() RETURN count(r) AS c", {"engine": self.scope})
        return {"nodes": int(nodes[0]["c"]) if nodes else 0, "edges": int(edges[0]["c"]) if edges else 0}

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "engine": f"Neo4j ({self.transport})",
                "engine_id": self.engine,
                "scope": self.scope,
                "nodes_count": len(self._keys) + self._residual,
                "edges_count": self._edges,
            }

    def ping(self) -> bool:
        try:
            self._runner.run("RETURN 1 AS ok")
            return True
        except Exception as exc:
            logger.warning("Neo4j ping failed: %s", exc)
            return False

    def reset(self) -> None:
        try:
            self._writer.flush(timeout=30)
        except Exception as exc:
            logger.warning("Neo4j had a failed write before the wipe (wiping anyway): %s", exc)
            self._writer.discard_pending_error()
        with self._lock:
            pending = len(self._keys) + self._residual
        try:
            self._runner.run(f"MATCH (n:{NODE_LABEL} {{engine: $engine}}) DETACH DELETE n", {"engine": self.scope})
            left = self.server_counts()["nodes"]
        except Exception:
            with self._lock:
                self._residual = pending
                self._keys.clear()
                self._fact_types.clear()
                self._edges = 0
            raise
        with self._lock:
            self._keys.clear()
            self._fact_types.clear()
            self._edges = 0
            self._residual = left
        if left:
            raise RuntimeError(f"{left} Clanfall nodes still in Neo4j scope {self.scope} after wipe")

    def _drop_quietly(self) -> None:
        try:
            self._writer.flush(timeout=5)
        except Exception:
            pass
        try:
            self._runner.run(f"MATCH (n:{NODE_LABEL} {{engine: $engine}}) DETACH DELETE n", {"engine": self.scope})
        except Exception:
            pass
        try:
            self._runner.close()
        except Exception:
            pass
