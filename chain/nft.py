"""ERC-721 Match Passport (stretch): one token per finished match, anchored to MatchLedger.

The contract only mints for a match the ledger has already revealed, with the exact
event-log hash recorded there. MockPassportNFT replicates those checks in-process.
"""
import base64
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from chain.hashing import keccak256, to_hex
from chain.ledger import (
    PHASE_REVEALED, LedgerError, TxReceipt, Web3Ledger, _map_error, _send, error_selectors, explorer_base, load_artifact,
)
from config import config

logger = logging.getLogger("clanfall.nft")

NFT_ERRORS = error_selectors([
    "MatchNotFinalized", "EventLogHashMismatch", "PassportAlreadyMinted",
    "OwnableUnauthorizedAccount(address)", "ERC721InvalidReceiver(address)",
])
# Inline small images (the outcome SVGs) straight into the token URI; anything
# bigger needs a hosted `image_url` in the sigil manifest, or on-chain storage gets expensive.
MAX_INLINE_IMAGE_BYTES = 8 * 1024


def _data_uri(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def sigil_image_uri(sigil: Dict[str, Any]) -> Optional[str]:
    if sigil.get("image_url"):
        return sigil["image_url"]
    path = Path(sigil["image_path"]) if sigil.get("image_path") else None
    if path is None or not path.exists() or path.stat().st_size > MAX_INLINE_IMAGE_BYTES:
        return None
    mime = "image/svg+xml" if path.suffix.lower() == ".svg" else (mimetypes.guess_type(path.name)[0] or "image/png")
    return _data_uri(mime, path.read_bytes())


def build_metadata(passport: Any) -> Dict[str, Any]:
    """ERC-721 metadata JSON for a (not yet NFT-stamped) MatchPassport."""
    p = passport
    metadata: Dict[str, Any] = {
        "name": f"Clanfall Match Passport {p.match_id[:8]}",
        "description": (
            f"{p.sigil.get('title', p.result)}: {p.reason}. Roles were committed on-chain before play and "
            f"revealed after; the event-log hash below is recorded in MatchLedger. Match memory was wiped "
            f"when this match ended."
        ),
        "attributes": [
            {"trait_type": "Result", "value": p.result},
            {"trait_type": "Scenario", "value": p.scenario},
            {"trait_type": "Duration (s)", "display_type": "number", "value": p.duration_s},
            {"trait_type": "Tasks", "display_type": "number", "value": p.task_count},
            {"trait_type": "Meetings", "display_type": "number", "value": p.meeting_count},
            {"trait_type": "Kills", "display_type": "number", "value": p.kill_count},
            {"trait_type": "Ejected", "value": ", ".join(p.ejections) or "nobody"},
            {"trait_type": "Players", "value": ", ".join(f"{s.player} ({s.role})" for s in p.players)},
            {"trait_type": "Event log hash", "value": p.event_log_hash},
            {"trait_type": "Match ID", "value": p.match_id},
            {"trait_type": "MatchLedger", "value": p.ledger.contract},
        ],
    }
    image = sigil_image_uri(p.sigil)
    if image:
        metadata["image"] = image
    return metadata


def token_uri(metadata: Dict[str, Any]) -> str:
    return _data_uri("application/json", json.dumps(metadata, separators=(",", ":")).encode())


def decode_token_uri(uri: str) -> Dict[str, Any]:
    prefix = "data:application/json;base64,"
    if not uri.startswith(prefix):
        raise ValueError("not a base64 JSON data URI")
    return json.loads(base64.b64decode(uri[len(prefix):]))


class MockPassportNFT:
    """In-process replica of MatchPassport.sol, bound to one ledger (mock or otherwise)."""

    mode = "mock"

    def __init__(self, ledger: Any, owner: str = "0xMOCKHOST"):
        self.ledger = ledger
        self.address = "mock://MatchPassport"
        self.owner = owner
        self.total_minted = 0
        self._owner_of: Dict[int, str] = {}
        self._uri: Dict[int, str] = {}
        self._token_of_match: Dict[bytes, int] = {}
        self._log_hash_of: Dict[int, bytes] = {}

    def mint(self, to: str, match_id: bytes, log_hash: bytes, uri: str) -> Dict[str, Any]:
        if self.ledger.get_match_phase(match_id) != PHASE_REVEALED:
            raise LedgerError("MatchNotFinalized")
        if self.ledger.get_event_log_hash(match_id) != log_hash:
            raise LedgerError("EventLogHashMismatch")
        if match_id in self._token_of_match:
            raise LedgerError("PassportAlreadyMinted")
        self.total_minted += 1
        token_id = self.total_minted
        self._token_of_match[match_id] = token_id
        self._owner_of[token_id] = to
        self._uri[token_id] = uri
        self._log_hash_of[token_id] = log_hash
        tx = to_hex(keccak256(f"mint:{to_hex(match_id)}:{token_id}".encode()))
        return {"token_id": token_id, "receipt": TxReceipt(tx_hash=tx, mode=self.mode, block_number=token_id)}

    def token_of_match(self, match_id: bytes) -> int:
        return self._token_of_match.get(match_id, 0)

    def owner_of(self, token_id: int) -> str:
        return self._owner_of[token_id]

    def token_uri(self, token_id: int) -> str:
        return self._uri[token_id]

    def event_log_hash_of(self, token_id: int) -> bytes:
        return self._log_hash_of.get(token_id, b"\x00" * 32)

    def token_url(self, token_id: int) -> Optional[str]:
        return None


class Web3PassportNFT:
    """MatchPassport.sol via web3.py. Shares the ledger's connection and signer."""

    mode = "testnet"

    def __init__(self, w3: Any, address: str, private_key: Optional[str] = None,
                 sender: Optional[str] = None, explorer_tx_url: str = ""):
        self.w3 = w3
        self.address = w3.to_checksum_address(address)
        self.contract = w3.eth.contract(address=self.address, abi=load_artifact("MatchPassport")["abi"])
        self.explorer_tx_url = explorer_tx_url
        self._key = private_key
        self.owner = w3.eth.account.from_key(private_key).address if private_key else (sender or w3.eth.accounts[0])

    @classmethod
    def for_ledger(cls, ledger: Web3Ledger, address: str) -> "Web3PassportNFT":
        key = ledger._account.key if ledger._account else None
        return cls(ledger.w3, address, private_key=key, sender=ledger.sender, explorer_tx_url=ledger.explorer_tx_url)

    @classmethod
    def deploy(cls, ledger: Web3Ledger) -> "Web3PassportNFT":
        art = load_artifact("MatchPassport")
        factory = ledger.w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
        key = ledger._account.key if ledger._account else None
        receipt = _send(ledger.w3, factory.constructor(ledger.address), key, None if key else ledger.sender)
        return cls.for_ledger(ledger, receipt["contractAddress"])

    def mint(self, to: str, match_id: bytes, log_hash: bytes, uri: str) -> Dict[str, Any]:
        fn = self.contract.functions.mintPassport(self.w3.to_checksum_address(to), match_id, log_hash, uri)
        try:
            receipt = _send(self.w3, fn, self._key, None if self._key else self.owner)
        except Exception as exc:
            raise _map_error(exc, NFT_ERRORS) from exc
        tx_hash = to_hex(bytes(receipt["transactionHash"]))
        from web3.logs import DISCARD

        minted = self.contract.events.PassportMinted().process_receipt(receipt, errors=DISCARD)
        token_id = int(minted[0]["args"]["tokenId"]) if minted else self.token_of_match(match_id)
        return {
            "token_id": token_id,
            "receipt": TxReceipt(
                tx_hash=tx_hash, mode=self.mode, block_number=receipt.get("blockNumber"),
                gas_used=receipt.get("gasUsed"),
                explorer_url=(self.explorer_tx_url + tx_hash) if self.explorer_tx_url else None,
            ),
        }

    def token_of_match(self, match_id: bytes) -> int:
        return int(self.contract.functions.tokenOfMatch(match_id).call())

    def owner_of(self, token_id: int) -> str:
        return self.contract.functions.ownerOf(token_id).call()

    def token_uri(self, token_id: int) -> str:
        return self.contract.functions.tokenURI(token_id).call()

    def event_log_hash_of(self, token_id: int) -> bytes:
        return bytes(self.contract.functions.eventLogHashOf(token_id).call())

    def token_url(self, token_id: int) -> Optional[str]:
        base = explorer_base(self.explorer_tx_url)
        return f"{base}nft/{self.address}/{token_id}" if base else None


def nft_for_ledger(ledger: Any) -> Optional[Any]:
    """Testnet NFT when the ledger is on-chain and an address is configured; otherwise a mock bound to the ledger."""
    if not config.chain.nft_enabled:
        return None
    if isinstance(ledger, Web3Ledger) and config.chain.passport_nft_address:
        try:
            return Web3PassportNFT.for_ledger(ledger, config.chain.passport_nft_address)
        except Exception as exc:
            logger.warning("Passport NFT contract unavailable, using mock: %s", exc)
    return MockPassportNFT(ledger)
