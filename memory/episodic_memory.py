import json
import threading
from typing import Dict, List, Optional, Tuple

from models import ActionType, GameEvent

INF = float("inf")


class EpisodicMemoryError(ValueError):
    pass


class EpisodicMemory:
    """
    Episodic Memory Layer:
    Append-only, per-match log of timestamped game events. Hidden events (kills)
    are stored here — they are part of the hash-committed record — but the
    public timeline helpers below only ever read PUBLIC events.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events: List[GameEvent] = []
        self._match_id: Optional[str] = None
        # player -> [(enter_time, room)] built from public MATCH_START / MOVE events
        self._timeline: Dict[str, List[Tuple[float, str]]] = {}
        self._dead: Dict[str, float] = {}

    @property
    def match_id(self) -> Optional[str]:
        return self._match_id

    def __len__(self) -> int:
        return len(self._events)

    def append(self, event: GameEvent) -> GameEvent:
        with self._lock:
            if self._match_id is None:
                self._match_id = event.match_id
            elif event.match_id != self._match_id:
                raise EpisodicMemoryError(
                    f"event for match {event.match_id} rejected: episodic memory is scoped to {self._match_id}"
                )
            if self._events and event.timestamp < self._events[-1].timestamp:
                raise EpisodicMemoryError(
                    f"non-monotonic timestamp {event.timestamp} < {self._events[-1].timestamp}"
                )
            stored = event.model_copy(update={"seq": len(self._events)})
            self._events.append(stored)
            self._index(stored)
            return stored

    def _index(self, event: GameEvent) -> None:
        if not event.is_public:
            return
        if event.action == ActionType.MATCH_START:
            for player in event.detail.get("players", []):
                self._timeline[player] = [(event.timestamp, event.location)]
        elif event.action == ActionType.MOVE and event.actor and event.location:
            self._timeline.setdefault(event.actor, []).append((event.timestamp, event.location))
        elif event.action == ActionType.REPORT and event.target:
            self._dead.setdefault(event.target, event.timestamp)
        elif event.action == ActionType.EJECT and event.target:
            self._dead.setdefault(event.target, event.timestamp)

    # ------------------------------------------------------------------
    # Read helpers (public view only)
    # ------------------------------------------------------------------

    def events(self, public_only: bool = False) -> List[GameEvent]:
        with self._lock:
            return [e for e in self._events if e.is_public] if public_only else list(self._events)

    def players(self) -> List[str]:
        return list(self._timeline.keys())

    def location_at(self, player: str, t: float) -> Optional[str]:
        room = None
        for enter, r in self._timeline.get(player, []):
            if enter <= t:
                room = r
            else:
                break
        return room

    def intervals(self, player: str, until: float = INF) -> List[Tuple[str, float, float]]:
        """[(room, enter, leave)] — leave is the next move, or `until`."""
        steps = self._timeline.get(player, [])
        out: List[Tuple[str, float, float]] = []
        for i, (enter, room) in enumerate(steps):
            if enter > until:
                break
            leave = steps[i + 1][0] if i + 1 < len(steps) else until
            out.append((room, enter, min(leave, until)))
        return out

    def presence_in(self, room: str, start: float, end: float) -> List[Tuple[str, float, float]]:
        """Players observed in `room` at any point within [start, end]."""
        out = []
        for player in self._timeline:
            for r, enter, leave in self.intervals(player, until=end):
                if r == room and enter <= end and leave >= start:
                    out.append((player, enter, leave))
        return out

    def find_tasks(self, player: str, room: str, start: float, end: float) -> List[GameEvent]:
        return [
            e for e in self.events(public_only=True)
            if e.action == ActionType.TASK and e.actor == player and e.location == room
            and start <= e.timestamp <= end
        ]

    # ------------------------------------------------------------------
    # Canonical serialization — this exact byte string is what gets hashed
    # ------------------------------------------------------------------

    def canonical_log(self) -> List[dict]:
        return [e.model_dump(mode="json") for e in self.events()]

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.canonical_log())

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._timeline.clear()
            self._dead.clear()
            self._match_id = None


def canonical_bytes(log: List[dict]) -> bytes:
    return json.dumps(log, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
