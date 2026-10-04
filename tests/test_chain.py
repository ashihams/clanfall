import json
import secrets

import pytest

from chain.commit_reveal import RoleBook, verify_commitment
from chain.hashing import encode_packed_commitment, event_log_hash, keccak256, match_id_to_bytes32, to_hex
from chain.ledger import BUILD_ARTIFACT, LedgerError, MockLedger, PHASE_COMMITTED, PHASE_REVEALED
from chain.passport import PlayerPassport, Wallet
from chain.verify import verify_record

PLAYERS = ["Red", "Blue", "Green", "Yellow"]


def test_encode_packed_layout():
    salt, mid = b"\x11" * 32, b"\x22" * 32
    packed = encode_packed_commitment(1, 3, salt, mid)
    assert len(packed) == 1 + 32 + 32 + 32
    assert packed[0] == 1 and packed[1:33] == (3).to_bytes(32, "big")
    assert packed[33:65] == salt and packed[65:] == mid


def test_role_book_hides_salts_until_reveal():
    book = RoleBook("m1", PLAYERS, ["Blue"])
    assert all(set(c) == {"seat", "player", "commitment"} for c in book.public_view())
    with pytest.raises(PermissionError):
        book.assignments()
    roles, seats, salts = book.reveal()
    assert roles == [0, 1, 0, 0] and seats == [0, 1, 2, 3]
    for a in book.assignments():
        assert verify_commitment("m1", int(a.role), a.seat, a.salt, a.commitment)
        assert not verify_commitment("m1", 1 - int(a.role), a.seat, a.salt, a.commitment)


def _committed(ledger):
    book = RoleBook("m-ledger", PLAYERS, ["Blue"])
    ledger.commit_roles(book.match_id_b32, book.commitments)
    return book


def _ledger_cases():
    yield "mock", MockLedger()
    if BUILD_ARTIFACT.exists():
        try:
            from web3 import EthereumTesterProvider, Web3
            from chain.ledger import Web3Ledger

            yield "evm", Web3Ledger.deploy(Web3(EthereumTesterProvider()))
        except ImportError:
            pass


@pytest.fixture(params=[c[0] for c in _ledger_cases()])
def ledger(request):
    return dict(_ledger_cases())[request.param]


def test_ledger_happy_path(ledger):
    book = _committed(ledger)
    assert ledger.get_match_phase(book.match_id_b32) == PHASE_COMMITTED
    roles, seats, salts = book.reveal()
    log_hash = keccak256(b"event log")
    ledger.reveal_and_finalize(book.match_id_b32, roles, seats, salts, log_hash)
    assert ledger.get_match_phase(book.match_id_b32) == PHASE_REVEALED
    assert ledger.get_event_log_hash(book.match_id_b32) == log_hash
    assert all(ledger.verify_commitment(book.match_id_b32, r, s, salt) for r, s, salt in zip(roles, seats, salts))


@pytest.mark.parametrize("tamper, code", [
    (lambda r, s, salt: ([1, 0, 0, 0], s, salt), "CommitmentMismatch"),       # swap who the impostor was
    (lambda r, s, salt: (r, s, [secrets.token_bytes(32)] + salt[1:]), "CommitmentMismatch"),
    (lambda r, s, salt: (r[:3], s[:3], salt[:3]), "NotAllSeatsRevealed"),
    (lambda r, s, salt: (r, s, salt[:3]), "ArrayLengthMismatch"),
    (lambda r, s, salt: (r + [0], s + [9], salt + [salt[0]]), "SeatOutOfRange"),
])
def test_ledger_rejects_tampered_reveals(ledger, tamper, code):
    book = _committed(ledger)
    roles, seats, salts = tamper(*book.reveal())
    with pytest.raises(LedgerError) as err:
        ledger.reveal_and_finalize(book.match_id_b32, roles, seats, salts, b"\x00" * 32)
    assert err.value.code == code
    assert ledger.get_match_phase(book.match_id_b32) == PHASE_COMMITTED


def test_ledger_lifecycle_errors(ledger):
    mid = match_id_to_bytes32("never-committed")
    with pytest.raises(LedgerError) as err:
        ledger.reveal_and_finalize(mid, [0], [0], [b"\x00" * 32], b"\x00" * 32)
    assert err.value.code == "MatchNotCommitted"

    book = _committed(ledger)
    with pytest.raises(LedgerError) as err:
        ledger.commit_roles(book.match_id_b32, book.commitments)
    assert err.value.code == "MatchAlreadyExists"
    roles, seats, salts = book.reveal()
    ledger.reveal_and_finalize(book.match_id_b32, roles, seats, salts, b"\x01" * 32)
    with pytest.raises(LedgerError) as err:
        ledger.reveal_and_finalize(book.match_id_b32, roles, seats, salts, b"\x01" * 32)
    assert err.value.code == "MatchAlreadyFinalized"


def test_full_match_on_evm(tmp_path):
    pytest.importorskip("eth_tester")
    from web3 import EthereumTesterProvider, Web3
    from chain.ledger import Web3Ledger
    from orchestration.match_runner import MatchOrchestrator

    ledger = Web3Ledger.deploy(Web3(EthereumTesterProvider()))
    outcome = MatchOrchestrator(ledger=ledger, export_dir=tmp_path).run_scenario("reactor_lie")
    p = outcome.passport
    assert p.ledger.mode == "testnet" and p.ledger.final_phase == "Revealed"
    assert p.ledger.on_chain_event_log_hash == p.event_log_hash
    assert all(s.verified_on_ledger for s in p.players)


def test_player_passport_signature():
    wallet = Wallet.generate()
    pp = PlayerPassport(match_id="m", player="Red", seat=0, wallet_address=wallet.address,
                        role_commitment="0x" + "00" * 32).signed_by(wallet)
    assert pp.role is None and pp.verify()
    assert not pp.model_copy(update={"role": "IMPOSTOR"}).verify()


def test_exported_record_verifies_and_detects_tampering(orchestrator):
    outcome = orchestrator.run_scenario("reactor_lie")
    from pathlib import Path

    folder = Path(outcome.export_dir)
    assert all(ok for _, ok, _ in verify_record(folder))
    log = json.loads((folder / "event_log.json").read_bytes())
    assert to_hex(event_log_hash((folder / "event_log.json").read_bytes())) == outcome.passport.event_log_hash

    kill = next(e for e in log if e["action"] == "kill")
    kill["actor"] = "Red"  # try to rewrite history
    (folder / "event_log.json").write_bytes(json.dumps(log, sort_keys=True, separators=(",", ":")).encode())
    results = dict((name, ok) for name, ok, _ in verify_record(folder))
    assert results["event log hash"] is False
