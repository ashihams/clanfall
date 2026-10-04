import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from chain.hashing import keccak256, role_commitment, to_hex
from config import config

logger = logging.getLogger("clanfall.ledger")

BUILD_DIR = Path(__file__).resolve().parent / "contracts" / "build"
BUILD_ARTIFACT = BUILD_DIR / "MatchLedger.json"

PHASE_NONE, PHASE_COMMITTED, PHASE_REVEALED = 0, 1, 2
PHASE_NAMES = {PHASE_NONE: "None", PHASE_COMMITTED: "Committed", PHASE_REVEALED: "Revealed"}

CONTRACT_ERRORS = [
    "MatchAlreadyExists", "MatchNotCommitted", "MatchAlreadyFinalized", "ArrayLengthMismatch",
    "SeatOutOfRange", "SeatAlreadyRevealed", "CommitmentMismatch", "NotAllSeatsRevealed", "EmptyCommitments",
]


def error_selectors(signatures: Sequence[str]) -> Dict[str, str]:
    """'Name()' or 'Name(address)' -> {4-byte selector hex: 'Name'}."""
    sigs = [s if "(" in s else f"{s}()" for s in signatures]
    return {keccak256(s.encode())[:4].hex(): s.split("(")[0] for s in sigs}


ERROR_SELECTORS = error_selectors(CONTRACT_ERRORS)


