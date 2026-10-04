from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Role(IntEnum):
    """Integer values are what gets committed on-chain (uint8)."""
    CREW = 0
    IMPOSTOR = 1


class ActionType(str, Enum):
    MATCH_START = "match_start"
    MOVE = "move"
    TASK = "task"
    KILL = "kill"
    REPORT = "report"
    MEETING_START = "meeting_start"
    CLAIM = "claim"
    VOTING_OPEN = "voting_open"
    VOTE = "vote"
    EJECT = "eject"
    GAME_OVER = "game_over"


class Visibility(str, Enum):
    """
    PUBLIC events are observable by players and may feed semantic memory.
    HIDDEN events (kills) are recorded in the hash-committed episodic log but
    are never used to derive facts — otherwise the engine would just leak the
    impostor's identity.
    """
    PUBLIC = "public"
    HIDDEN = "hidden"


HIDDEN_ACTIONS = {ActionType.KILL}


class MatchPhase(str, Enum):
    LOBBY = "LOBBY"
    PLAYING = "PLAYING"
    MEETING = "MEETING"
    ENDED = "ENDED"


class SignalType(str, Enum):
    NONE = "NONE"
    BODY_FOUND = "BODY_FOUND"
    ALIBI_CLAIM = "ALIBI_CLAIM"
    ALIBI_CONTRADICTION = "ALIBI_CONTRADICTION"
    VOTING = "VOTING"
    EJECTION = "EJECTION"
    MATCH_OVER = "MATCH_OVER"


class SeverityLevel(str, Enum):
    NOMINAL = "NOMINAL"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FactType(str, Enum):
    ALIBI_CONTRADICTION = "ALIBI_CONTRADICTION"
    LAST_SEEN_WITH = "LAST_SEEN_WITH"
    PRESENCE_NEAR_BODY = "PRESENCE_NEAR_BODY"
    ALIBI_CONFIRMED = "ALIBI_CONFIRMED"


class GameEvent(BaseModel):
    """One timestamped entry in the append-only episodic log."""
    seq: int = 0
    timestamp: float = Field(ge=0.0, description="Game-clock seconds since match start")
    match_id: str
    actor: Optional[str] = None
    action: ActionType
    location: Optional[str] = None
    target: Optional[str] = None
    claimed_at: Optional[float] = None
    meeting_id: Optional[int] = None
    detail: Dict[str, Any] = Field(default_factory=dict)

    @property
    def visibility(self) -> Visibility:
        return Visibility.HIDDEN if self.action in HIDDEN_ACTIONS else Visibility.PUBLIC

    @property
    def is_public(self) -> bool:
        return self.visibility == Visibility.PUBLIC


class SituationReport(BaseModel):
    suspicious: bool = False
    signal_type: SignalType = SignalType.NONE
    severity: SeverityLevel = SeverityLevel.NOMINAL
    confidence: float = 1.0
    subjects: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    description: str = "Nominal match activity."
    needs_evidence: bool = False


class SemanticFact(BaseModel):
    fact_id: str
    match_id: str
    fact_type: FactType
    subject: str
    room: Optional[str] = None
    timestamp: float
    text: str
    confidence: float = 0.8
    related_players: List[str] = Field(default_factory=list)
    source_event_seqs: List[int] = Field(default_factory=list)
    rule_id: str = ""


class RetrievedFact(BaseModel):
    fact: SemanticFact
    vector_similarity: float = 0.0
    graph_relevance: float = 0.0
    final_score: float = 0.0


class HybridMemoryContext(BaseModel):
    query: str = ""
    retrieved_facts: List[RetrievedFact] = Field(default_factory=list)
    graph_paths: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_latency_ms: float = 0.0
    vector_engine: str = "memory"

    @property
    def top_fact(self) -> Optional[RetrievedFact]:
        return self.retrieved_facts[0] if self.retrieved_facts else None


class MeetingEvidence(BaseModel):
    meeting_id: int
    stage: str
    query: str
    headline: str
    subject: Optional[str] = None
    fact_id: Optional[str] = None
    fact_type: Optional[FactType] = None
    final_score: float = 0.0
    vector_similarity: float = 0.0
    graph_relevance: float = 0.0
    source: str = "template"
    supporting: List[str] = Field(default_factory=list)


__all__ = [
    "Role", "ActionType", "Visibility", "HIDDEN_ACTIONS", "MatchPhase", "SignalType",
    "SeverityLevel", "FactType", "GameEvent", "SituationReport", "SemanticFact",
    "RetrievedFact", "HybridMemoryContext", "MeetingEvidence",
]
