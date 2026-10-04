import logging
from typing import Any, List, Optional, Sequence

from chain.hashing import match_id_to_bytes32, to_hex
from chain.ledger import MockLedger, get_ledger

logger = logging.getLogger("clanfall.bridge.ledger")


class MatchLedgerClient:
    """Bridge wrapper for MatchLedger contract interactions (mock or testnet)."""

    def __init__(self, ledger: Optional[Any] = None):
        self._ledger = ledger or get_ledger()

    @property
    def mode(self) -> str:
        return getattr(self._ledger, "mode", "mock")

    @property
    def address(self) -> str:
        return getattr(self._ledger, "address", "mock://MatchLedger")

    def commit_roles(self, match_id: str, commitments: Sequence[bytes]) -> Any:
        match_id_b32 = match_id_to_bytes32(match_id)
        return self._ledger.commit_roles(match_id_b32, commitments)

    def reveal_and_finalize(
        self,
        match_id: str,
        roles: Sequence[int],
        seats: Sequence[int],
        salts: Sequence[bytes],
        event_log_hash: bytes,
    ) -> Any:
        match_id_b32 = match_id_to_bytes32(match_id)
        return self._ledger.reveal_and_finalize(match_id_b32, roles, seats, salts, event_log_hash)

    def verify_commitment(self, match_id: str, role: int, seat: int, salt: bytes) -> bool:
        match_id_b32 = match_id_to_bytes32(match_id)
        return self._ledger.verify_commitment(match_id_b32, role, seat, salt)
