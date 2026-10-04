from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from models import ActionType, GameEvent, MatchPhase, Role
from simulation.map import ROOMS, is_adjacent
from simulation.scenario import Scenario

# Receives each emitted event; may return a response (e.g. the pipeline state
# carrying "evidence"). The engine never imports memory or chain code.
EventSink = Callable[[GameEvent], Optional[Dict[str, Any]]]

CREW_VOTE_THRESHOLD = 0.5


class EngineError(ValueError):
    pass


@dataclass
class MatchSummary:
    match_id: str
    scenario: str
    result: str
    reason: str
    duration_s: float
    task_count: int
    meeting_count: int
    kill_count: int
    ejections: List[str]
    event_count: int
    evidence: List[Dict[str, Any]] = field(default_factory=list)


class MatchEngine:
    """
    Scripted, bot-driven match driver. Enforces the rules (adjacency, alive
    checks, impostor-only kills, report-in-room, majority vote, win conditions)
    and emits timestamped GameEvents to the sink. Bots vote on the evidence the
    meeting screen shows them.
    """

    def __init__(self, scenario: Scenario, match_id: str, roles: Dict[str, Role], sink: Optional[EventSink] = None):
        self.scenario = scenario
        self.match_id = match_id
        self.roles = dict(roles)
        self.sink = sink or (lambda event: None)

        self.phase = MatchPhase.LOBBY
        self.clock = 0.0
        self.positions: Dict[str, str] = {}
        self.alive: List[str] = list(scenario.players)
        self.bodies: Dict[str, str] = {}
        self.tasks_done = 0
        self.kills = 0
        self.meetings = 0
        self.ejections: List[str] = []
        self.step_index = 0
        self.timeline: List[Dict[str, Any]] = []
        self.evidence_shown: List[Dict[str, Any]] = []
        self.result: Optional[str] = None
        self.reason: str = ""

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def _emit(self, action: ActionType, t: float, **fields) -> Optional[Dict[str, Any]]:
        if t < self.clock:
            raise EngineError(f"step at t={t} is earlier than the game clock ({self.clock})")
        self.clock = t
        event = GameEvent(timestamp=t, match_id=self.match_id, action=action, **fields)
        response = self.sink(event) or {}
        evidence = response.get("evidence")
        if evidence is not None and hasattr(evidence, "model_dump"):
            evidence = evidence.model_dump(mode="json")
        record = {"event": (response.get("stored_event") or event).model_dump(mode="json"), "evidence": evidence,
                  "logs": response.get("logs", [])}
        self.timeline.append(record)
        if evidence:
            self.evidence_shown.append(evidence)
        return record

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.phase == MatchPhase.ENDED

    def start(self) -> List[Dict[str, Any]]:
        if self.phase != MatchPhase.LOBBY:
            raise EngineError("match already started")
        mark = len(self.timeline)
        self.positions = {p: self.scenario.spawn for p in self.scenario.players}
        self.phase = MatchPhase.PLAYING
        self._emit(
            ActionType.MATCH_START, 0.0,
            location=self.scenario.spawn,
            detail={"players": list(self.scenario.players), "scenario": self.scenario.key},
        )
        return self.timeline[mark:]

    def advance(self) -> List[Dict[str, Any]]:
        """Process the next scripted step; returns the records it produced."""
        if self.phase == MatchPhase.LOBBY:
            return self.start()
        if self.done:
            return []
        mark = len(self.timeline)
        if self.step_index >= len(self.scenario.steps):
            self._game_over("UNRESOLVED", "script ended without a winner")
            return self.timeline[mark:]
        step = self.scenario.steps[self.step_index]
        self.step_index += 1
        handler = {
            "move": self._move,
            "task": self._task,
            "kill": self._kill,
            "report": self._report,
        }.get(step["action"])
        if handler is None:
            raise EngineError(f"unknown action {step['action']!r}")
        handler(step)
        self._check_win()
        return self.timeline[mark:]

    def run(self) -> MatchSummary:
        if self.phase == MatchPhase.LOBBY:
            self.start()
        while not self.done:
            self.advance()
        return self.summary()

    def summary(self) -> MatchSummary:
        return MatchSummary(
            match_id=self.match_id,
            scenario=self.scenario.key,
            result=self.result or "IN_PROGRESS",
            reason=self.reason,
            duration_s=self.clock,
            task_count=self.tasks_done,
            meeting_count=self.meetings,
            kill_count=self.kills,
            ejections=list(self.ejections),
            event_count=len(self.timeline),
            evidence=list(self.evidence_shown),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _require_alive(self, player: str) -> None:
        if player not in self.roles:
            raise EngineError(f"unknown player {player!r}")
        if player not in self.alive:
            raise EngineError(f"{player} is not alive")

    def _move(self, step: Dict[str, Any]) -> None:
        actor, dest = step["actor"], step["location"]
        self._require_alive(actor)
        if dest not in ROOMS:
            raise EngineError(f"unknown room {dest!r}")
        src = self.positions[actor]
        if not is_adjacent(src, dest):
            raise EngineError(f"{actor} cannot move {src} -> {dest}: rooms are not adjacent")
        self.positions[actor] = dest
        self._emit(ActionType.MOVE, step["t"], actor=actor, location=dest, detail={"from": src})

    def _task(self, step: Dict[str, Any]) -> None:
        actor = step["actor"]
        self._require_alive(actor)
        room = step.get("location", self.positions[actor])
        if room != self.positions[actor]:
            raise EngineError(f"{actor} is in {self.positions[actor]}, not {room}")
        if self.roles[actor] == Role.IMPOSTOR:
            raise EngineError("impostors cannot complete real tasks")
        self.tasks_done += 1
        self._emit(ActionType.TASK, step["t"], actor=actor, location=room)

    def _kill(self, step: Dict[str, Any]) -> None:
        actor, victim = step["actor"], step["target"]
        self._require_alive(actor)
        self._require_alive(victim)
        if self.roles[actor] != Role.IMPOSTOR:
            raise EngineError(f"{actor} is not an impostor")
        if self.roles[victim] == Role.IMPOSTOR:
            raise EngineError("impostors cannot kill each other")
        room = self.positions[actor]
        if self.positions[victim] != room:
            raise EngineError(f"{victim} is not in {room}")
        self.alive.remove(victim)
        self.bodies[victim] = room
        self.kills += 1
        self._emit(ActionType.KILL, step["t"], actor=actor, target=victim, location=room)

    def _report(self, step: Dict[str, Any]) -> None:
        actor, victim = step["actor"], step["target"]
        self._require_alive(actor)
        room = self.positions[actor]
        if self.bodies.get(victim) != room:
            raise EngineError(f"no body of {victim} in {room}")
        t = float(step["t"])
        self._emit(ActionType.REPORT, t, actor=actor, target=victim, location=room)
        self._meeting(t, victim, room, step.get("meeting", {}))

    def _meeting(self, t: float, victim: str, room: str, script: Dict[str, Any]) -> None:
        self.meetings += 1
        meeting_id = self.meetings
        self.phase = MatchPhase.MEETING
        opening = self._emit(ActionType.MEETING_START, t + 1, meeting_id=meeting_id, location=room,
                             detail={"victim": victim})
        clock = t + 2
        for claim in script.get("claims", []):
            if claim["actor"] not in self.alive:
                raise EngineError(f"{claim['actor']} cannot speak: not alive")
            self._emit(
                ActionType.CLAIM, clock,
                actor=claim["actor"], location=claim["location"], claimed_at=float(claim["claimed_at"]),
                meeting_id=meeting_id, detail={"victim": victim},
            )
            clock += 1
        voting = self._emit(ActionType.VOTING_OPEN, clock, meeting_id=meeting_id)
        evidence = (voting or {}).get("evidence") or (opening or {}).get("evidence")
        clock += 1

        votes_script = script.get("votes", "auto")
        ballots: Dict[str, Optional[str]] = {}
        for voter in list(self.alive):
            if isinstance(votes_script, dict):
                target = votes_script.get(voter)
            else:
                target = self._auto_vote(voter, evidence)
            ballots[voter] = target
            self._emit(ActionType.VOTE, clock, actor=voter, target=target, meeting_id=meeting_id,
                       detail={"skip": target is None})

        ejected = self._tally(ballots)
        tally = Counter(v if v is not None else "skip" for v in ballots.values())
        clock += 1
        if ejected:
            self.alive.remove(ejected)
            self.ejections.append(ejected)
        self.bodies.clear()
        self.phase = MatchPhase.PLAYING
        self._emit(ActionType.EJECT, clock, target=ejected, meeting_id=meeting_id,
                   detail={"tally": dict(tally)})

    def _auto_vote(self, voter: str, evidence: Optional[Dict[str, Any]]) -> Optional[str]:
        subject = (evidence or {}).get("subject")
        score = float((evidence or {}).get("final_score") or 0.0)
        if self.roles[voter] == Role.IMPOSTOR:
            if subject and subject != voter and subject in self.alive:
                return subject
            others = sorted(p for p in self.alive if p != voter and self.roles[p] != Role.IMPOSTOR)
            return others[0] if others else None
        if subject and subject != voter and subject in self.alive and score >= CREW_VOTE_THRESHOLD:
            return subject
        return None

    @staticmethod
    def _tally(ballots: Dict[str, Optional[str]]) -> Optional[str]:
        """Simple majority: the top target must beat every other option, including skip."""
        counts = Counter(v if v is not None else "__skip__" for v in ballots.values())
        ranked = counts.most_common()
        if not ranked:
            return None
        top, top_n = ranked[0]
        if top == "__skip__" or (len(ranked) > 1 and ranked[1][1] == top_n):
            return None
        return top

    # ------------------------------------------------------------------
    # Win conditions
    # ------------------------------------------------------------------

    def _check_win(self) -> None:
        if self.done:
            return
        impostors = [p for p in self.alive if self.roles[p] == Role.IMPOSTOR]
        crew = [p for p in self.alive if self.roles[p] == Role.CREW]
        if not impostors:
            self._game_over("CREW_VICTORY", "every impostor was ejected")
        elif len(impostors) >= len(crew):
            self._game_over("IMPOSTOR_VICTORY", "impostors reached parity with the crew")
        elif self.tasks_done >= self.scenario.tasks_required:
            self._game_over("CREW_VICTORY", "the crew finished every task")

    def _game_over(self, result: str, reason: str) -> None:
        self.result, self.reason = result, reason
        self.phase = MatchPhase.ENDED
        self._emit(ActionType.GAME_OVER, self.clock + 1, detail={"result": result, "reason": reason})
