import time
from typing import Any, Dict, List, Optional

from chain.ledger import MockLedger


class MockMemoryPipeline:
    """In-memory stand-in for memory pipeline evidence retrieval and event logging."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def ingest_event(self, event_type: str, actor: Optional[str], target: Optional[str], location: Optional[str] = None) -> Dict[str, Any]:
        record = {
            "seq": len(self.events) + 1,
            "timestamp": time.time(),
            "action": event_type,
            "actor": actor,
            "target": target,
            "location": location or "Cafeteria",
        }
        self.events.append(record)
        return record

    def retrieve_evidence(self, query: str = "", player: Optional[str] = None) -> str:
        if not self.events:
            return "No suspicious activity observed prior to the meeting."

        kills = [e for e in self.events if e["action"] == "kill"]
        if kills:
            last_kill = kills[-1]
            vic = last_kill.get("target") or "a crewmate"
            loc = last_kill.get("location") or "the station"
            return f"Evidence surface: Body of {vic} discovered near {loc}. Suspect movements detected nearby."

        return "Evidence surface: Audio logs indicate movement near Electrical shortly before emergency alarm."

    def reset(self) -> Dict[str, Any]:
        count = len(self.events)
        self.events.clear()
        return {"wiped": True, "count": count}


# Global singleton instances for mock mode
MOCK_CHAIN = MockLedger()
MOCK_MEMORY = MockMemoryPipeline()
