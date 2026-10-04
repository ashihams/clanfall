import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from chain.commit_reveal import RoleBook, verify_commitment
from chain.hashing import event_log_hash, from_hex, keccak256, to_hex
from chain.leaderboard import ProfileStore, consent_signer, leaderboard_for_ledger, sign_consent
from chain.ledger import LedgerError, MockLedger, get_ledger, phase_name
from chain.nft import MockPassportNFT, build_metadata, nft_for_ledger, token_uri
from chain.passport import (
    LedgerRecord, MatchPassport, NFTRecord, PlayerPassport, RevealedSeat, Wallet, select_sigil,
)
from config import PROJECT_ROOT, config
from models import ActionType, GameEvent, Role
from orchestration.graph import ClanfallGraph
from simulation.engine import MatchEngine
from simulation.scenario import Scenario, get_scenario

logger = logging.getLogger("clanfall.match")


class MatchStateError(RuntimeError):
    pass


@dataclass
class MatchSession:
    match_id: str
    scenario: Scenario
    role_book: RoleBook
    wallets: Dict[str, Wallet]
    host_wallet: Wallet
    passports: Dict[str, PlayerPassport]
    ledger: Any
    commit_receipt: Any
    engine: MatchEngine
    pre_start_wipe: Optional[Dict[str, Any]] = None


