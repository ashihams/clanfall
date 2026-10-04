import json
from pathlib import Path

import pytest

from chain.commit_reveal import RoleBook
from chain.hashing import keccak256
from chain.leaderboard import (
    MockLeaderboard, ProfileStore, consent_signer, recover_attestor, sign_attestation, sign_consent,
)
from chain.ledger import BUILD_DIR, LedgerError, MockLedger
from chain.nft import MockPassportNFT, decode_token_uri, token_uri
from chain.passport import Wallet
from chain.verify import verify_record

PLAYERS = ["Red", "Blue", "Green", "Yellow"]
LOG_HASH = keccak256(b"event log")


def _evm_available() -> bool:
    try:
        import eth_tester  # noqa: F401
    except ImportError:
        return False
    return all((BUILD_DIR / f"{n}.json").exists() for n in ("MatchLedger", "MatchPassport", "ClanfallLeaderboard"))


def _stack(kind: str):
    """(ledger, nft, leaderboard) wired together, mock or on a local EVM."""
    if kind == "mock":
        ledger = MockLedger()
        return ledger, MockPassportNFT(ledger), MockLeaderboard(ledger)
    from web3 import EthereumTesterProvider, Web3

    from chain.leaderboard import Web3Leaderboard
    from chain.ledger import Web3Ledger
    from chain.nft import Web3PassportNFT

    ledger = Web3Ledger.deploy(Web3(EthereumTesterProvider()))
    return ledger, Web3PassportNFT.deploy(ledger), Web3Leaderboard.deploy(ledger, attestor=Wallet.generate())


KINDS = ["mock"] + (["evm"] if _evm_available() else [])


@pytest.fixture(params=KINDS)
def stack(request):
    return _stack(request.param)


def _match(ledger, name="m-nft", finalize=True):
    book = RoleBook(name, PLAYERS, ["Blue"])
    ledger.commit_roles(book.match_id_b32, book.commitments)
    if finalize:
        roles, seats, salts = book.reveal()
        ledger.reveal_and_finalize(book.match_id_b32, roles, seats, salts, LOG_HASH)
    return book.match_id_b32


def test_token_uri_roundtrip():
    meta = {"name": "x", "attributes": [{"trait_type": "Result", "value": "CREW_VICTORY"}]}
    assert decode_token_uri(token_uri(meta)) == meta


def test_nft_mints_only_for_revealed_match_with_matching_hash(stack):
    ledger, nft, _ = stack
    pending = _match(ledger, "m-pending", finalize=False)
    with pytest.raises(LedgerError) as err:
        nft.mint(nft.owner, pending, LOG_HASH, "data:,x")
    assert err.value.code == "MatchNotFinalized"

    mid = _match(ledger)
    with pytest.raises(LedgerError) as err:
        nft.mint(nft.owner, mid, keccak256(b"rewritten log"), "data:,x")
    assert err.value.code == "EventLogHashMismatch"

    uri = token_uri({"name": "Clanfall Match Passport"})
    minted = nft.mint(nft.owner, mid, LOG_HASH, uri)
    tid = minted["token_id"]
    assert tid == 1 and nft.token_of_match(mid) == 1
    assert nft.owner_of(tid) == nft.owner and nft.token_uri(tid) == uri
    assert nft.event_log_hash_of(tid) == LOG_HASH

    with pytest.raises(LedgerError) as err:
        nft.mint(nft.owner, mid, LOG_HASH, uri)
    assert err.value.code == "PassportAlreadyMinted"


def test_leaderboard_requires_host_attestation_and_blocks_replays(stack):
    ledger, _, board = stack
    player = Wallet.generate()

    pending = _match(ledger, "m-pending", finalize=False)
    with pytest.raises(LedgerError) as err:
        board.submit(player, pending, True, board.attest(pending, player.address, True))
    assert err.value.code == "MatchNotFinalized"

    mid = _match(ledger)
    forged = sign_attestation(Wallet.generate().private_key, board.address, board.chain_id, mid, player.address, True)
    with pytest.raises(LedgerError) as err:
        board.submit(player, mid, True, forged)
    assert err.value.code == "InvalidAttestation"

    # The host attested a loss; claiming a win with it must fail.
    loss = board.attest(mid, player.address, False)
    with pytest.raises(LedgerError) as err:
        board.submit(player, mid, True, loss)
    assert err.value.code == "InvalidAttestation"

    board.submit(player, mid, False, loss)
    assert board.get_record(player.address)["losses"] == 1
    assert board.has_submitted(mid, player.address)
    with pytest.raises(LedgerError) as err:
        board.submit(player, mid, False, loss)
    assert err.value.code == "AlreadySubmitted"
    assert [r["address"] for r in board.standings()] == [player.address]


def test_attestation_and_consent_signatures():
    host, player, match_wallet = Wallet.generate(), Wallet.generate(), Wallet.generate()
    sig = sign_attestation(host.private_key, MockLeaderboard(MockLedger()).address, 84532, b"\x01" * 32, player.address, True)
    assert recover_attestor(sig, MockLeaderboard(MockLedger()).address, 84532, b"\x01" * 32, player.address, True) == host.address
    consent = sign_consent(match_wallet, "m1", player.address)
    assert consent_signer(consent, "m1", player.address) == match_wallet.address
    assert consent_signer(consent, "m1", Wallet.generate().address) != match_wallet.address