class LedgerError(Exception):
    """Mirrors MatchLedger.sol custom errors (same names in mock and testnet mode)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}{': ' + detail if detail else ''}")


@dataclass
class TxReceipt:
    tx_hash: str
    mode: str
    block_number: Optional[int] = None
    gas_used: Optional[int] = None
    explorer_url: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _MatchRecord:
    phase: int = PHASE_NONE
    host: str = ""
    commitments: List[bytes] = field(default_factory=list)
    revealed: List[bool] = field(default_factory=list)
    event_log_hash: bytes = b"\x00" * 32
    committed_at: int = 0
    revealed_at: int = 0


class MockLedger:
    """In-process replica of MatchLedger.sol — same checks, same error names, no chain."""

    mode = "mock"

    def __init__(self, host: str = "0xMOCKHOST"):
        self.address = "mock://MatchLedger"
        self.host = host
        self._matches: Dict[bytes, _MatchRecord] = {}
        self._nonce = 0
        self._block = 0

    def _tx(self, payload: Dict[str, Any], events: List[Dict[str, Any]]) -> TxReceipt:
        self._nonce += 1
        self._block += 1
        body = json.dumps({**payload, "nonce": self._nonce}, sort_keys=True, default=str).encode()
        return TxReceipt(tx_hash=to_hex(keccak256(body)), mode=self.mode, block_number=self._block, events=events)

    def commit_roles(self, match_id: bytes, commitments: Sequence[bytes]) -> TxReceipt:
        rec = self._matches.get(match_id)
        if rec is not None and rec.phase != PHASE_NONE:
            raise LedgerError("MatchAlreadyExists")
        if not commitments:
            raise LedgerError("EmptyCommitments")
        now = int(time.time())
        self._matches[match_id] = _MatchRecord(
            phase=PHASE_COMMITTED,
            host=self.host,
            commitments=list(commitments),
            revealed=[False] * len(commitments),
            committed_at=now,
        )
        return self._tx(
            {"fn": "commitRoles", "matchId": to_hex(match_id), "commitments": [to_hex(c) for c in commitments]},
            [{"event": "RolesCommitted", "matchId": to_hex(match_id), "playerCount": len(commitments)}],
        )

    def reveal_and_finalize(
        self,
        match_id: bytes,
        roles: Sequence[int],
        seats: Sequence[int],
        salts: Sequence[bytes],
        event_log_hash: bytes,
    ) -> TxReceipt:
        rec = self._matches.get(match_id)
        if rec is None or rec.phase == PHASE_NONE:
            raise LedgerError("MatchNotCommitted")
        if rec.phase == PHASE_REVEALED:
            raise LedgerError("MatchAlreadyFinalized")
        if not (len(roles) == len(seats) == len(salts)):
            raise LedgerError("ArrayLengthMismatch")

        # Solidity reverts the whole tx on any failure, so validate on a copy first.
        revealed = list(rec.revealed)
        events = []
        for role, seat, salt in zip(roles, seats, salts):
            if seat >= len(rec.commitments):
                raise LedgerError("SeatOutOfRange", f"seat {seat}")
            if revealed[seat]:
                raise LedgerError("SeatAlreadyRevealed", f"seat {seat}")
            if role_commitment(role, seat, salt, match_id) != rec.commitments[seat]:
                raise LedgerError("CommitmentMismatch", f"seat {seat}")
            revealed[seat] = True
            events.append({"event": "RoleVerified", "seat": seat, "role": role})
        if not all(revealed):
            raise LedgerError("NotAllSeatsRevealed")

        rec.revealed = revealed
        rec.event_log_hash = event_log_hash
        rec.phase = PHASE_REVEALED
        rec.revealed_at = int(time.time())
        events.append({"event": "MatchFinalized", "eventLogHash": to_hex(event_log_hash)})
        return self._tx(
            {"fn": "revealAndFinalize", "matchId": to_hex(match_id), "eventLogHash": to_hex(event_log_hash)},
            events,
        )

    def get_match_phase(self, match_id: bytes) -> int:
        rec = self._matches.get(match_id)
        return rec.phase if rec else PHASE_NONE

    def get_commitment(self, match_id: bytes, seat: int) -> bytes:
        rec = self._matches.get(match_id)
        if rec is None or seat >= len(rec.commitments):
            raise LedgerError("SeatOutOfRange")
        return rec.commitments[seat]

    def get_event_log_hash(self, match_id: bytes) -> bytes:
        rec = self._matches.get(match_id)
        return rec.event_log_hash if rec else b"\x00" * 32

    def verify_commitment(self, match_id: bytes, role: int, seat: int, salt: bytes) -> bool:
        return role_commitment(role, seat, salt, match_id) == self.get_commitment(match_id, seat)


def load_artifact(name: str = "MatchLedger") -> Dict[str, Any]:
    path = BUILD_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m chain.contracts.compile`")
    return json.loads(path.read_text(encoding="utf-8"))


def explorer_base(explorer_tx_url: str) -> str:
    """'https://sepolia.basescan.org/tx/' -> 'https://sepolia.basescan.org/'."""
    if not explorer_tx_url:
        return ""
    return explorer_tx_url[: -len("tx/")] if explorer_tx_url.endswith("tx/") else explorer_tx_url.rstrip("/") + "/"


class Web3Ledger:
    """MatchLedger.sol on a real EVM chain via web3.py (or an in-process EthereumTester)."""

    mode = "testnet"

    def __init__(self, w3: Any, address: str, private_key: Optional[str] = None,
                 sender: Optional[str] = None, explorer_tx_url: str = ""):
        self.w3 = w3
        self.address = w3.to_checksum_address(address)
        self.contract = w3.eth.contract(address=self.address, abi=load_artifact()["abi"])
        self.explorer_tx_url = explorer_tx_url
        self._account = None
        if private_key:
            self._account = w3.eth.account.from_key(private_key)
            self.sender = self._account.address
        else:
            self.sender = sender or w3.eth.accounts[0]

    @classmethod
    def from_config(cls) -> "Web3Ledger":
        from web3 import Web3

        c = config.chain
        if not (c.rpc_url and c.private_key and c.ledger_address):
            raise RuntimeError("CHAIN_RPC_URL, CHAIN_PRIVATE_KEY and MATCH_LEDGER_ADDRESS must all be set")
        w3 = Web3(Web3.HTTPProvider(c.rpc_url, request_kwargs={"timeout": 30}))
        if not w3.is_connected():
            raise RuntimeError(f"cannot reach {c.rpc_url}")
        return cls(w3, c.ledger_address, private_key=c.private_key, explorer_tx_url=c.explorer_tx_url)

    @classmethod
    def deploy(cls, w3: Any, private_key: Optional[str] = None, sender: Optional[str] = None,
               explorer_tx_url: str = "") -> "Web3Ledger":
        art = load_artifact()
        factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
        receipt = _send(w3, factory.constructor(), private_key, sender or (None if private_key else w3.eth.accounts[0]))
        return cls(w3, receipt["contractAddress"], private_key=private_key, sender=sender,
                   explorer_tx_url=explorer_tx_url)

    def _send(self, fn) -> TxReceipt:
        try:
            receipt = _send(self.w3, fn, self._account.key if self._account else None, self.sender)
        except Exception as exc:
            raise _map_error(exc) from exc
        tx_hash = to_hex(bytes(receipt["transactionHash"]))
        return TxReceipt(
            tx_hash=tx_hash,
            mode=self.mode,
            block_number=receipt.get("blockNumber"),
            gas_used=receipt.get("gasUsed"),
            explorer_url=(self.explorer_tx_url + tx_hash) if self.explorer_tx_url else None,
        )

    def commit_roles(self, match_id: bytes, commitments: Sequence[bytes]) -> TxReceipt:
        receipt = self._send(self.contract.functions.commitRoles(match_id, list(commitments)))
        eventually(lambda: self.get_match_phase(match_id), lambda p: p == PHASE_COMMITTED)
        return receipt

    def reveal_and_finalize(self, match_id: bytes, roles: Sequence[int], seats: Sequence[int],
                            salts: Sequence[bytes], event_log_hash: bytes) -> TxReceipt:
        receipt = self._send(self.contract.functions.revealAndFinalize(
            match_id, list(roles), list(seats), list(salts), event_log_hash
        ))
        eventually(lambda: self.get_match_phase(match_id), lambda p: p == PHASE_REVEALED)
        return receipt

    def _call(self, fn):
        try:
            return fn.call()
        except Exception as exc:
            raise _map_error(exc) from exc

    def get_match_phase(self, match_id: bytes) -> int:
        return int(self._call(self.contract.functions.getMatchPhase(match_id)))

    def get_commitment(self, match_id: bytes, seat: int) -> bytes:
        return bytes(self._call(self.contract.functions.getCommitment(match_id, seat)))

    def get_event_log_hash(self, match_id: bytes) -> bytes:
        return bytes(self._call(self.contract.functions.getEventLogHash(match_id)))

    def verify_commitment(self, match_id: bytes, role: int, seat: int, salt: bytes) -> bool:
        return bool(self._call(self.contract.functions.verifyCommitment(match_id, role, seat, salt)))


def eventually(read: Any, ok: Any, attempts: int = 10, delay_s: float = 1.0) -> Any:
    """Re-read until ok(value). Public RPCs are load-balanced, so a read right after a tx can hit a stale node."""
    value = read()
    for _ in range(attempts - 1):
        if ok(value):
            break
        time.sleep(delay_s)
        value = read()
    return value


_last_nonce: Dict[Any, int] = {}


def _next_nonce(w3: Any, address: str) -> int:
    """Never reuse a nonce, even if a stale RPC node reports an old transaction count."""
    key = (id(w3.provider), address)
    nonce = max(w3.eth.get_transaction_count(address, "pending"), _last_nonce.get(key, -1) + 1)
    _last_nonce[key] = nonce
    return nonce


def _send(w3: Any, fn: Any, private_key: Optional[str], sender: Optional[str],
          gas: Optional[int] = None) -> Dict[str, Any]:
    if private_key:
        account = w3.eth.account.from_key(private_key)
        params = {
            "from": account.address,
            "nonce": _next_nonce(w3, account.address),
            "chainId": w3.eth.chain_id,
        }
        if gas:
            params["gas"] = gas
        tx = fn.build_transaction(params)
        signed = account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw)
    else:
        tx_hash = fn.transact({"from": sender})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.get("status") == 0:
        raise LedgerError("TransactionReverted", to_hex(bytes(tx_hash)))
    return receipt


def _map_error(exc: Exception, selectors: Optional[Dict[str, str]] = None) -> Exception:
    if isinstance(exc, LedgerError):
        return exc
    selectors = selectors or ERROR_SELECTORS
    parts = [repr(exc), str(getattr(exc, "data", "") or "")]
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, (bytes, bytearray)):
            parts.append(bytes(arg).hex())
        elif isinstance(arg, str) and "b'" in arg:
            try:
                import ast
                parts.append(ast.literal_eval(arg[arg.index("b'"):]).hex())
            except Exception:
                pass
    text = " ".join(parts)
    for selector, name in selectors.items():
        if selector in text:
            return LedgerError(name)
    return exc


def send_value(w3: Any, to: str, value_wei: int, private_key: Optional[str], sender: Optional[str]) -> Dict[str, Any]:
    """Plain ETH transfer (used to cover gas for a player's own opt-in transaction)."""
    if private_key:
        account = w3.eth.account.from_key(private_key)
        tx = {
            "to": w3.to_checksum_address(to),
            "value": value_wei,
            "gas": 21000,
            "gasPrice": w3.eth.gas_price,
            "nonce": _next_nonce(w3, account.address),
            "chainId": w3.eth.chain_id,
        }
        signed = account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw)
    else:
        tx_hash = w3.eth.send_transaction({"from": sender, "to": w3.to_checksum_address(to), "value": value_wei})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.get("status") == 0:
        raise LedgerError("TransactionReverted", to_hex(bytes(tx_hash)))
    return receipt


_ledger: Optional[Any] = None


def get_ledger() -> Any:
    """Testnet ledger when fully configured and reachable, otherwise the in-process mock."""
    global _ledger
    if _ledger is not None:
        return _ledger
    if config.chain.mode == "testnet":
        try:
            _ledger = Web3Ledger.from_config()
            logger.info("MatchLedger on chain %s at %s", config.chain.chain_id, _ledger.address)
            return _ledger
        except Exception as exc:
            logger.warning("Testnet ledger unavailable, using mock ledger: %s", exc)
    _ledger = MockLedger()
    return _ledger


def phase_name(phase: int) -> str:
    return PHASE_NAMES.get(phase, str(phase))
