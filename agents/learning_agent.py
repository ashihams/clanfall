from typing import List

from memory.match_memory import MatchMemory
from models import GameEvent, SemanticFact


class LearningAgent:
    """
    Learning Agent:
    In AeroCortex this consolidated experience ACROSS missions and reinforced
    rules over time. Clanfall deliberately removes that: consolidation is
    within-match only (episodic -> semantic facts + graph), every write targets
    the per-match stores in MatchMemory, and MatchMemory.reset() clears them all.
    There is no cross-match rule reinforcement.
    """

    def __init__(self, memory: MatchMemory):
        self.memory = memory

    def consolidate(self, event: GameEvent) -> List[SemanticFact]:
        if not event.is_public:
            return []
        self.memory.graph.add_event(event)
        facts = self.memory.semantic.derive(event, self.memory.episodic)
        if facts:
            self.memory.semantic.store(facts)
            self.memory.record_facts(facts)
            for fact in facts:
                self.memory.graph.add_fact(fact)
        return facts
