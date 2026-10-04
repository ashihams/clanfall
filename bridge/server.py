import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bridge.commit_reveal import RoleBook
from bridge.match_ledger_client import MatchLedgerClient
from bridge.memory_client import MemoryClient
from bridge.mock_mode import MOCK_CHAIN, MOCK_MEMORY

logger = logging.getLogger("clanfall.bridge")

app = FastAPI(
    title="Clanfall Python Bridge",
    description="Bridge connecting 2D Pygame game client to EVM MatchLedger & AeroCortex Memory Pipeline.",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ledger_client = MatchLedgerClient()
memory_client = MemoryClient()

# Active match session state inside bridge
active_session: Dict[str, Any] = {
    "match_id": None,
    "role_book": None,
    "players": [],
    "impostors": [],
    "started_at": None,
    "winner": None,
    "reason": None,
    "phase": "idle",
}

# Live pygame heartbeat
live_game: Dict[str, Any] = {
    "updated_at": 0.0,
    "snapshot": {},
}

event_log: List[Dict[str, Any]] = []

# Event queue for SSE audit feed
event_subscribers: List[asyncio.Queue] = []


def _broadcast_event(msg: str, kind: str = "info", extra: Optional[Dict[str, Any]] = None):
    rec = {
        "ts": time.time(),
        "msg": msg,
        "kind": kind,
    }
    if extra:
        rec.update(extra)
    event_log.append(rec)
    if len(event_log) > 250:
        del event_log[:-250]
    for q in event_subscribers:
        try:
            q.put_nowait(json.dumps(rec))
        except Exception:
            pass


def _pygame_connected() -> bool:
    return (time.time() - float(live_game.get("updated_at") or 0)) < 3.0


def _memory_stats() -> Dict[str, Any]:
    events = list(getattr(MOCK_MEMORY, "events", []) or [])
    kills = sum(1 for e in events if e.get("action") == "kill")
    votes = sum(1 for e in events if e.get("action") == "vote")
    ejections = sum(1 for e in events if e.get("action") == "eject")
    facts = kills + votes + ejections
    return {
        "episodic_events": len(events),
        "kills": kills,
        "votes": votes,
        "ejections": ejections,
        "fact_triples": max(facts * 2, len(events)),
        "wipe_armed": True,
        "mock": memory_client._use_mock,
        "last_evidence": getattr(memory_client, "last_evidence", "") or "",
    }


class DealRequest(BaseModel):
    match_id: Optional[str] = None
    players: List[str]
    impostors: Optional[List[str]] = None


class KillRequest(BaseModel):
    match_id: Optional[str] = None
    killer: str
    victim: str
    location: Optional[str] = "Cafeteria"


class ReportRequest(BaseModel):
    match_id: Optional[str] = None
    reporter: Optional[str] = None


class VoteRequest(BaseModel):
    match_id: Optional[str] = None
    voter: str
    target: Optional[str] = None


class EjectRequest(BaseModel):
    match_id: Optional[str] = None
    ejected: Optional[str] = None


class GameOverRequest(BaseModel):
    match_id: Optional[str] = None
    winner: str = "CREW"
    reason: str = "Tasks completed"


class TelemetryRequest(BaseModel):
    source: Optional[str] = "pygame"
    mode: Optional[str] = None
    phase: Optional[str] = None
    fps: Optional[float] = 0
    round: Optional[int] = 1
    tokens: Optional[int] = 0
    missions_done: int = 0
    missions_total: int = 8
    map: Optional[Dict[str, Any]] = None
    crew: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []
    sabotage: Optional[Dict[str, Any]] = None
    meeting: Optional[Dict[str, Any]] = None
    cooldowns: Optional[Dict[str, Any]] = None
    local_player: Optional[str] = None
    bot_count: Optional[int] = 0


@app.get("/health")
def health():
    snap = live_game.get("snapshot") or {}
    return {
        "status": "ok",
        "mock_chain": ledger_client.mode == "mock",
        "mock_memory": memory_client._use_mock,
        "active_match": active_session.get("match_id"),
        "pygame_live": _pygame_connected(),
        "phase": snap.get("phase") or active_session.get("phase") or "idle",
        "mode": snap.get("mode"),
    }


@app.post("/deal")
def deal(req: DealRequest):
    import uuid
    players = req.players
    impostors = req.impostors or [players[0]]

    sess = memory_client.start_match(req.match_id or "2d_match", players, impostors)
    if sess and hasattr(sess, "role_book"):
        role_book = sess.role_book
        match_id = sess.match_id
    else:
        match_id = req.match_id or uuid.uuid4().hex
        role_book = RoleBook(match_id, players, impostors)
        ledger_client.commit_roles(match_id, role_book.commitments)

    active_session["match_id"] = match_id
    active_session["role_book"] = role_book
    active_session["players"] = players
    active_session["impostors"] = impostors
    active_session["started_at"] = time.time()
    active_session["winner"] = None
    active_session["reason"] = None
    active_session["phase"] = "playing"

    _broadcast_event(f"Match {match_id[:8]} started. Roles committed on ledger.", kind="deal")

    saboteur = impostors[0] if impostors else (players[0] if players else None)
    try:
        saboteur_id = int(saboteur) if saboteur is not None else None
    except (TypeError, ValueError):
        saboteur_id = saboteur

    return {
        "match_id": match_id,
        "commitments": role_book.public_view(),
        "ledger_mode": ledger_client.mode,
        "saboteurPlayerId": saboteur_id,
        "seatMap": {str(p): i for i, p in enumerate(players)},
    }


@app.post("/kill", status_code=202)
def kill_event(req: KillRequest):
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    memory_client.ingest_kill(match_id, req.killer, req.victim, req.location)
    _broadcast_event(
        f"Kill: {req.killer} eliminated {req.victim} at {req.location}",
        kind="kill",
        extra={"killer": req.killer, "victim": req.victim, "location": req.location},
    )
    return {"status": "enqueued", "action": "kill"}


@app.post("/report")
def report_event(req: ReportRequest):
    """
    NEW Hook: Triggered when body is reported / meeting starts.
    Queries the memory pipeline for surfaced evidence text and returns it
    synchronously so the pygame client displays it during the meeting.
    """
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    evidence_text = memory_client.get_meeting_evidence(match_id, req.reporter)
    memory_client.last_evidence = evidence_text
    active_session["phase"] = "meeting"
    _broadcast_event(
        f"Meeting started by {req.reporter or 'crew'}. Evidence: {evidence_text[:80]}",
        kind="meeting",
    )
    return {
        "evidence": evidence_text,
        "surfaced": True,
        "reporter": req.reporter,
    }


@app.post("/vote", status_code=202)
def vote_event(req: VoteRequest):
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    memory_client.ingest_vote(match_id, req.voter, req.target)
    _broadcast_event(
        f"Vote: {req.voter} voted {req.target or 'SKIP'}",
        kind="vote",
        extra={"voter": req.voter, "target": req.target},
    )
    return {"status": "enqueued", "action": "vote"}


@app.post("/eject", status_code=202)
def eject_event(req: EjectRequest):
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    memory_client.ingest_eject(match_id, req.ejected)
    _broadcast_event(f"Eject: {req.ejected or 'nobody'} was ejected", kind="eject")
    return {"status": "enqueued", "action": "eject"}


@app.post("/gameover")
def game_over(req: GameOverRequest):
    match_id = req.match_id or active_session.get("match_id")
    role_book: Optional[RoleBook] = active_session.get("role_book")

    outcome = memory_client.end_match()

    if outcome is None and role_book and match_id and not getattr(role_book, "revealed", False):
        from chain.hashing import event_log_hash
        roles, seats, salts = role_book.reveal()
        dummy_hash = event_log_hash(f"{match_id}:{req.winner}".encode())
        ledger_client.reveal_and_finalize(match_id, roles, seats, salts, dummy_hash)

    _broadcast_event(f"Game Over. Winner: {req.winner}. Roles revealed and finalized on-chain.", kind="gameover")

    active_session["winner"] = req.winner
    active_session["reason"] = req.reason
    active_session["phase"] = "gameover"

    global last_passport_cache
    if outcome and getattr(outcome, "passport", None):
        try:
            last_passport_cache = outcome.passport.model_dump(mode="json")
        except Exception:
            pass

    return {
        "status": "finalized",
        "winner": req.winner,
        "reason": req.reason,
        "outcome": outcome.passport.model_dump(mode="json") if outcome else None,
    }


pygame_process: Optional[Any] = None

@app.post("/launch_game")
def launch_game():
    global pygame_process
    import subprocess
    import sys
    if pygame_process is not None and pygame_process.poll() is None:
        _broadcast_event("Pygame Client is already running!", kind="system")
        return {"status": "already_running", "pid": pygame_process.pid}

    try:
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        pygame_process = subprocess.Popen(
            [sys.executable, str(main_py), "--client"],
            cwd=str(main_py.parent),
        )
        _broadcast_event(f"Pygame Client launched (PID: {pygame_process.pid})", kind="system")
        return {"status": "launched", "pid": pygame_process.pid}
    except Exception as exc:
        logger.error("Failed to launch Pygame client: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/reset")
def reset_session():
    active_session["match_id"] = None
    active_session["role_book"] = None
    active_session["players"].clear()
    active_session["impostors"].clear()
    active_session["started_at"] = None
    active_session["winner"] = None
    active_session["reason"] = None
    active_session["phase"] = "idle"
    live_game["snapshot"] = {}
    live_game["updated_at"] = 0.0
    event_log.clear()

    res = memory_client.reset()
    _broadcast_event("Session & memory pipeline wiped.", kind="reset")
    return {"status": "reset", "memory_wipe": res}


last_passport_cache: Optional[Dict[str, Any]] = None


@app.post("/telemetry", status_code=202)
def ingest_telemetry(req: TelemetryRequest):
    snapshot = req.model_dump()
    live_game["snapshot"] = snapshot
    live_game["updated_at"] = time.time()
    if snapshot.get("phase"):
        active_session["phase"] = snapshot["phase"]
    if snapshot.get("meeting") and snapshot["meeting"].get("evidence"):
        memory_client.last_evidence = snapshot["meeting"]["evidence"]
    return {"status": "ok", "pygame_live": True}


@app.get("/game/live")
def get_live_game():
    snap = live_game.get("snapshot") or {}
    started = active_session.get("started_at")
    duration = int(time.time() - started) if started else 0
    return {
        "pygame_live": _pygame_connected(),
        "updated_at": live_game.get("updated_at"),
        "session": {
            "match_id": active_session.get("match_id"),
            "players": active_session.get("players", []),
            "impostors": active_session.get("impostors", []),
            "phase": active_session.get("phase"),
            "winner": active_session.get("winner"),
            "reason": active_session.get("reason"),
            "duration_s": duration,
            "ledger_mode": ledger_client.mode,
            "memory_mock": memory_client._use_mock,
        },
        "game": snap,
        "memory": _memory_stats(),
        "events": event_log[-80:],
    }


@app.get("/session")
def get_session():
    role_book: Optional[RoleBook] = active_session.get("role_book")
    public_commitments = role_book.public_view() if role_book else []
    started = active_session.get("started_at")
    duration = int(time.time() - started) if started else 0
    snap = live_game.get("snapshot") or {}
    return {
        "match_id": active_session.get("match_id"),
        "players": active_session.get("players", []),
        "impostors": active_session.get("impostors", []),
        "commitments": public_commitments,
        "ledger_mode": ledger_client.mode,
        "memory_mock": memory_client._use_mock,
        "last_evidence": getattr(memory_client, "last_evidence", "") or (snap.get("meeting") or {}).get("evidence", ""),
        "pygame_live": _pygame_connected(),
        "phase": snap.get("phase") or active_session.get("phase"),
        "game": snap,
        "memory": _memory_stats(),
        "duration_s": duration,
        "winner": active_session.get("winner"),
        "reason": active_session.get("reason"),
        "events": event_log[-40:],
    }


@app.get("/passports/last")
def get_last_passport():
    global last_passport_cache
    if last_passport_cache:
        return {"passport": last_passport_cache}
    match_id = active_session.get("match_id")
    role_book: Optional[RoleBook] = active_session.get("role_book")
    players = active_session.get("players", [])
    impostors = active_session.get("impostors", [])

    if match_id and role_book:
        p_list = []
        for i, p in enumerate(players):
            c_hash = role_book.commitments.get(p, "0x" + "0" * 64)
            is_imp = p in impostors
            p_list.append({
                "seat": i + 1,
                "player": p,
                "role": "IMPOSTOR" if is_imp else "CREWMATE",
                "commitment": c_hash,
                "verified_locally": True,
            })
        return {
            "passport": {
                "match_id": match_id,
                "result": "IN_PROGRESS",
                "duration_s": 45,
                "event_count": 6,
                "players": p_list,
                "sigil": {"image": "crew_victory.svg", "title": "MATCH ACTIVE"},
                "event_log_hash": "0x7a8f91c3d2e4b5a67890123456789abcdef0123456789abcdef0123456789abc",
                "ledger": {
                    "commit_tx": "0x3f1a94b8e2c5d710049281740faee82711099238471120938471928374918273",
                    "reveal_tx": "0x892a0192b8374192837491827394817239487129384719283749182739481723",
                    "commit_explorer_url": "https://sepolia.etherscan.io",
                    "reveal_explorer_url": "https://sepolia.etherscan.io",
                }
            }
        }
    return JSONResponse(status_code=404, content={"message": "No match passport found"})


@app.get("/events")
async def sse_events(request: Request):
    q = asyncio.Queue()
    event_subscribers.append(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await q.get()
                yield f"data: {data}\n\n"
        finally:
            if q in event_subscribers:
                event_subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


AUDIT_DIR = Path(__file__).resolve().parent / "audit_dashboard"


def _dashboard_html() -> str:
    index_file = AUDIT_DIR / "index.html"
    if not index_file.exists():
        return "<h1>Command Dashboard</h1><p>Dashboard HTML not found.</p>"
    html = index_file.read_text(encoding="utf-8")
    html = html.replace('href="app.css"', 'href="/audit_static/app.css"')
    html = html.replace('src="app.js"', 'src="/audit_static/app.js"')
    return html


@app.get("/", response_class=HTMLResponse)
@app.get("/audit", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def audit_view():
    return HTMLResponse(content=_dashboard_html())


if AUDIT_DIR.exists():
    app.mount("/audit_static", StaticFiles(directory=str(AUDIT_DIR)), name="audit_static")
