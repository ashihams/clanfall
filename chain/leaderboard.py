"""Opt-in leaderboard (stretch). Deliberately separate from match memory.

Match memory is wiped every match and nothing here reads it. A player who wants a
public record opts in explicitly:

1. Their per-match wallet signs a consent message naming the profile address they
   want to publish under (per-match wallets are throwaway, so reputation needs a
   wallet the player keeps).
2. The host checks that signature against the revealed Player Passport and signs an
   attestation of the result (won/lost) for that profile address.
3. The profile wallet itself sends `optInAndSubmit`; the contract checks the match is
   revealed on MatchLedger, the attestation is from the host, and it's not a replay.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from eth_account import Account
from eth_account.messages import encode_defunct

from chain.hashing import keccak256, to_hex
from chain.ledger import (
    PHASE_REVEALED, LedgerError, TxReceipt, Web3Ledger, _map_error, _send, error_selectors, eventually, load_artifact,
    send_value,
)
from chain.passport import Wallet
from config import config

logger = logging.getLogger("clanfall.leaderboard")

LEADERBOARD_ERRORS = error_selectors(["MatchNotFinalized", "AlreadySubmitted", "InvalidAttestation"])
MOCK_LEADERBOARD_ADDRESS = Account.from_key(keccak256(b"clanfall-mock-leaderboard")).address


def result_digest(contract: str, chain_id: int, match_id: bytes, player: str, won: bool) -> bytes:
    """keccak256(abi.encodePacked(address(this), block.chainid, matchId, player, won))."""
    return keccak256(
        bytes.fromhex(contract[2:]) + chain_id.to_bytes(32, "big") + match_id
        + bytes.fromhex(player[2:]) + (b"\x01" if won else b"\x00")
    )


def sign_attestation(attestor_key: Any, contract: str, chain_id: int, match_id: bytes, player: str, won: bool) -> str:
    digest = result_digest(contract, chain_id, match_id, player, won)
    signed = Account.sign_message(encode_defunct(primitive=digest), private_key=attestor_key)
    return "0x" + signed.signature.hex().removeprefix("0x")


def recover_attestor(signature: str, contract: str, chain_id: int, match_id: bytes, player: str, won: bool) -> str:
    digest = result_digest(contract, chain_id, match_id, player, won)
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)


def consent_message(match_id: str, profile_address: str) -> str:
    return f"Clanfall: I choose to publish my result for match {match_id} under {profile_address}"


def sign_consent(match_wallet: Wallet, match_id: str, profile_address: str) -> str:
    signed = Account.sign_message(encode_defunct(text=consent_message(match_id, profile_address)),
                                  private_key=match_wallet.private_key)
    return "0x" + signed.signature.hex().removeprefix("0x")


def consent_signer(signature: str, match_id: str, profile_address: str) -> str:
    return Account.recover_message(encode_defunct(text=consent_message(match_id, profile_address)), signature=signature)


class MockLeaderboard:
    """In-process replica of ClanfallLeaderboard.sol (same checks and error names)."""

    mode = "mock"

    def __init__(self, ledger: Any, attestor: Optional[Wallet] = None):
        self.ledger = ledger
        self.address = MOCK_LEADERBOARD_ADDRESS
        self.chain_id = config.chain.chain_id
        self._attestor = attestor or Wallet.generate()
        self.attestor = self._attestor.address
        self._records: Dict[str, Dict[str, int]] = {}
        self._submitted: set = set()
        self._players: List[str] = []
        self._nonce = 0

    def attest(self, match_id: bytes, player: str, won: bool) -> str:
        return sign_attestation(self._attestor.private_key, self.address, self.chain_id, match_id, player, won)

    def submit(self, player_wallet: Wallet, match_id: bytes, won: bool, attestation: str) -> Dict[str, Any]:
        player = player_wallet.address
        if self.ledger.get_match_phase(match_id) != PHASE_REVEALED:
            raise LedgerError("MatchNotFinalized")
        if (match_id, player) in self._submitted:
            raise LedgerError("AlreadySubmitted")
        try:
            signer = recover_attestor(attestation, self.address, self.chain_id, match_id, player, won)
        except Exception:
            signer = None
        if signer != self.attestor:
            raise LedgerError("InvalidAttestation")
        self._submitted.add((match_id, player))
        rec = self._records.setdefault(player, {"wins": 0, "losses": 0, "last_submitted_at": 0})
        if player not in self._players:
            self._players.append(player)
        rec["wins" if won else "losses"] += 1
        rec["last_submitted_at"] = int(time.time())
        self._nonce += 1
        tx = to_hex(keccak256(f"optIn:{player}:{to_hex(match_id)}:{self._nonce}".encode()))
        return {"receipt": TxReceipt(tx_hash=tx, mode=self.mode, block_number=self._nonce), "topup": None,
                "record": self.get_record(player)}

    def get_record(self, player: str) -> Dict[str, int]:
        return dict(self._records.get(player, {"wins": 0, "losses": 0, "last_submitted_at": 0}))

    def has_submitted(self, match_id: bytes, player: str) -> bool:
        return (match_id, player) in self._submitted

    def standings(self) -> List[Dict[str, Any]]:
        return [{"address": a, **self.get_record(a)} for a in self._players]


class Web3Leaderboard:
    """ClanfallLeaderboard.sol via web3.py. The host key signs attestations and funds opt-in gas."""

    mode = "testnet"

    def __init__(self, w3: Any, address: str, host_key: Optional[Any] = None, host_sender: Optional[str] = None,
                 attestor_key: Optional[Any] = None, explorer_tx_url: str = ""):
        self.w3 = w3
        self.address = w3.to_checksum_address(address)
        self.contract = w3.eth.contract(address=self.address, abi=load_artifact("ClanfallLeaderboard")["abi"])
        self.chain_id = w3.eth.chain_id
        self.explorer_tx_url = explorer_tx_url
        self._host_key = host_key
        self._host_sender = host_sender
        self._attestor_key = attestor_key if attestor_key is not None else host_key
        self._attestor: Optional[str] = None

    @property
    def attestor(self) -> str:
        # Public RPCs are load-balanced; a node may not have seen a just-deployed contract yet.
        for attempt in range(5):
            if self._attestor:
                break
            try:
                self._attestor = self.contract.functions.attestor().call()
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2)
        return self._attestor

    @classmethod
    def for_ledger(cls, ledger: Web3Ledger, address: str, attestor_key: Optional[Any] = None) -> "Web3Leaderboard":
        key = ledger._account.key if ledger._account else None
        return cls(ledger.w3, address, host_key=key, host_sender=ledger.sender,
                   attestor_key=attestor_key, explorer_tx_url=ledger.explorer_tx_url)

    @classmethod
    def deploy(cls, ledger: Web3Ledger, attestor: Optional[Wallet] = None) -> "Web3Leaderboard":
        """Attestor defaults to the host account that deploys (the same key that commits matches)."""
        art = load_artifact("ClanfallLeaderboard")
        factory = ledger.w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
        key = ledger._account.key if ledger._account else None
        attestor_address = attestor.address if attestor else ledger.sender
        receipt = _send(ledger.w3, factory.constructor(ledger.address, attestor_address), key, None if key else ledger.sender)
        return cls.for_ledger(ledger, receipt["contractAddress"], attestor_key=attestor.private_key if attestor else None)

    def _tx(self, receipt: Dict[str, Any]) -> TxReceipt:
        tx_hash = to_hex(bytes(receipt["transactionHash"]))
        return TxReceipt(tx_hash=tx_hash, mode=self.mode, block_number=receipt.get("blockNumber"),
                         gas_used=receipt.get("gasUsed"),
                         explorer_url=(self.explorer_tx_url + tx_hash) if self.explorer_tx_url else None)

    def attest(self, match_id: bytes, player: str, won: bool) -> str:
        if self._attestor_key is None:
            raise LedgerError("NoAttestorKey", "this process does not hold the leaderboard attestor key")
        return sign_attestation(self._attestor_key, self.address, self.chain_id, match_id, player, won)

    def _precheck(self, match_id: bytes, player: str, won: bool, attestation: str) -> None:
        """Same checks as optInAndSubmit, via views, so nothing is spent on a tx that would revert."""
        ledger = self.w3.eth.contract(address=self.contract.functions.ledger().call(), abi=load_artifact()["abi"])
        if int(ledger.functions.getMatchPhase(match_id).call()) != PHASE_REVEALED:
            raise LedgerError("MatchNotFinalized")
        if self.has_submitted(match_id, player):
            raise LedgerError("AlreadySubmitted")
        try:
            signer = recover_attestor(attestation, self.address, self.chain_id, match_id, player, won)
        except Exception:
            signer = None
        if signer != self.attestor:
            raise LedgerError("InvalidAttestation")

    def _gas_limit(self, fn: Any, sender: str) -> int:
        try:
            return int(fn.estimate_gas({"from": sender}) * 1.3)
        except Exception:
            return 200_000

    def _max_gas_price(self) -> int:
        base = self.w3.eth.get_block("latest").get("baseFeePerGas", 0) or 0
        try:
            tip = self.w3.eth.max_priority_fee
        except Exception:
            tip = 0
        return max(2 * base + tip, self.w3.eth.gas_price)

    def submit(self, player_wallet: Wallet, match_id: bytes, won: bool, attestation: str) -> Dict[str, Any]:
        self._precheck(match_id, player_wallet.address, won, attestation)
        fn = self.contract.functions.optInAndSubmit(match_id, won, bytes.fromhex(attestation.removeprefix("0x")))
        gas = self._gas_limit(fn, player_wallet.address)
        budget = gas * self._max_gas_price()
        topup = None
        balance = self.w3.eth.get_balance(player_wallet.address)
        if balance < budget:
            amount = max(self.w3.to_wei(config.chain.leaderboard_gas_topup_eth, "ether"), 2 * budget) - balance
            topup = self._tx(send_value(self.w3, player_wallet.address, amount, self._host_key, self._host_sender))
            eventually(lambda: self.w3.eth.get_balance(player_wallet.address), lambda b: b >= budget)
        try:
            receipt = _send(self.w3, fn, player_wallet.private_key, None, gas=gas)
        except Exception as exc:
            raise _map_error(exc, LEADERBOARD_ERRORS) from exc
        from web3.logs import DISCARD

        events = self.contract.events.ResultSubmitted().process_receipt(receipt, errors=DISCARD)
        record = None
        if events:
            args = events[0]["args"]
            record = {"wins": int(args["wins"]), "losses": int(args["losses"]), "last_submitted_at": int(time.time())}
        return {"receipt": self._tx(receipt), "topup": topup, "record": record}

    def get_record(self, player: str) -> Dict[str, int]:
        wins, losses, last = self.contract.functions.getRecord(self.w3.to_checksum_address(player)).call()
        return {"wins": int(wins), "losses": int(losses), "last_submitted_at": int(last)}

    def has_submitted(self, match_id: bytes, player: str) -> bool:
        return bool(self.contract.functions.hasSubmitted(match_id, self.w3.to_checksum_address(player)).call())

    def standings(self) -> List[Dict[str, Any]]:
        count = int(self.contract.functions.playerCount().call())
        out = []
        for i in range(count):
            addr = self.contract.functions.playerAt(i).call()
            out.append({"address": addr, **self.get_record(addr)})
        return out


def leaderboard_for_ledger(ledger: Any) -> Any:
    if isinstance(ledger, Web3Ledger) and config.chain.leaderboard_address:
        try:
            return Web3Leaderboard.for_ledger(ledger, config.chain.leaderboard_address)
        except Exception as exc:
            logger.warning("Leaderboard contract unavailable, using mock: %s", exc)
    return MockLeaderboard(ledger)


class ProfileStore:
    """
    Persistent wallets for players who opted in, keyed by player name. Created only on a
    player's first opt-in. Lives outside match memory (and outside the wipe) on purpose;
    in a real game the player would bring their own wallet instead.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._wallets: Dict[str, Wallet] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._wallets = {name: Wallet(**w) for name, w in data.items()}

    def get(self, name: str) -> Optional[Wallet]:
        return self._wallets.get(name)

    def get_or_create(self, name: str) -> Wallet:
        if name not in self._wallets:
            self._wallets[name] = Wallet.generate()
            self._save()
        return self._wallets[name]

    def names_by_address(self) -> Dict[str, str]:
        return {w.address: name for name, w in self._wallets.items()}

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({n: {"address": w.address, "private_key": w.private_key}
                                         for n, w in self._wallets.items()}, indent=2), encoding="utf-8")
