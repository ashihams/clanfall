import threading
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from config import config
from memory.episodic_memory import EpisodicMemory
from memory.vector_store import VectorStore
from models import ActionType, FactType, GameEvent, SemanticFact


class SemanticRule(BaseModel):
    rule_id: str
    trigger: ActionType
    fact_type: FactType
    confidence: float
    description: str


RULES: List[SemanticRule] = [
    SemanticRule(
        rule_id="RULE_PRESENCE_NEAR_BODY",
        trigger=ActionType.REPORT,
        fact_type=FactType.PRESENCE_NEAR_BODY,
        confidence=0.75,
        description="IF a body is reported in room R at time T AND player X was observed in R within W seconds before T THEN X was near the body",
    ),
    SemanticRule(
        rule_id="RULE_LAST_SEEN_WITH",
        trigger=ActionType.REPORT,
        fact_type=FactType.LAST_SEEN_WITH,
        confidence=0.80,
        description="IF victim V's final observed room interval overlapped with player X (latest overlap wins) THEN V was last seen with X",
    ),
    SemanticRule(
        rule_id="RULE_ALIBI_CONTRADICTION",
        trigger=ActionType.CLAIM,
        fact_type=FactType.ALIBI_CONTRADICTION,
        confidence=0.95,
        description="IF X claims to have been in room A at time t AND the public record places X in room B != A at t THEN X's alibi is contradicted",
    ),
    SemanticRule(
        rule_id="RULE_ALIBI_CONFIRMED",
        trigger=ActionType.CLAIM,
        fact_type=FactType.ALIBI_CONFIRMED,
        confidence=0.60,
        description="IF X claims to have been in room A at time t AND the public record agrees THEN X's alibi is confirmed",
    ),
]


def fact_document(fact: SemanticFact) -> str:
    players = " ".join(fact.related_players)
    return (
        f"{fact.text} | type {fact.fact_type.value} | subject {fact.subject} | "
        f"room {fact.room or 'unknown'} | players {players}"
    )


