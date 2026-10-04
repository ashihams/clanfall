import logging
import time
from typing import Any, Dict, List, Optional

from bridge.mock_mode import MOCK_MEMORY
from models import ActionType, GameEvent
from orchestration.match_runner import MatchOrchestrator

logger = logging.getLogger("clanfall.bridge.memory")


class MemoryClient:
    """Client for the memory pipeline (MatchOrchestrator / AeroCortex memory graph)."""

    def __init__(self, orchestrator: Optional[MatchOrchestrator] = None):
        self._orchestrator = orchestrator or MatchOrchestrator()
        self._use_mock = False

    def start_match(self, match_id: str, players: List[str], impostors: List[str]) -> Any:
        try:
            from simulation.scenario import Scenario
            sc = Scenario(
                key="2d_match",
                title="2D Pygame Match",
                description="Live 2D Among Us game session",
                players=players,
                impostors=impostors,
                steps=[],
            )
            sess = self._orchestrator.start_match(sc)
            return sess
        except Exception as exc:
            logger.warning("Failed to start orchestrator session, falling back to mock memory: %s", exc)
            self._use_mock = True
            MOCK_MEMORY.reset()
            return None

    def _active_id(self, fallback: str) -> str:
        if self._orchestrator and self._orchestrator.session:
            return self._orchestrator.session.match_id
        return fallback

    def ingest_kill(self, match_id: str, killer: str, victim: str, location: Optional[str] = "Cafeteria") -> Dict[str, Any]:
        if self._use_mock or self._orchestrator.session is None:
            return MOCK_MEMORY.ingest_event("kill", killer, victim, location)
        m_id = self._active_id(match_id)
        event = GameEvent(
            seq=len(self._orchestrator.memory.episodic.events()) + 1,
            timestamp=time.time(),
            match_id=m_id,
            actor=killer,
            action=ActionType.KILL,
            target=victim,
            location=location or "Cafeteria",
        )
        return self._orchestrator.ingest(event)

    def ingest_vote(self, match_id: str, voter: str, target: Optional[str]) -> Dict[str, Any]:
        if self._use_mock or self._orchestrator.session is None:
            return MOCK_MEMORY.ingest_event("vote", voter, target)
        m_id = self._active_id(match_id)
        event = GameEvent(
            seq=len(self._orchestrator.memory.episodic.events()) + 1,
            timestamp=time.time(),
            match_id=m_id,
            actor=voter,
            action=ActionType.VOTE,
            target=target,
        )
        return self._orchestrator.ingest(event)

    def ingest_eject(self, match_id: str, ejected: Optional[str]) -> Dict[str, Any]:
        if self._use_mock or self._orchestrator.session is None:
            return MOCK_MEMORY.ingest_event("eject", None, ejected)
        m_id = self._active_id(match_id)
        event = GameEvent(
            seq=len(self._orchestrator.memory.episodic.events()) + 1,
            timestamp=time.time(),
            match_id=match_id,
            actor="SYSTEM",
            action=ActionType.EJECT,
            target=ejected,
        )
        return self._orchestrator.ingest(event)

    def get_meeting_evidence(self, match_id: str, reporter: Optional[str] = None) -> str:
        """Query memory graph for surfaced evidence text during body report / meeting start."""
        if self._use_mock or self._orchestrator.session is None:
            return MOCK_MEMORY.retrieve_evidence(player=reporter)
        try:
            # Query the memory graph's memory agent for evidence
            ctx = self._orchestrator.graph.memory_agent.retrieve(
                query_text=f"where was players right before body was reported by {reporter or 'crew'}",
                top_k=3,
            )
            if ctx and ctx.retrieved_facts:
                top = ctx.retrieved_facts[0].fact
                return f"Surfaced Evidence: {top.text}"
            return f"Surfaced Evidence: {reporter or 'Player'} reported a body. Recent movements recorded in vicinity."
        except Exception as exc:
            logger.warning("Error retrieving evidence from graph: %s", exc)
            return MOCK_MEMORY.retrieve_evidence(player=reporter)

    def end_match(self) -> Any:
        if self._use_mock or self._orchestrator.session is None:
            return None
        return self._orchestrator.end_match()

    def reset(self) -> Dict[str, Any]:
        MOCK_MEMORY.reset()
        if self._orchestrator:
            return self._orchestrator.abort_match()
        return {"wiped": True}
