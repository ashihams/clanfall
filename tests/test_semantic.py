from agents.situation_agent import SituationAgent
from memory.episodic_memory import EpisodicMemory, EpisodicMemoryError
from memory.semantic_memory import SemanticMemory
from memory.vector_store import InMemoryBackend, VectorStore
from models import ActionType, FactType, GameEvent, SignalType

import pytest

M = "match-under-test"


def ev(t, action, **kw):
    return GameEvent(timestamp=t, match_id=M, action=action, **kw)


@pytest.fixture
def world():
    epi = EpisodicMemory()
    sem = SemanticMemory(vector_store=VectorStore(local_backend=InMemoryBackend()))
    script = [
        ev(0, ActionType.MATCH_START, location="Cafeteria", detail={"players": ["Red", "Blue", "Green", "Yellow"]}),
        ev(5, ActionType.MOVE, actor="Blue", location="Storage"),
        ev(5, ActionType.MOVE, actor="Green", location="Storage"),
        ev(5, ActionType.MOVE, actor="Yellow", location="Storage"),
        ev(10, ActionType.MOVE, actor="Green", location="Electrical"),
        ev(18, ActionType.MOVE, actor="Blue", location="Electrical"),
        ev(22, ActionType.KILL, actor="Blue", target="Green", location="Electrical"),
        ev(26, ActionType.MOVE, actor="Blue", location="Storage"),
        ev(33, ActionType.MOVE, actor="Yellow", location="Electrical"),
    ]
    stored = [epi.append(e) for e in script]
    return epi, sem, stored


def test_hidden_events_never_produce_facts(world):
    epi, sem, stored = world
    kill = stored[6]
    assert not kill.is_public
    assert sem.derive(kill, epi) == []
    assert SituationAgent(epi).assess(kill).signal_type == SignalType.NONE


def test_report_derives_presence_and_last_seen(world):
    epi, sem, _ = world
    report = epi.append(ev(34, ActionType.REPORT, actor="Yellow", target="Green", location="Electrical"))
    facts = sem.derive(report, epi)
    by_type = {f.fact_type: f for f in facts}
    assert by_type[FactType.PRESENCE_NEAR_BODY].subject == "Blue"
    assert by_type[FactType.LAST_SEEN_WITH].subject == "Blue"
    # the reporter's own arrival and the victim are excluded
    assert all(f.subject not in ("Yellow", "Green") for f in facts)


def test_claim_contradiction_and_confirmation(world):
    epi, sem, _ = world
    epi.append(ev(34, ActionType.REPORT, actor="Yellow", target="Green", location="Electrical"))
    lie = epi.append(ev(36, ActionType.CLAIM, actor="Blue", location="Reactor", claimed_at=22, meeting_id=1))
    truth = epi.append(ev(37, ActionType.CLAIM, actor="Yellow", location="Storage", claimed_at=22, meeting_id=1))

    [contradiction] = sem.derive(lie, epi)
    assert contradiction.fact_type == FactType.ALIBI_CONTRADICTION
    assert contradiction.room == "Electrical"
    assert SituationAgent(epi).assess(lie).signal_type == SignalType.ALIBI_CONTRADICTION

    [confirmed] = sem.derive(truth, epi)
    assert confirmed.fact_type == FactType.ALIBI_CONFIRMED
    assert SituationAgent(epi).assess(truth).signal_type == SignalType.ALIBI_CLAIM


def test_episodic_log_is_append_only_and_match_scoped(world):
    epi, _, _ = world
    with pytest.raises(EpisodicMemoryError):
        epi.append(ev(1, ActionType.MOVE, actor="Red", location="MedBay"))  # goes back in time
    with pytest.raises(EpisodicMemoryError):
        epi.append(GameEvent(timestamp=40, match_id="other-match", action=ActionType.MOVE, actor="Red", location="MedBay"))
    assert [e.seq for e in epi.events()] == list(range(len(epi)))


def test_canonical_bytes_are_deterministic(world):
    epi, _, _ = world
    assert epi.canonical_bytes() == epi.canonical_bytes()
    assert b'"action":"kill"' in epi.canonical_bytes()