class SemanticMemory:
    """
    Semantic Memory Layer:
    Derives facts from PUBLIC event patterns via deterministic IF-THEN rules and
    stores them in the vector backend (Tencent VectorDB, local fallback).
    Everything here is per-match and is wiped by clear().
    """

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self.vector_store = vector_store or VectorStore()
        self.rules: Dict[str, SemanticRule] = {r.rule_id: r for r in RULES}
        self._facts: Dict[str, SemanticFact] = {}
        self._lock = threading.Lock()
        self.near_window = config.rules.near_body_window_s
        self.last_seen_window = config.rules.last_seen_window_s

    # ------------------------------------------------------------------
    # Derivation
    # ------------------------------------------------------------------

    def derive(self, event: GameEvent, episodic: EpisodicMemory) -> List[SemanticFact]:
        if not event.is_public:
            return []
        if event.action == ActionType.REPORT:
            return self._presence_near_body(event, episodic) + self._last_seen_with(event, episodic)
        if event.action == ActionType.CLAIM:
            return self._check_claim(event, episodic)
        return []

    def _fact_id(self, event: GameEvent, rule_id: str, subject: str) -> str:
        return f"{event.match_id[:8]}-{rule_id.replace('RULE_', '')}-{event.seq}-{subject}"

    def _presence_near_body(self, event: GameEvent, episodic: EpisodicMemory) -> List[SemanticFact]:
        rule = self.rules["RULE_PRESENCE_NEAR_BODY"]
        room, victim, reporter, t_found = event.location, event.target, event.actor, event.timestamp
        window = self.near_window
        best: Dict[str, Tuple[float, float, float]] = {}
        for player, enter, leave in episodic.presence_in(room, t_found - window, t_found):
            if player == victim or (player == reporter and leave >= t_found):
                continue
            gap = max(0.0, t_found - leave)
            if player not in best or gap < best[player][2]:
                best[player] = (enter, leave, gap)

        facts = []
        for player, (enter, leave, gap) in sorted(best.items()):
            if leave >= t_found:
                when = "and was still there when the body was found"
            else:
                when = f"{gap:.0f}s before {victim}'s body was found there"
            facts.append(SemanticFact(
                fact_id=self._fact_id(event, rule.rule_id, player),
                match_id=event.match_id,
                fact_type=rule.fact_type,
                subject=player,
                room=room,
                timestamp=leave if leave < t_found else t_found,
                text=f"{player} was in {room} from {enter:.0f}s to {min(leave, t_found):.0f}s, {when}.",
                confidence=round(0.55 + 0.35 * (1.0 - min(gap, window) / window), 3),
                related_players=[victim],
                source_event_seqs=[event.seq],
                rule_id=rule.rule_id,
            ))
        return facts

    def _last_seen_with(self, event: GameEvent, episodic: EpisodicMemory) -> List[SemanticFact]:
        rule = self.rules["RULE_LAST_SEEN_WITH"]
        victim, reporter, t_found = event.target, event.actor, event.timestamp
        victim_steps = episodic.intervals(victim, until=t_found)
        if not victim_steps:
            return []
        v_room, v_enter, v_leave = victim_steps[-1]

        candidate: Optional[Tuple[float, float, str]] = None
        for player in episodic.players():
            if player == victim:
                continue
            for room, enter, leave in episodic.intervals(player, until=t_found):
                if room != v_room or (player == reporter and leave >= t_found):
                    continue
                start, end = max(enter, v_enter), min(leave, v_leave)
                if start > end or end < t_found - self.last_seen_window:
                    continue
                if candidate is None or (start, player) > (candidate[0], candidate[2]):
                    candidate = (start, end, player)
        if candidate is None:
            return []
        start, end, player = candidate
        return [SemanticFact(
            fact_id=self._fact_id(event, rule.rule_id, player),
            match_id=event.match_id,
            fact_type=rule.fact_type,
            subject=player,
            room=v_room,
            timestamp=start,
            text=f"{victim} was last seen with {player} in {v_room} (together from {start:.0f}s to {end:.0f}s).",
            confidence=rule.confidence,
            related_players=[victim],
            source_event_seqs=[event.seq],
            rule_id=rule.rule_id,
        )]

    def _check_claim(self, event: GameEvent, episodic: EpisodicMemory) -> List[SemanticFact]:
        player, claimed_room = event.actor, event.location
        t = event.claimed_at if event.claimed_at is not None else event.timestamp
        actual = episodic.location_at(player, t)
        if actual is None or not claimed_room:
            return []
        context = event.detail.get("victim")
        related = [context] if context else []

        if actual != claimed_room:
            rule = self.rules["RULE_ALIBI_CONTRADICTION"]
            return [SemanticFact(
                fact_id=self._fact_id(event, rule.rule_id, player),
                match_id=event.match_id,
                fact_type=rule.fact_type,
                subject=player,
                room=actual,
                timestamp=t,
                text=(
                    f"{player} claimed to be in {claimed_room} at {t:.0f}s, "
                    f"but the match record places {player} in {actual} at that time."
                ),
                confidence=rule.confidence,
                related_players=related,
                source_event_seqs=[event.seq],
                rule_id=rule.rule_id,
            )]

        rule = self.rules["RULE_ALIBI_CONFIRMED"]
        tasks = episodic.find_tasks(player, claimed_room, t - self.near_window, t + self.near_window)
        text = f"{player}'s claim checks out: the record places {player} in {claimed_room} at {t:.0f}s"
        text += f" and shows a task completed there at {tasks[0].timestamp:.0f}s." if tasks else "."
        return [SemanticFact(
            fact_id=self._fact_id(event, rule.rule_id, player),
            match_id=event.match_id,
            fact_type=rule.fact_type,
            subject=player,
            room=claimed_room,
            timestamp=t,
            text=text,
            confidence=round(rule.confidence + (0.1 if tasks else 0.0), 3),
            related_players=related,
            source_event_seqs=[event.seq] + [e.seq for e in tasks[:1]],
            rule_id=rule.rule_id,
        )]

    # ------------------------------------------------------------------
    # Storage / retrieval
    # ------------------------------------------------------------------

    def store(self, facts: List[SemanticFact]) -> None:
        if not facts:
            return
        with self._lock:
            for fact in facts:
                self._facts[fact.fact_id] = fact
        self.vector_store.add_documents(
            ids=[f.fact_id for f in facts],
            documents=[fact_document(f) for f in facts],
            metadatas=[{
                "match_id": f.match_id,
                "fact_type": f.fact_type.value,
                "subject": f.subject,
                "room": f.room,
                "timestamp": f.timestamp,
                "confidence": f.confidence,
                "rule_id": f.rule_id,
            } for f in facts],
        )

    def search(self, query_text: str, top_k: int, match_id: Optional[str]) -> List[Tuple[SemanticFact, float]]:
        """Vector search, returning (fact, cosine distance). Only facts from the live match survive."""
        if not self._facts:
            return []
        result = self.vector_store.query(query_text, n_results=max(top_k, len(self._facts)))
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        out = []
        for i, fact_id in enumerate(ids):
            fact = self._facts.get(fact_id)
            if fact is None or (match_id and fact.match_id != match_id):
                continue
            out.append((fact, float(distances[i]) if i < len(distances) else 1.0))
        return out[:top_k]

    def get(self, fact_id: str) -> Optional[SemanticFact]:
        return self._facts.get(fact_id)

    def all_facts(self) -> List[SemanticFact]:
        return list(self._facts.values())

    def count(self) -> int:
        return len(self._facts)

    def get_rules(self) -> List[SemanticRule]:
        return list(self.rules.values())

    def clear(self) -> None:
        with self._lock:
            self._facts.clear()
        self.vector_store.reset()
