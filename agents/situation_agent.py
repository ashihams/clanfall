from memory.episodic_memory import EpisodicMemory
from models import ActionType, GameEvent, SeverityLevel, SignalType, SituationReport


class SituationAgent:
    """
    Situation Agent:
    Deterministic, rule-based classifier for incoming game events. Like the
    AeroCortex original it never uses an LLM for classification. Hidden events
    are acknowledged but never described, so nothing about them leaks out.
    """

    def __init__(self, episodic: EpisodicMemory):
        self.episodic = episodic

    def assess(self, event: GameEvent) -> SituationReport:
        if not event.is_public:
            return SituationReport(description="Hidden event recorded to the sealed log.")

        if event.action == ActionType.REPORT:
            return SituationReport(
                suspicious=True,
                signal_type=SignalType.BODY_FOUND,
                severity=SeverityLevel.CRITICAL,
                confidence=1.0,
                subjects=[event.target, event.actor],
                location=event.location,
                description=f"{event.actor} found {event.target}'s body in {event.location} at {event.timestamp:.0f}s.",
            )

        if event.action == ActionType.MEETING_START:
            return SituationReport(
                suspicious=True,
                signal_type=SignalType.BODY_FOUND,
                severity=SeverityLevel.HIGH,
                description=f"Emergency meeting #{event.meeting_id} opened.",
                needs_evidence=True,
            )

        if event.action == ActionType.CLAIM:
            t = event.claimed_at if event.claimed_at is not None else event.timestamp
            actual = self.episodic.location_at(event.actor, t)
            if actual is not None and actual != event.location:
                return SituationReport(
                    suspicious=True,
                    signal_type=SignalType.ALIBI_CONTRADICTION,
                    severity=SeverityLevel.HIGH,
                    confidence=0.95,
                    subjects=[event.actor],
                    location=actual,
                    description=(
                        f"{event.actor} claims {event.location} at {t:.0f}s; "
                        f"record says {actual}. Contradiction."
                    ),
                )
            return SituationReport(
                signal_type=SignalType.ALIBI_CLAIM,
                severity=SeverityLevel.LOW,
                subjects=[event.actor],
                location=event.location,
                description=f"{event.actor} claims {event.location} at {t:.0f}s (consistent with the record).",
            )

        if event.action == ActionType.VOTING_OPEN:
            return SituationReport(
                signal_type=SignalType.VOTING,
                severity=SeverityLevel.MEDIUM,
                description=f"Voting opened for meeting #{event.meeting_id}.",
                needs_evidence=True,
            )

        if event.action == ActionType.EJECT:
            who = event.target or "nobody"
            return SituationReport(
                signal_type=SignalType.EJECTION,
                severity=SeverityLevel.MEDIUM,
                subjects=[event.target] if event.target else [],
                description=f"Vote resolved: {who} ejected.",
            )

        if event.action == ActionType.GAME_OVER:
            return SituationReport(
                signal_type=SignalType.MATCH_OVER,
                description=f"Match over: {event.detail.get('result', 'UNKNOWN')}.",
            )

        return SituationReport()
