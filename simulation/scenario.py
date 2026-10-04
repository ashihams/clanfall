from dataclasses import dataclass, field
from typing import Any, Dict, List

from simulation.map import SPAWN


@dataclass
class Scenario:
    key: str
    title: str
    description: str
    players: List[str]
    impostors: List[str]
    steps: List[Dict[str, Any]]
    tasks_required: int = 12
    spawn: str = SPAWN
    expected_result: str = ""
    notes: List[str] = field(default_factory=list)


# Step schema:
#   {"t": float, "actor": str, "action": "move"|"task"|"kill"|"report", "location"?: str, "target"?: str}
#   A "report" step carries its whole meeting:
#   "meeting": {"claims": [{"actor", "location", "claimed_at"}], "votes": "auto" | {voter: target|None}}

REACTOR_LIE = Scenario(
    key="reactor_lie",
    title="The Reactor Lie",
    description=(
        "Blue kills Green in Electrical, slips out through Storage, and at the meeting "
        "claims to have been in Reactor. The memory engine catches the contradiction live."
    ),
    players=["Red", "Blue", "Green", "Yellow"],
    impostors=["Blue"],
    expected_result="CREW_VICTORY",
    steps=[
        {"t": 5, "actor": "Red", "action": "move", "location": "MedBay"},
        {"t": 5, "actor": "Green", "action": "move", "location": "Storage"},
        {"t": 5, "actor": "Blue", "action": "move", "location": "Storage"},
        {"t": 5, "actor": "Yellow", "action": "move", "location": "Storage"},
        {"t": 10, "actor": "Green", "action": "move", "location": "Electrical"},
        {"t": 10, "actor": "Yellow", "action": "task", "location": "Storage"},
        {"t": 14, "actor": "Red", "action": "task", "location": "MedBay"},
        {"t": 18, "actor": "Blue", "action": "move", "location": "Electrical"},
        {"t": 22, "actor": "Blue", "action": "kill", "target": "Green"},
        {"t": 26, "actor": "Blue", "action": "move", "location": "Storage"},
        {"t": 30, "actor": "Blue", "action": "move", "location": "Reactor"},
        {"t": 33, "actor": "Yellow", "action": "move", "location": "Electrical"},
        {
            "t": 34, "actor": "Yellow", "action": "report", "target": "Green",
            "meeting": {
                "claims": [
                    {"actor": "Red", "location": "MedBay", "claimed_at": 22},
                    {"actor": "Yellow", "location": "Storage", "claimed_at": 22},
                    {"actor": "Blue", "location": "Reactor", "claimed_at": 22},
                ],
                "votes": "auto",
            },
        },
    ],
)

CLEAN_GETAWAY = Scenario(
    key="clean_getaway",
    title="Clean Getaway",
    description=(
        "Red kills Yellow in Reactor and tells only the truth afterwards. Green wanders "
        "through Reactor without noticing the body, so the evidence points at an innocent "
        "player. The engine only remembers what was observable, and the impostor wins."
    ),
    players=["Red", "Blue", "Green", "Yellow"],
    impostors=["Red"],
    expected_result="IMPOSTOR_VICTORY",
    steps=[
        {"t": 5, "actor": "Red", "action": "move", "location": "MedBay"},
        {"t": 5, "actor": "Yellow", "action": "move", "location": "MedBay"},
        {"t": 5, "actor": "Green", "action": "move", "location": "Storage"},
        {"t": 5, "actor": "Blue", "action": "move", "location": "Storage"},
        {"t": 9, "actor": "Yellow", "action": "task", "location": "MedBay"},
        {"t": 9, "actor": "Green", "action": "task", "location": "Storage"},
        {"t": 12, "actor": "Yellow", "action": "move", "location": "Reactor"},
        {"t": 14, "actor": "Red", "action": "move", "location": "Reactor"},
        {"t": 16, "actor": "Blue", "action": "move", "location": "Electrical"},
        {"t": 19, "actor": "Blue", "action": "task", "location": "Electrical"},
        {"t": 20, "actor": "Red", "action": "kill", "target": "Yellow"},
        {"t": 21, "actor": "Red", "action": "move", "location": "MedBay"},
        {"t": 23, "actor": "Green", "action": "move", "location": "Reactor"},
        {"t": 26, "actor": "Green", "action": "move", "location": "Storage"},
        {"t": 29, "actor": "Blue", "action": "move", "location": "Storage"},
        {"t": 31, "actor": "Blue", "action": "move", "location": "Reactor"},
        {
            "t": 32, "actor": "Blue", "action": "report", "target": "Yellow",
            "meeting": {
                "claims": [
                    {"actor": "Red", "location": "MedBay", "claimed_at": 22},
                    {"actor": "Green", "location": "Storage", "claimed_at": 20},
                    {"actor": "Blue", "location": "Electrical", "claimed_at": 20},
                ],
                "votes": "auto",
            },
        },
    ],
)

SCENARIOS: Dict[str, Scenario] = {s.key: s for s in (REACTOR_LIE, CLEAN_GETAWAY)}


def get_scenario(key: str) -> Scenario:
    if key not in SCENARIOS:
        raise KeyError(f"unknown scenario {key!r}; available: {sorted(SCENARIOS)}")
    return SCENARIOS[key]
