import copy

import pytest

from models import ActionType, Role
from simulation.engine import EngineError, MatchEngine
from simulation.scenario import CLEAN_GETAWAY, REACTOR_LIE, SCENARIOS


def roles_for(scenario):
    return {p: (Role.IMPOSTOR if p in scenario.impostors else Role.CREW) for p in scenario.players}


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_scenarios_reach_expected_result(orchestrator, key):
    outcome = orchestrator.run_scenario(key)
    assert outcome.passport.result == SCENARIOS[key].expected_result
    assert outcome.passport.kill_count == 1
    assert outcome.passport.meeting_count == 1


def test_engine_without_memory_skips_votes():
    engine = MatchEngine(REACTOR_LIE, "m", roles_for(REACTOR_LIE))  # no sink -> no evidence shown
    summary = engine.run()
    assert summary.ejections == []
    eject = [r for r in engine.timeline if r["event"]["action"] == ActionType.EJECT.value][0]
    assert eject["event"]["target"] is None


def _with_step(scenario, index, **changes):
    s = copy.deepcopy(scenario)
    s.steps[index].update(changes)
    return s


@pytest.mark.parametrize("mutation, message", [
    (dict(index=0, location="Electrical"), "not adjacent"),
    (dict(index=8, actor="Red"), "not an impostor"),
    (dict(index=8, target="Red"), "is not in"),
    (dict(index=12, target="Red"), "no body"),
])
def test_rule_violations_are_rejected(mutation, message):
    scenario = _with_step(REACTOR_LIE, mutation.pop("index"), **mutation)
    engine = MatchEngine(scenario, "m", roles_for(scenario))
    with pytest.raises(EngineError, match=message):
        engine.run()


def test_impostors_cannot_do_real_tasks():
    scenario = copy.deepcopy(CLEAN_GETAWAY)
    scenario.steps.insert(4, {"t": 6, "actor": "Red", "action": "task", "location": "MedBay"})
    with pytest.raises(EngineError, match="impostors cannot"):
        MatchEngine(scenario, "m", roles_for(scenario)).run()


def test_tally_needs_a_strict_majority_over_skip_and_ties():
    tally = MatchEngine._tally
    assert tally({"a": "x", "b": "x", "c": None}) == "x"
    assert tally({"a": "x", "b": "y"}) is None
    assert tally({"a": "x", "b": None}) is None
    assert tally({"a": None, "b": None, "c": "x"}) is None
