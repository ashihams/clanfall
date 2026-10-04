"""Independently verify an exported match record.

    python -m chain.verify data/passports/<match_id>
    python -m chain.verify data/passports/<match_id> --onchain   # also read the ledger contract (testnet mode)

Checks: event_log.json hashes to the passport's event_log_hash, every revealed
(role, seat, salt) reopens its commitment, the host and player signatures are
valid, the NFT metadata matches the passport, and (optionally) the on-chain
ledger and NFT records match.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

from chain.commit_reveal import verify_commitment
from chain.hashing import event_log_hash, from_hex, keccak256, to_hex
from chain.nft import token_uri
from chain.passport import MatchPassport, PlayerPassport
from models import Role


def verify_record(folder: Path, onchain: bool = False) -> List[Tuple[str, bool, str]]:
    passport = MatchPassport(**json.loads((folder / "match_passport.json").read_text(encoding="utf-8")))
    players = [PlayerPassport(**p) for p in json.loads((folder / "player_passports.json").read_text(encoding="utf-8"))]
    log_bytes = (folder / "event_log.json").read_bytes()

    checks: List[Tuple[str, bool, str]] = []
    recomputed = to_hex(event_log_hash(log_bytes))
    checks.append(("event log hash", recomputed == passport.event_log_hash, recomputed))
    checks.append(("host signature", passport.verify_signature(), passport.host_address))
    for seat in passport.players:
        ok = verify_commitment(passport.match_id, int(Role[seat.role]), seat.seat, from_hex(seat.salt), from_hex(seat.commitment))
        checks.append((f"seat {seat.seat} {seat.player} = {seat.role}", ok, seat.commitment))
    for p in players:
        checks.append((f"player passport {p.player}", p.verify(), p.wallet_address))

    nft_meta_path = folder / "nft_metadata.json"
    if passport.nft and nft_meta_path.exists():
        metadata = json.loads(nft_meta_path.read_text(encoding="utf-8"))
        meta_hash = to_hex(keccak256(token_uri(metadata).encode()))
        checks.append(("NFT metadata hash", meta_hash == passport.nft.metadata_sha3, meta_hash))
        attr = {a["trait_type"]: a["value"] for a in metadata.get("attributes", [])}
        checks.append(("NFT carries event log hash", attr.get("Event log hash") == passport.event_log_hash,
                       f"token #{passport.nft.token_id}"))

    if onchain:
        from chain.ledger import Web3Ledger

        ledger = Web3Ledger.from_config()
        mid = from_hex(passport.ledger.match_id_bytes32)
        on_hash = to_hex(ledger.get_event_log_hash(mid))
        checks.append(("on-chain event log hash", on_hash == passport.event_log_hash, on_hash))
        for seat in passport.players:
            ok = ledger.verify_commitment(mid, int(Role[seat.role]), seat.seat, from_hex(seat.salt))
            checks.append((f"on-chain seat {seat.seat}", ok, ledger.address))
        if passport.nft and passport.nft.mode == "testnet":
            from chain.nft import Web3PassportNFT

            nft = Web3PassportNFT.for_ledger(ledger, passport.nft.contract)
            tid = passport.nft.token_id
            checks.append(("on-chain NFT token for match", nft.token_of_match(mid) == tid, f"token #{tid}"))
            nft_hash = to_hex(nft.event_log_hash_of(tid))
            checks.append(("on-chain NFT event log hash", nft_hash == passport.event_log_hash, nft_hash))
            uri_hash = to_hex(keccak256(nft.token_uri(tid).encode()))
            checks.append(("on-chain NFT metadata", uri_hash == passport.nft.metadata_sha3, nft.address))
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--onchain", action="store_true")
    args = parser.parse_args(argv)
    checks = verify_record(args.folder, onchain=args.onchain)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<34} {detail}")
    all_ok = all(ok for _, ok, _ in checks)
    print("\nVERIFIED" if all_ok else "\nVERIFICATION FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
