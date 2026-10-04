from typing import List, Optional, TypedDict

from models import GameEvent, HybridMemoryContext, MeetingEvidence, SemanticFact, SituationReport


class ClanfallState(TypedDict, total=False):
    """Typed data bus for one event's trip through the LangGraph pipeline."""
    event: GameEvent
    stored_event: GameEvent
    situation: SituationReport
    new_facts: List[SemanticFact]
    memory_context: Optional[HybridMemoryContext]
    evidence: Optional[MeetingEvidence]
    logs: List[str]
