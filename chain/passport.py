import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from eth_account import Account
from eth_account.messages import encode_defunct
from pydantic import BaseModel, Field

SIGIL_DIR = Path(__file__).resolve().parent.parent / "assets" / "sigils"


@dataclass
class Wallet:
    """Throwaway per-match keypair. No funds, never reused across matches."""
    address: str
    private_key: str

    @classmethod
    def generate(cls) -> "Wallet":
        acct = Account.create()
        return cls(address=acct.address, private_key=acct.key.hex())


def _canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_payload(payload: Dict[str, Any], private_key: str) -> str:
    signed = Account.sign_message(encode_defunct(text=_canonical(payload)), private_key=private_key)
    return "0x" + signed.signature.hex().removeprefix("0x")


def recover_signer(payload: Dict[str, Any], signature: str) -> str:
    return Account.recover_message(encode_defunct(text=_canonical(payload)), signature=signature)


class PlayerPassport(BaseModel):
    match_id: str
    player: str
    seat: int
    wallet_address: str
    role_commitment: str
    role: Optional[str] = None
    issued_at: float = Field(default_factory=time.time)
    signature: str = ""

    def unsigned(self) -> Dict[str, Any]:
        return self.model_dump(exclude={"signature"})

    def signed_by(self, wallet: Wallet) -> "PlayerPassport":
        return self.model_copy(update={"signature": sign_payload(self.unsigned(), wallet.private_key)})

    def verify(self) -> bool:
        try:
            return recover_signer(self.unsigned(), self.signature) == self.wallet_address
        except Exception:
            return False


class RevealedSeat(BaseModel):
    seat: int
    player: str
    wallet_address: str
    role: str
    commitment: str
    salt: str
    verified_locally: bool
    verified_on_ledger: bool


class LedgerRecord(BaseModel):
    mode: str
    contract: str
    match_id_bytes32: str
    commit_tx: str
    reveal_tx: str
    commit_explorer_url: Optional[str] = None
    reveal_explorer_url: Optional[str] = None
    final_phase: str = ""
    on_chain_event_log_hash: str = ""


class NFTRecord(BaseModel):
    mode: str
    contract: str
    token_id: int
    owner: str
    mint_tx: str
    mint_explorer_url: Optional[str] = None
    token_url: Optional[str] = None
    metadata_sha3: str = ""


class MatchPassport(BaseModel):
    match_id: str
    scenario: str
    players: List[RevealedSeat]
    result: str
    reason: str
    duration_s: float
    task_count: int
    meeting_count: int
    kill_count: int
    ejections: List[str]
    event_count: int
    event_log_hash: str
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    ledger: LedgerRecord
    sigil: Dict[str, Any] = Field(default_factory=dict)
    nft: Optional[NFTRecord] = None
    nft_error: Optional[str] = None
    host_address: str = ""
    issued_at: float = Field(default_factory=time.time)
    signature: str = ""

    def unsigned(self) -> Dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"signature"})
        # Omit unset NFT fields so passports signed before the NFT existed still verify.
        for key in ("nft", "nft_error"):
            if data.get(key) is None:
                data.pop(key, None)
        return data

    def signed_by(self, wallet: Wallet) -> "MatchPassport":
        updated = self.model_copy(update={"host_address": wallet.address})
        return updated.model_copy(update={"signature": sign_payload(updated.unsigned(), wallet.private_key)})

    def verify_signature(self) -> bool:
        try:
            return recover_signer(self.unsigned(), self.signature) == self.host_address
        except Exception:
            return False


def select_sigil(result: str) -> Dict[str, Any]:
    """Pick the static outcome image for this result from assets/sigils/manifest.json."""
    manifest_path = SIGIL_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    entry = dict(manifest.get(result) or manifest.get("UNRESOLVED") or {})
    if entry.get("image"):
        entry["image_path"] = str(SIGIL_DIR / entry["image"])
    entry["result"] = result
    return entry
