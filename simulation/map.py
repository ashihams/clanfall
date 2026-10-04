from typing import Dict, List, Set

SPAWN = "Cafeteria"

ROOMS: List[str] = ["Cafeteria", "MedBay", "Storage", "Electrical", "Reactor"]

_EDGES = [
    ("Cafeteria", "MedBay"),
    ("Cafeteria", "Storage"),
    ("Storage", "Electrical"),
    ("Storage", "Reactor"),
    ("MedBay", "Reactor"),
]

ADJACENCY: Dict[str, Set[str]] = {room: set() for room in ROOMS}
for a, b in _EDGES:
    ADJACENCY[a].add(b)
    ADJACENCY[b].add(a)


def is_adjacent(a: str, b: str) -> bool:
    return b in ADJACENCY.get(a, set())


def edges() -> List[List[str]]:
    return [list(e) for e in _EDGES]
