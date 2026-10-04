"""The core correctness property: nothing survives from one match to the next."""
import pytest

from memory.knowledge_graph import fact_node
from memory.match_memory import MatchMemory
from memory.vector_store import ChromaBackend, InMemoryBackend, VectorStore

ZERO_KEYS = ("episodic_events", "semantic_facts", "vector_documents", "graph_nodes", "graph_edges", "evidence_items")


def _assert_empty(stats):
    assert stats["match_id"] is None
    for key in ZERO_KEYS:
        assert stats[key] == 0, f"{key} survived the wipe: {stats}"


def test_end_of_match_wipes_every_layer(orchestrator):
    outcome = orchestrator.run_scenario("reactor_lie")
    assert outcome.wipe["before"]["semantic_facts"] > 0
    assert outcome.wipe["before"]["graph_nodes"] > 0
    assert outcome.wipe["wiped"] is True
    _assert_empty(orchestrator.memory.stats())
    assert orchestrator.memory.working.is_empty()


def test_second_match_retrieves_nothing_from_first(orchestrator):
    first = orchestrator.run_scenario("reactor_lie")
    first_fact_ids = {e["fact_id"] for e in first.passport.evidence if e.get("fact_id")}
    assert first_fact_ids

    probe = "Blue claimed to be in Reactor but the record places Blue in Electrical near Green's body"
    assert orchestrator.graph.memory_agent.retrieve(probe).retrieved_facts == []

    session = orchestrator.start_match("clean_getaway")
    assert session.pre_start_wipe is None, "memory should already have been empty"
    assert orchestrator.graph.memory_agent.retrieve(probe).retrieved_facts == []

    session.engine.run()
    second_match = session.match_id
    for fact in orchestrator.memory.semantic.all_facts():
        assert fact.match_id == second_match
    graph_nodes = {n["id"] for n in orchestrator.memory.graph.export()["nodes"]}
    assert not any(fact_node(fid) in graph_nodes for fid in first_fact_ids)
    retrieved = orchestrator.graph.memory_agent.retrieve(probe, top_k=20).retrieved_facts
    assert retrieved and all(r.fact.match_id == second_match for r in retrieved)

    second = orchestrator.end_match()
    assert second.passport.match_id != first.passport.match_id
    _assert_empty(orchestrator.memory.stats())


def test_reset_endpoint_path_wipes_mid_match(orchestrator):
    session = orchestrator.start_match("reactor_lie")
    for _ in range(15):
        session.engine.advance()
    assert orchestrator.memory.stats()["semantic_facts"] > 0
    wipe = orchestrator.abort_match()
    assert wipe["wiped"] is True
    assert orchestrator.session is None
    _assert_empty(orchestrator.memory.stats())


def test_dirty_memory_is_wiped_before_a_match_starts(orchestrator):
    session = orchestrator.start_match("reactor_lie")
    session.engine.run()
    orchestrator.session = None  # simulate a crash that skipped end_match()
    assert not orchestrator.memory.is_empty()
    session2 = orchestrator.start_match("clean_getaway")
    assert session2.pre_start_wipe is not None and session2.pre_start_wipe["wiped"] is True
    assert orchestrator.memory.stats()["episodic_events"] == 0


@pytest.mark.parametrize("backend_factory", [InMemoryBackend, ChromaBackend], ids=["memory", "chroma"])
def test_vector_backends_forget_on_reset(backend_factory):
    try:
        local = backend_factory()
    except Exception as exc:
        pytest.skip(f"backend unavailable: {exc}")
    store = VectorStore(local_backend=local)
    store.add_documents(["a", "b"], ["Blue in Electrical", "Red in MedBay"], [{"m": 1}, {"m": 1}])
    assert store.count() == 2
    store.reset()
    assert store.count() == 0
    assert store.query("Blue Electrical")["ids"] == [[]]


def test_remote_backend_is_wiped_too():
    class FakeRemote(InMemoryBackend):
        name = "tencent"

    remote, local = FakeRemote(), InMemoryBackend()
    memory = MatchMemory(vector_store=VectorStore(remote_backend=remote, local_backend=local))
    memory.vector_store.add_documents(["x"], ["Blue near the body"], [{}])
    assert remote.count() == 1 and local.count() == 1
    memory.reset()
    assert remote.count() == 0 and local.count() == 0
