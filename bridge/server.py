import asyncio
import json
import logging
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
}

# Event queue for SSE audit feed
event_subscribers: List[asyncio.Queue] = []


def _broadcast_event(msg: str):
    for q in event_subscribers:
        try:
            q.put_nowait(msg)
        except Exception:
            pass


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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mock_chain": ledger_client.mode == "mock",
        "mock_memory": memory_client._use_mock,
        "active_match": active_session.get("match_id"),
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

    _broadcast_event(f"Match {match_id[:8]} started. Roles committed on ledger.")

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
    _broadcast_event(f"Event: {req.killer} killed {req.victim} at {req.location}")
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
    _broadcast_event(f"Meeting started by {req.reporter or 'crew'}. Evidence surfaced: {evidence_text[:40]}...")
    return {
        "evidence": evidence_text,
        "surfaced": True,
        "reporter": req.reporter,
    }


@app.post("/vote", status_code=202)
def vote_event(req: VoteRequest):
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    memory_client.ingest_vote(match_id, req.voter, req.target)
    _broadcast_event(f"Event: {req.voter} voted for {req.target or 'skip'}")
    return {"status": "enqueued", "action": "vote"}


@app.post("/eject", status_code=202)
def eject_event(req: EjectRequest):
    match_id = req.match_id or active_session.get("match_id") or "mock_match"
    memory_client.ingest_eject(match_id, req.ejected)
    _broadcast_event(f"Event: Player {req.ejected or 'nobody'} was ejected")
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

    _broadcast_event(f"Game Over. Winner: {req.winner}. Roles revealed and finalized on-chain.")

    return {
        "status": "finalized",
        "winner": req.winner,
        "reason": req.reason,
        "outcome": outcome.passport.model_dump(mode="json") if outcome else None,
    }


@app.post("/reset")
def reset_session():
    active_session["match_id"] = None
    active_session["role_book"] = None
    active_session["players"].clear()
    active_session["impostors"].clear()

    res = memory_client.reset()
    _broadcast_event("Session & memory pipeline wiped.")
    return {"status": "reset", "memory_wipe": res}


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

@app.get("/audit", response_class=HTMLResponse)
def audit_view():
    index_file = AUDIT_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Audit Dashboard</h1><p>Dashboard HTML not found.</p>")

if AUDIT_DIR.exists():
    app.mount("/audit_static", StaticFiles(directory=str(AUDIT_DIR)), name="audit_static")
