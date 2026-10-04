import os

# Keep tests offline and fast: no Groq, no Tencent, mock ledger, in-memory vectors by default.
os.environ["GROQ_ENABLED"] = "false"
os.environ["CHAIN_MODE"] = "mock"
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ["API_KEY"] = ""
os.environ.pop("TENCENT_VDB_URL", None)
os.environ.pop("TENCENT_VDB_KEY", None)
os.environ["PINECONE_ENABLED"] = "false"
os.environ["NEO4J_ENABLED"] = "false"
os.environ["MONGO_ENABLED"] = "false"

import pytest  # noqa: E402


@pytest.fixture
def orchestrator(tmp_path):
    from chain.ledger import MockLedger
    from orchestration.match_runner import MatchOrchestrator

    return MatchOrchestrator(ledger=MockLedger(), export_dir=tmp_path / "passports")