def test_match_end_mints_passport_and_record_verifies(orchestrator):
    outcome = orchestrator.run_scenario("reactor_lie")
    nft = outcome.passport.nft
    assert nft is not None and nft.token_id == 1 and outcome.passport.nft_error is None
    assert outcome.passport.verify_signature()

    folder = Path(outcome.export_dir)
    meta = json.loads((folder / "nft_metadata.json").read_text(encoding="utf-8"))
    attrs = {a["trait_type"]: a["value"] for a in meta["attributes"]}
    assert attrs["Result"] == "CREW_VICTORY" and attrs["Event log hash"] == outcome.passport.event_log_hash
    assert meta["image"].startswith("data:image/svg+xml;base64,")
    results = verify_record(folder)
    assert all(ok for _, ok, _ in results), results

    assert orchestrator.run_scenario("clean_getaway").passport.nft.token_id == 2


def test_opt_in_is_explicit_separate_from_memory_and_accumulates(orchestrator):
    assert orchestrator.leaderboard_standings() == []

    orchestrator.run_scenario("reactor_lie")       # crew wins, Blue (impostor) loses
    assert orchestrator.leaderboard_standings() == []  # nothing published without opting in
    assert orchestrator.memory.is_empty()

    red = orchestrator.opt_in_leaderboard("Red")
    assert red["won"] is True and (red["record"]["wins"], red["record"]["losses"]) == (1, 0)
    assert orchestrator.opt_in_leaderboard("Blue")["won"] is False
    with pytest.raises(Exception):
        orchestrator.opt_in_leaderboard("Red")
    assert orchestrator.memory.is_empty()              # publishing never touches match memory

    orchestrator.run_scenario("clean_getaway")     # impostor (Red) wins
    red2 = orchestrator.opt_in_leaderboard("Red")
    assert red2["profile_address"] == red["profile_address"]
    assert red2["record"]["wins"] == 2

    standings = {r["player"]: r for r in orchestrator.leaderboard_standings()}
    assert standings["Red"]["wins"] == 2 and standings["Blue"]["losses"] == 1
    assert "Green" not in standings


def test_profile_store_persists(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    w = store.get_or_create("Red")
    assert ProfileStore(tmp_path / "profiles.json").get("Red").address == w.address


@pytest.mark.skipif("evm" not in KINDS, reason="eth-tester or contract artifacts missing")
def test_contract_itself_rejects_bad_submissions():
    """Bypass the Python pre-checks and hit ClanfallLeaderboard.sol directly."""
    from chain.leaderboard import LEADERBOARD_ERRORS
    from chain.ledger import _map_error, _send

    ledger, _, board = _stack("evm")
    mid = _match(ledger)
    player = board.w3.eth.accounts[3]

    def raw_submit(won, attestation):
        fn = board.contract.functions.optInAndSubmit(mid, won, bytes.fromhex(attestation[2:]))
        try:
            _send(board.w3, fn, None, player, gas=300_000)
        except Exception as exc:
            raise _map_error(exc, LEADERBOARD_ERRORS) from exc

    forged = sign_attestation(Wallet.generate().private_key, board.address, board.chain_id, mid, player, True)
    for won, att, code in [(True, forged, "InvalidAttestation"),
                           (True, board.attest(mid, player, False), "InvalidAttestation")]:
        with pytest.raises(LedgerError) as err:
            raw_submit(won, att)
        assert err.value.code == code

    raw_submit(False, board.attest(mid, player, False))
    with pytest.raises(LedgerError) as err:
        raw_submit(False, board.attest(mid, player, False))
    assert err.value.code == "AlreadySubmitted"


@pytest.mark.skipif("evm" not in KINDS, reason="eth-tester or contract artifacts missing")
def test_full_match_mint_and_opt_in_on_evm(tmp_path):
    from web3 import EthereumTesterProvider, Web3

    from chain.leaderboard import Web3Leaderboard
    from chain.ledger import Web3Ledger
    from chain.nft import Web3PassportNFT
    from orchestration.match_runner import MatchOrchestrator

    ledger = Web3Ledger.deploy(Web3(EthereumTesterProvider()))
    orch = MatchOrchestrator(
        ledger=ledger, export_dir=tmp_path / "passports",
        nft=Web3PassportNFT.deploy(ledger), leaderboard=Web3Leaderboard.deploy(ledger, attestor=Wallet.generate()),
    )
    outcome = orch.run_scenario("reactor_lie")
    assert outcome.passport.nft.mode == "testnet" and outcome.passport.nft.token_id == 1
    assert orch.nft.event_log_hash_of(1).hex() == outcome.passport.event_log_hash.removeprefix("0x")

    entry = orch.opt_in_leaderboard("Red")
    assert entry["mode"] == "testnet" and entry["gas_topup_tx"]
    assert orch.leaderboard.get_record(entry["profile_address"])["wins"] == 1
