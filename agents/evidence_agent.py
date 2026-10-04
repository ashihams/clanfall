from typing import Optional

from llm.groq_client import GroqClient
from models import FactType, HybridMemoryContext, MeetingEvidence

LABELS = {
    FactType.ALIBI_CONTRADICTION: "ALIBI CONTRADICTED",
    FactType.LAST_SEEN_WITH: "LAST SEEN WITH",
    FactType.PRESENCE_NEAR_BODY: "NEAR THE BODY",
    FactType.ALIBI_CONFIRMED: "ALIBI CONFIRMED",
}


class EvidenceAgent:
    """
    Turns the Memory Agent's top-ranked fact into on-screen meeting evidence.
    Replaces AeroCortex's Planner Agent. The template path is deterministic and
    always available; Groq (if configured) may only rephrase the fact text.
    """

    def __init__(self, groq: Optional[GroqClient] = None):
        self.groq = groq if groq is not None else GroqClient()

    def build(self, context: HybridMemoryContext, meeting_id: int, stage: str) -> MeetingEvidence:
        top = context.top_fact
        if top is None:
            return MeetingEvidence(
                meeting_id=meeting_id,
                stage=stage,
                query=context.query,
                headline="No relevant evidence in this match's memory yet.",
            )

        fact = top.fact
        headline = f"{LABELS.get(fact.fact_type, fact.fact_type.value)}: {fact.text}"
        source = "template"
        if self.groq.is_configured:
            reply = self.groq.generate_json(f"Fact: {fact.text}")
            rephrased = (reply or {}).get("headline")
            if isinstance(rephrased, str) and 0 < len(rephrased) < 240:
                headline = f"{LABELS.get(fact.fact_type, fact.fact_type.value)}: {rephrased.strip()}"
                source = "groq"

        return MeetingEvidence(
            meeting_id=meeting_id,
            stage=stage,
            query=context.query,
            headline=headline,
            subject=fact.subject,
            fact_id=fact.fact_id,
            fact_type=fact.fact_type,
            final_score=top.final_score,
            vector_similarity=top.vector_similarity,
            graph_relevance=top.graph_relevance,
            source=source,
            supporting=[r.fact.text for r in context.retrieved_facts[1:3]],
        )
