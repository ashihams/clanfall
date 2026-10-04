"""
Neo4j knowledge graph + MongoDB memory store.

Offline tests use mongomock and fake Neo4j runners. The live tests run the full
match pipeline against real servers when NEO4J_TEST_URI / MONGO_TEST_URI are set
(e.g. local Docker containers) and check that the wipe leaves nothing behind.
"""
import os

import pytest

from chain.ledger import MockLedger
from config.settings import MongoConfig, Neo4jConfig
from memory.document_store import COLLECTIONS, MongoMemoryStore
from memory.knowledge_graph import KnowledgeGraph, NetworkXBackend, event_ops, fact_ops
from memory.match_memory import MatchMemory
from memory.neo4j_graph import Neo4jBackend, compile_ops, query_api_base
from orchestration.graph import ClanfallGraph
from orchestration.match_runner import MatchOrchestrator

NEO4J_TEST_URI = os.getenv("NEO4J_TEST_URI")
MONGO_TEST_URI = os.getenv("MONGO_TEST_URI")


class DeadRunner:
    transport = "fake"

    def __init__(self):
        self.calls = 0

    def run(self, cypher, params=None):
        self.calls += 1
        if self.calls > 1:  # let the index creation through, fail everything after
            raise ConnectionError("neo4j down")
        return []

    def close(self):
        pass


def _orchestrator(tmp_path, graph=None, store=None):
    memory = MatchMemory(graph=graph or KnowledgeGraph(use_config=False), store=store)
    return MatchOrchestrator(graph=ClanfallGraph(memory=memory), ledger=MockLedger(), export_dir=tmp_path / "p")


# --------------------------------------------------------------------- Cypher compilation

def test_compiled_cypher_is_scoped_and_whitelisted(orchestrator):
    session = orchestrator.start_match("reactor_lie")
    session.engine.run()
    events = orchestrator.memory.episodic.events(public_only=True)
    facts = orchestrator.memory.semantic.all_facts()
    known: set = set()
    for ops in [event_ops(e) for e in events] + [fact_ops(f) for f in facts]:
        cypher, params, touched, _ = compile_ops(ops, known)
        known |= touched
        for line in filter(None, cypher.splitlines()):
            assert line.startswith(("MERGE (", "CREATE (", "SET ")), line
            if line.startswith("MERGE"):
                assert ":ClanfallNode {engine: $engine, key: $" in line
        assert all(isinstance(k, str) for k in params)
    orchestrator.abort_match()


def test_query_api_url_is_derived_from_the_uri():
    assert query_api_base(Neo4jConfig(uri="neo4j+s://abc.databases.neo4j.io")) == "https://abc.databases.neo4j.io"
    assert query_api_base(Neo4jConfig(uri="bolt://localhost:7687")) == "http://localhost:7474"
    assert query_api_base(Neo4jConfig(uri="neo4j://x", http_url="https://proxy")) == "https://proxy"


# --------------------------------------------------------------------- Query API (HTTPS) runner

def _query_api(handler):
    import httpx

    return httpx.Client(transport=httpx.MockTransport(handler), auth=("neo4j", "pw"))


def test_http_runner_speaks_the_query_api_and_chains_bookmarks():
    import json as _json

    seen = []

    def handler(request):
        body = _json.loads(request.content)
        seen.append((request.url.path, body))
        if "/db/neo4j/" in request.url.path:   # newer Aura: database is named after the instance id
            return _resp(202, {"errors": [{"code": "Neo.ClientError.Database.DatabaseNotFound",
                                           "message": "Database does not exist. Database name: 'neo4j'."}]})
        if body["statement"].startswith("BAD"):
            return _resp(202, {"errors": [{"code": "Neo.ClientError.Statement.SyntaxError", "message": "nope"}]})
        return _resp(202, {"data": {"fields": ["hops", "names"], "values": [[2, ["a", "b"]]]},
                           "bookmarks": [f"FB:{len(seen)}"]})

    settings = Neo4jConfig(uri="neo4j+s://abcd1234.databases.neo4j.io", password="pw", database="neo4j")
    from memory.neo4j_graph import HttpRunner

    runner = HttpRunner(settings, client=_query_api(handler))
    assert runner.database == "abcd1234"
    assert runner.url == "https://abcd1234.databases.neo4j.io/db/abcd1234/query/v2"
    rows = runner.run("MATCH (n)\nRETURN n", {"x": 1})
    assert rows == [{"hops": 2, "names": ["a", "b"]}]
    path, body = seen[-1]
    assert "\n" not in body["statement"] and body["parameters"] == {"x": 1}
    assert body["bookmarks"] == ["FB:2"]                      # read-your-writes across requests
    with pytest.raises(RuntimeError, match="SyntaxError"):
        runner.run("BAD")


