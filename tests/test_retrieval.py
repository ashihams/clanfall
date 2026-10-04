from agents.memory_agent import distance_to_similarity, fuse_hybrid_scores, minmax_normalize
from models import FactType


def test_minmax_and_similarity():
    assert minmax_normalize([]) == []
    assert minmax_normalize([2.0, 2.0]) == [1.0, 1.0]
    assert minmax_normalize([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]
    assert distance_to_similarity(0.25) == 0.75
    assert distance_to_similarity(1.7) == 0.0


def test_fusion_uses_point6_vector_point4_graph():
    vector = [{"key": "a", "distance": 0.1}, {"key": "b", "distance": 0.5}]
    graph = [{"key": "a", "graph_relevance": 0.1}, {"key": "b", "graph_relevance": 0.9}]
    fused = {f["key"]: f for f in fuse_hybrid_scores(vector, graph)}
    assert fused["a"]["final_score"] == 0.6  # vector 1.0, graph 0.0
    assert fused["b"]["final_score"] == 0.4  # vector 0.0, graph 1.0


def test_meeting_evidence_catches_the_liar(orchestrator):
    outcome = orchestrator.run_scenario("reactor_lie")
    voting = [e for e in outcome.passport.evidence if e["stage"] == "voting"]
    assert voting, "every meeting must surface at least one fact"
    top = voting[0]
    assert top["fact_type"] == FactType.ALIBI_CONTRADICTION.value
    assert top["subject"] == "Blue"
    assert top["final_score"] >= 0.5


def test_evidence_only_names_living_players(orchestrator):
    outcome = orchestrator.run_scenario("clean_getaway")
    for item in outcome.passport.evidence:
        assert item["subject"] != "Yellow"  # the victim


def test_every_meeting_surfaces_evidence(orchestrator):
    for key in ("reactor_lie", "clean_getaway"):
        outcome = orchestrator.run_scenario(key)
        meetings = outcome.passport.meeting_count
        assert meetings >= 1
        assert {e["meeting_id"] for e in outcome.passport.evidence if e.get("fact_id")} == set(range(1, meetings + 1))
