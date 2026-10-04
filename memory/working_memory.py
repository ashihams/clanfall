import threading
from typing import Any, Dict, List, Optional

from models import ActionType, GameEvent, MatchPhase, MeetingEvidence


class WorkingMemory:
    """
    Working Memory: fast in-memory state for the live match — phase, who is
    alive, where everyone was last seen, the current body/meeting, and the
    evidence surfaced so far. Built from PUBLIC events only.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.clear()

    def clear(self) -> None:
        with self.lock:
            self.match_id: Optional[str] = None
            self.phase: MatchPhase = MatchPhase.LOBBY
            self.clock: float = 0.0
            self.players: List[str] = []
            self.alive: List[str] = []
            self.locations: Dict[str, str] = {}
            self.current_meeting: Optional[int] = None
            self.last_body: Optional[Dict[str, Any]] = None
            self.evidence: List[MeetingEvidence] = []
            self.event_count: int = 0
            self.last_event: Optional[GameEvent] = None

    def update(self, event: GameEvent) -> None:
        with self.lock:
            self.event_count += 1
            self.clock = event.timestamp
            if not event.is_public:
                return
            self.last_event = event
            if event.action == ActionType.MATCH_START:
                self.match_id = event.match_id
                self.phase = MatchPhase.PLAYING
                self.players = list(event.detail.get("players", []))
                self.alive = list(self.players)
                self.locations = {p: event.location for p in self.players}
            elif event.action == ActionType.MOVE:
                self.locations[event.actor] = event.location
            elif event.action == ActionType.REPORT:
                if event.target in self.alive:
                    self.alive.remove(event.target)
                self.last_body = {
                    "victim": event.target,
                    "room": event.location,
                    "found_at": event.timestamp,
                    "reporter": event.actor,
                    "event_seq": event.seq,
                }
            elif event.action == ActionType.MEETING_START:
                self.phase = MatchPhase.MEETING
                self.current_meeting = event.meeting_id
            elif event.action == ActionType.EJECT:
                # Every meeting closes with an EJECT event; target=None means nobody was ejected.
                if event.target in self.alive:
                    self.alive.remove(event.target)
                self.phase = MatchPhase.PLAYING
                self.current_meeting = None
            elif event.action == ActionType.GAME_OVER:
                self.phase = MatchPhase.ENDED

    def add_evidence(self, evidence: MeetingEvidence) -> None:
        with self.lock:
            self.evidence.append(evidence)

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "match_id": self.match_id,
                "phase": self.phase.value,
                "clock": self.clock,
                "players": list(self.players),
                "alive": list(self.alive),
                "locations": dict(self.locations),
                "current_meeting": self.current_meeting,
                "last_body": dict(self.last_body) if self.last_body else None,
                "evidence": [e.model_dump(mode="json") for e in self.evidence],
                "event_count": self.event_count,
            }

    def is_empty(self) -> bool:
        return self.match_id is None and self.event_count == 0 and not self.evidence