def test_http_runner_rejects_auth_failures_and_non_api_responses():
    from memory.neo4j_graph import HttpRunner

    settings = Neo4jConfig(uri="neo4j+s://abcd1234.databases.neo4j.io", password="wrong")
    unauthorized = lambda r: _resp(401, {"errors": [{"code": "Neo.ClientError.Security.Unauthorized",
                                                     "message": "Invalid username or password."}]})
    with pytest.raises(RuntimeError, match="Unauthorized"):
        HttpRunner(settings, client=_query_api(unauthorized))
    html = lambda r: __import__("httpx").Response(200, text="<html>captive portal</html>")
    with pytest.raises(RuntimeError, match="200"):
        HttpRunner(settings, client=_query_api(html))


def _resp(status, payload):
    import httpx

    return httpx.Response(status, json=payload)


# --------------------------------------------------------------------- Neo4j failover

def test_neo4j_outage_falls_back_to_networkx_and_reports_unverified_wipe(tmp_path):
    backend = Neo4jBackend(settings=Neo4jConfig(), runner=DeadRunner(), scope="clanfall-test")
    kg = KnowledgeGraph(backend=backend)
    assert kg.engine == "neo4j"
    orch = _orchestrator(tmp_path, graph=kg)
    session = orch.start_match("reactor_lie")
    session.engine.run()
    assert kg.engine == "networkx"                      # failed over mid-match, nothing lost
    assert orch.memory.stats()["semantic_facts"] > 0
    wipe = orch.abort_match()
    assert wipe["wiped"] is False                       # Neo4j could not be verified empty
    assert wipe["remote_left"]["graph_nodes"] != 0


# --------------------------------------------------------------------- MongoDB store (mongomock)

def _mock_store(client=None, scope="clanfall-test"):
    mongomock = pytest.importorskip("mongomock")
    return MongoMemoryStore(settings=MongoConfig(database="clanfall"), client=client or mongomock.MongoClient(), scope=scope)


def test_mongo_store_records_the_match_and_wipe_is_verified(tmp_path):
    store = _mock_store()
    orch = _orchestrator(tmp_path, store=store)
    outcome = orch.run_scenario("reactor_lie")
    before = outcome.wipe["before"]
    assert before["stored_documents"] == (
        before["episodic_events"] + before["semantic_facts"] + before["evidence_items"])
    assert outcome.wipe["wiped"] is True
    assert outcome.wipe["remote_left"] == {"graph_nodes": 0, "stored_documents": 0}
    assert store.server_count() == 0 and store.count() == 0


def test_mongo_wipe_never_touches_other_data():
    mongomock = pytest.importorskip("mongomock")
    client = mongomock.MongoClient()
    client["clanfall"]["semantic_facts"].insert_one({"_id": "someone-else", "engine": "other-app"})
    client["aerocortex"]["episodes"].insert_one({"_id": "aero-1"})
    store = _mock_store(client)
    orch_store_doc = {"fact_id": "f1"}
    store._put("semantic_facts", "f1", orch_store_doc)
    store.flush()
    assert store.server_count() == 1
    assert store.reset() == 0
    assert client["clanfall"]["semantic_facts"].count_documents({"engine": "other-app"}) == 1
    assert client["aerocortex"]["episodes"].count_documents({}) == 1


