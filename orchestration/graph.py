from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from agents.evidence_agent import EvidenceAgent
from agents.learning_agent import LearningAgent
from agents.memory_agent import MemoryAgent
from agents.situation_agent import SituationAgent
from memory.match_memory import MatchMemory
from models import ActionType, GameEvent
from orchestration.state import ClanfallState


class ClanfallGraph:
    """
    Per-event LangGraph pipeline (same shape as the AeroCortex graph):

        ingest -> situation -> consolidate -> [needs evidence?] -> memory -> evidence -> END
                                                    \\-> END

    The simulation never calls this directly; it only emits events that arrive
    here through the orchestrator / POST /event boundary.
    """

    def __init__(self, memory: Optional[MatchMemory] = None, evidence_agent: Optional[EvidenceAgent] = None):
        self.memory = memory or MatchMemory()
        self.situation_agent = SituationAgent(self.memory.episodic)
        self.learning_agent = LearningAgent(self.memory)
        self.memory_agent = MemoryAgent(self.memory)
        self.evidence_agent = evidence_agent or EvidenceAgent()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(ClanfallState)
        builder.add_node("ingest_node", self._ingest_step)
        builder.add_node("situation_node", self._situation_step)
        builder.add_node("consolidate_node", self._consolidate_step)
        builder.add_node("memory_node", self._memory_step)
        builder.add_node("evidence_node", self._evidence_step)

        builder.add_edge(START, "ingest_node")
        builder.add_edge("ingest_node", "situation_node")
        builder.add_edge("situation_node", "consolidate_node")
        builder.add_conditional_edges(
            "consolidate_node",
            self._route_evidence,
            {"evidence": "memory_node", "done": END},
        )
        builder.add_edge("memory_node", "evidence_node")
        builder.add_edge("evidence_node", END)
        return builder.compile()

    def _ingest_step(self, state: ClanfallState) -> Dict[str, Any]:
        stored = self.memory.episodic.append(state["event"])
        self.memory.record_event(stored)
        self.memory.working.update(stored)
        logs = state.get("logs", [])
        tag = "SEALED" if not stored.is_public else stored.action.value.upper()
        logs.append(f"[t={stored.timestamp:>5.1f}s] [EPISODIC] #{stored.seq} {tag}")
        return {"stored_event": stored, "logs": logs}

    def _situation_step(self, state: ClanfallState) -> Dict[str, Any]:
        report = self.situation_agent.assess(state["stored_event"])
        logs = state.get("logs", [])
        if report.signal_type.value != "NONE":
            logs.append(f"[SITUATION] {report.signal_type.value}: {report.description}")
        return {"situation": report, "logs": logs}

    def _consolidate_step(self, state: ClanfallState) -> Dict[str, Any]:
        facts = self.learning_agent.consolidate(state["stored_event"])
        logs = state.get("logs", [])
        for fact in facts:
            logs.append(f"[SEMANTIC] {fact.fact_type.value} ({fact.confidence:.2f}): {fact.text}")
        return {"new_facts": facts, "logs": logs}

    def _route_evidence(self, state: ClanfallState) -> str:
        return "evidence" if state["situation"].needs_evidence else "done"

    def _memory_step(self, state: ClanfallState) -> Dict[str, Any]:
        event = state["stored_event"]
        stage = "voting" if event.action == ActionType.VOTING_OPEN else "opening"
        context = self.memory_agent.retrieve_for_stage(stage)
        logs = state.get("logs", [])
        logs.append(
            f"[MEMORY] {len(context.retrieved_facts)} facts ranked via {context.vector_engine} + graph "
            f"in {context.retrieval_latency_ms}ms"
        )
        return {"memory_context": context, "logs": logs}

    def _evidence_step(self, state: ClanfallState) -> Dict[str, Any]:
        event = state["stored_event"]
        stage = "voting" if event.action == ActionType.VOTING_OPEN else "opening"
        evidence = self.evidence_agent.build(state["memory_context"], event.meeting_id or 0, stage)
        self.memory.working.add_evidence(evidence)
        self.memory.record_evidence(evidence)
        logs = state.get("logs", [])
        logs.append(f"[EVIDENCE] {evidence.headline} (score {evidence.final_score:.2f})")
        return {"evidence": evidence, "logs": logs}

    def run(self, event: GameEvent) -> ClanfallState:
        return self.graph.invoke({"event": event, "logs": []})

    def reset(self) -> Dict[str, Any]:
        self.memory_agent.last_context = None
        return self.memory.reset()