@dataclass
class MatchOutcome:
    passport: MatchPassport
    player_passports: List[PlayerPassport]
    wipe: Dict[str, Any]
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    export_dir: Optional[str] = None
    # Kept in process memory only, so a player can still choose to opt in after the match.
    wallets: Dict[str, Wallet] = field(default_factory=dict, repr=False)
    ledger: Any = field(default=None, repr=False)
    opted_in: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class MatchOrchestrator:
    """
    Bridge between the simulation and everything downstream. The engine only
    emits events into ingest(); memory, chain, and passports are handled here.
    """

    def __init__(self, graph: Optional[ClanfallGraph] = None, ledger: Any = None,
                 export_dir: Optional[Union[str, Path]] = None, nft: Any = "auto", leaderboard: Any = None,
                 profiles: Optional[ProfileStore] = None):
        self.graph = graph or ClanfallGraph()
        self.ledger = ledger or get_ledger()
        self.export_dir = Path(export_dir) if export_dir else PROJECT_ROOT / config.passport_dir
        self.nft = nft_for_ledger(self.ledger) if nft == "auto" else nft
        self.leaderboard = leaderboard or leaderboard_for_ledger(self.ledger)
        self.profiles = profiles or ProfileStore(self.export_dir.parent / "profiles" / "profiles.json")
        self.session: Optional[MatchSession] = None
        self.last_outcome: Optional[MatchOutcome] = None

    @property
    def memory(self):
        return self.graph.memory

    # ------------------------------------------------------------------
    # Match start: wipe guard + commit roles BEFORE any simulation
    # ------------------------------------------------------------------

    def start_match(self, scenario: Union[str, Scenario]) -> MatchSession:
        if self.session is not None:
            raise MatchStateError(f"match {self.session.match_id} is still in progress")
        scenario = get_scenario(scenario) if isinstance(scenario, str) else scenario

        pre_wipe = None
        if not self.memory.is_empty():
            logger.warning("Memory was not empty at match start; wiping before commit")
            pre_wipe = self.graph.reset()

        match_id = uuid.uuid4().hex
        role_book = RoleBook(match_id, scenario.players, scenario.impostors)
        ledger = self.ledger
        try:
            receipt = ledger.commit_roles(role_book.match_id_b32, role_book.commitments)
        except Exception as exc:
            if isinstance(ledger, MockLedger):
                raise
            logger.warning("Ledger commit failed on %s (%s); this match falls back to the mock ledger", ledger.mode, exc)
            ledger = MockLedger()
            receipt = ledger.commit_roles(role_book.match_id_b32, role_book.commitments)

        wallets = {p: Wallet.generate() for p in scenario.players}
        passports = {
            p: PlayerPassport(
                match_id=match_id,
                player=p,
                seat=role_book.seat_of(p),
                wallet_address=wallets[p].address,
                role_commitment=to_hex(role_book.commitments[role_book.seat_of(p)]),
            ).signed_by(wallets[p])
            for p in scenario.players
        }
        engine = MatchEngine(scenario, match_id, role_book.roles(), sink=self.ingest)
        self.session = MatchSession(
            match_id=match_id,
            scenario=scenario,
            role_book=role_book,
            wallets=wallets,
            host_wallet=Wallet.generate(),
            passports=passports,
            ledger=ledger,
            commit_receipt=receipt,
            engine=engine,
            pre_start_wipe=pre_wipe,
        )
        return self.session

    # ------------------------------------------------------------------
    # The /event boundary
    # ------------------------------------------------------------------

    def ingest(self, event: GameEvent) -> Dict[str, Any]:
        if self.session is None:
            raise MatchStateError("no active match — start one first")
        if event.match_id != self.session.match_id:
            raise MatchStateError(f"event belongs to {event.match_id}, active match is {self.session.match_id}")
        return self.graph.run(event)

    def step(self) -> List[Dict[str, Any]]:
        if self.session is None:
            raise MatchStateError("no active match")
        return self.session.engine.advance()

    # ------------------------------------------------------------------
    # Match end: hash, reveal, passport, export, WIPE
    # ------------------------------------------------------------------

    def end_match(self) -> MatchOutcome:
        s = self.session
        if s is None:
            raise MatchStateError("no active match")

        episodic = self.memory.episodic
        log_bytes = episodic.canonical_bytes()
        log_hash = event_log_hash(log_bytes)
        events = episodic.events()

        roles, seats, salts = s.role_book.reveal()
        reveal_receipt = s.ledger.reveal_and_finalize(s.role_book.match_id_b32, roles, seats, salts, log_hash)

        revealed: List[RevealedSeat] = []
        for a in s.role_book.assignments():
            revealed.append(RevealedSeat(
                seat=a.seat,
                player=a.player,
                wallet_address=s.wallets[a.player].address,
                role=Role(a.role).name,
                commitment=to_hex(a.commitment),
                salt=to_hex(a.salt),
                verified_locally=verify_commitment(s.match_id, int(a.role), a.seat, a.salt, a.commitment),
                verified_on_ledger=s.ledger.verify_commitment(s.role_book.match_id_b32, int(a.role), a.seat, a.salt),
            ))

        game_over = next((e for e in reversed(events) if e.action == ActionType.GAME_OVER), None)
        passport = MatchPassport(
            match_id=s.match_id,
            scenario=s.scenario.key,
            players=revealed,
            result=(game_over.detail.get("result") if game_over else "UNRESOLVED"),
            reason=(game_over.detail.get("reason", "") if game_over else "match ended without GAME_OVER"),
            duration_s=events[-1].timestamp if events else 0.0,
            task_count=sum(1 for e in events if e.action == ActionType.TASK),
            meeting_count=sum(1 for e in events if e.action == ActionType.MEETING_START),
            kill_count=sum(1 for e in events if e.action == ActionType.KILL),
            ejections=[e.target for e in events if e.action == ActionType.EJECT and e.target],
            event_count=len(events),
            event_log_hash=to_hex(log_hash),
            evidence=[ev.model_dump(mode="json") for ev in self.memory.working.evidence],
            ledger=LedgerRecord(
                mode=s.ledger.mode,
                contract=s.ledger.address,
                match_id_bytes32=to_hex(s.role_book.match_id_b32),
                commit_tx=s.commit_receipt.tx_hash,
                reveal_tx=reveal_receipt.tx_hash,
                commit_explorer_url=s.commit_receipt.explorer_url,
                reveal_explorer_url=reveal_receipt.explorer_url,
                final_phase=phase_name(s.ledger.get_match_phase(s.role_book.match_id_b32)),
                on_chain_event_log_hash=to_hex(s.ledger.get_event_log_hash(s.role_book.match_id_b32)),
            ),
            sigil=select_sigil(game_over.detail.get("result") if game_over else "UNRESOLVED"),
        )
        nft_record, nft_error = self._mint_passport(s, passport, log_hash)
        passport = passport.model_copy(update={"nft": nft_record, "nft_error": nft_error}).signed_by(s.host_wallet)

        player_passports = [
            s.passports[a.player].model_copy(update={"role": Role(a.role).name}).signed_by(s.wallets[a.player])
            for a in s.role_book.assignments()
        ]
        timeline = list(s.engine.timeline)
        export_dir = self._export(passport, player_passports, log_bytes)

        wipe = self.graph.reset()
        if not wipe["wiped"]:
            raise MatchStateError(f"memory wipe failed: {wipe['after']} remote_left={wipe.get('remote_left')}")
        self.session = None
        self.last_outcome = MatchOutcome(
            passport=passport,
            player_passports=player_passports,
            wipe=wipe,
            timeline=timeline,
            export_dir=str(export_dir) if export_dir else None,
            wallets=dict(s.wallets),
            ledger=s.ledger,
        )
        return self.last_outcome

    def _mint_passport(self, s: MatchSession, passport: MatchPassport, log_hash: bytes):
        """Stretch: mint the ERC-721 Match Passport. A failure here never blocks the match from finishing."""
        if self.nft is None:
            return None, None
        nft = self.nft if s.ledger is self.ledger else MockPassportNFT(s.ledger)
        uri = token_uri(build_metadata(passport))
        try:
            minted = nft.mint(nft.owner, s.role_book.match_id_b32, log_hash, uri)
        except Exception as exc:
            logger.warning("Match Passport NFT mint failed on %s: %s", nft.mode, exc)
            return None, f"{type(exc).__name__}: {exc}"
        token_id = minted["token_id"]
        return NFTRecord(
            mode=nft.mode,
            contract=nft.address,
            token_id=token_id,
            owner=nft.owner,
            mint_tx=minted["receipt"].tx_hash,
            mint_explorer_url=minted["receipt"].explorer_url,
            token_url=nft.token_url(token_id),
            metadata_sha3=to_hex(keccak256(uri.encode())),
        ), None

    # ------------------------------------------------------------------
    # Opt-in leaderboard: separate from match memory, player-initiated only
    # ------------------------------------------------------------------

    def opt_in_leaderboard(self, player: str) -> Dict[str, Any]:
        out = self.last_outcome
        if out is None:
            raise MatchStateError("no finished match to publish a result from")
        seat = next((p for p in out.passport.players if p.player == player), None)
        if seat is None:
            raise MatchStateError(f"{player} did not play in match {out.passport.match_id}")
        if player in out.opted_in:
            raise MatchStateError(f"{player} already published this match")
        if out.passport.result not in ("CREW_VICTORY", "IMPOSTOR_VICTORY"):
            raise MatchStateError("match has no winner to publish")
        if out.ledger is not self.ledger:
            raise MatchStateError("this match was finalized on the fallback mock ledger, so it can't be published")

        won = (seat.role == "CREW") == (out.passport.result == "CREW_VICTORY")
        match_id_b32 = from_hex(out.passport.ledger.match_id_bytes32)
        profile = self.profiles.get_or_create(player)

        consent = sign_consent(out.wallets[player], out.passport.match_id, profile.address)
        if consent_signer(consent, out.passport.match_id, profile.address) != seat.wallet_address:
            raise MatchStateError("consent signature does not match the player's match wallet")
        attestation = self.leaderboard.attest(match_id_b32, profile.address, won)
        submitted = self.leaderboard.submit(profile, match_id_b32, won, attestation)

        receipt, topup = submitted["receipt"], submitted["topup"]
        entry = {
            "player": player,
            "profile_address": profile.address,
            "match_id": out.passport.match_id,
            "role": seat.role,
            "won": won,
            "consent_signature": consent,
            "host_attestation": attestation,
            "mode": self.leaderboard.mode,
            "contract": self.leaderboard.address,
            "tx": receipt.tx_hash,
            "explorer_url": receipt.explorer_url,
            "gas_topup_tx": topup.tx_hash if topup else None,
            "record": submitted.get("record") or self.leaderboard.get_record(profile.address),
        }
        out.opted_in[player] = entry
        return entry

    def leaderboard_standings(self) -> List[Dict[str, Any]]:
        names = self.profiles.names_by_address()
        rows = [{"player": names.get(r["address"]), **r} for r in self.leaderboard.standings()]
        return sorted(rows, key=lambda r: (-r["wins"], r["losses"]))

    def _export(self, passport: MatchPassport, players: List[PlayerPassport], log_bytes: bytes) -> Optional[Path]:
        """Write the verifiable match record (not memory — the engine never reads these back)."""
        try:
            out = self.export_dir / passport.match_id
            out.mkdir(parents=True, exist_ok=True)
            (out / "match_passport.json").write_text(json.dumps(passport.model_dump(mode="json"), indent=2), encoding="utf-8")
            (out / "player_passports.json").write_text(
                json.dumps([p.model_dump(mode="json") for p in players], indent=2), encoding="utf-8"
            )
            (out / "event_log.json").write_bytes(log_bytes)
            if passport.nft:
                (out / "nft_metadata.json").write_text(json.dumps(build_metadata(passport), indent=2), encoding="utf-8")
            return out
        except OSError as exc:
            logger.warning("Passport export failed: %s", exc)
            return None

    def abort_match(self) -> Dict[str, Any]:
        """Drop the active match without finalizing (roles stay committed, never revealed) and wipe."""
        self.session = None
        return self.graph.reset()

    def run_scenario(self, scenario: Union[str, Scenario]) -> MatchOutcome:
        session = self.start_match(scenario)
        try:
            session.engine.run()
        except Exception:
            self.abort_match()
            raise
        return self.end_match()


__all__ = ["MatchOrchestrator", "MatchSession", "MatchOutcome", "MatchStateError", "LedgerError"]