def test_mongo_outage_keeps_the_match_running_but_flags_the_wipe(tmp_path):
    store = _mock_store()

    def broken(*_):
        raise ConnectionError("mongo down")

    store._upsert = broken
    orch = _orchestrator(tmp_path, store=store)
    session = orch.start_match("reactor_lie")
    session.engine.run()
    assert orch.memory.stats()["semantic_facts"] > 0  # in-process memory unaffected
    store.flush = lambda timeout=None: None
    assert store.health()["status"] == "down"
    store.db = None                                   # server unreachable for the wipe too
    wipe = orch.abort_match()
    assert wipe["wiped"] is False and wipe["remote_left"]["stored_documents"] > 0


# --------------------------------------------------------------------- live servers

@pytest.mark.skipif(not NEO4J_TEST_URI, reason="set NEO4J_TEST_URI (+ NEO4J_TEST_PASSWORD) to run against Neo4j")
@pytest.mark.parametrize("transport", ["bolt", "http"])
def test_live_neo4j_matches_networkx_and_wipes_its_scope(tmp_path, transport):
    settings = Neo4jConfig(
        uri=NEO4J_TEST_URI, user=os.getenv("NEO4J_TEST_USER", "neo4j"),
        password=os.getenv("NEO4J_TEST_PASSWORD", ""), transport=transport,
        http_url=os.getenv("NEO4J_TEST_HTTP_URL", ""),
    )
    try:
        backend = Neo4jBackend(settings=settings)
    except Exception as exc:
        pytest.skip(f"Neo4j {transport} unavailable: {exc}")
    runner = backend._runner
    runner.run("CREATE (:AeroCortexProbe {keep: true})")
    kg = KnowledgeGraph(backend=backend)
    orch = _orchestrator(tmp_path, graph=kg)
    session = orch.start_match("reactor_lie")
    session.engine.run()
    assert kg.engine == "neo4j"

    nx_ref: NetworkXBackend = kg._nx
    assert backend.server_counts() == {"nodes": nx_ref.g.number_of_nodes(), "edges": nx_ref.g.number_of_edges()}
    fact_ids = [f.fact_id for f in orch.memory.semantic.all_facts()]
    report = next(e for e in orch.memory.episodic.events(public_only=True) if e.action.value == "report")
    focus = [f"event:{report.seq}", f"room:{report.location}", f"player:{report.target}"]
    remote = {r["key"]: r["graph_relevance"] for r in backend.fact_relevance(fact_ids, focus)}
    local = {r["key"]: r["graph_relevance"] for r in nx_ref.fact_relevance(fact_ids, focus)}
    assert remote == local

    outcome = orch.end_match()
    assert outcome.wipe["wiped"] is True
    assert backend.server_counts() == {"nodes": 0, "edges": 0}
    assert runner.run("MATCH (n:AeroCortexProbe) RETURN count(n) AS c")[0]["c"] >= 1
    runner.run("MATCH (n:AeroCortexProbe) DELETE n")


@pytest.mark.skipif(not MONGO_TEST_URI, reason="set MONGO_TEST_URI to run against MongoDB")
def test_live_mongo_store_wipes_its_scope(tmp_path):
    try:
        store = MongoMemoryStore(settings=MongoConfig(uri=MONGO_TEST_URI, database="clanfall_test", timeout_ms=4000))
    except Exception as exc:
        pytest.skip(f"MongoDB unavailable: {exc}")
    store.db["semantic_facts"].insert_one({"_id": "keep-me", "engine": "other-app"})
    orch = _orchestrator(tmp_path, store=store)
    outcome = orch.run_scenario("reactor_lie")
    assert outcome.wipe["before"]["stored_documents"] > 0
    assert outcome.wipe["wiped"] is True
    assert store.server_count() == 0
    assert store.db["semantic_facts"].count_documents({"engine": "other-app"}) == 1
    for name in COLLECTIONS:
        store.db[name].drop()
