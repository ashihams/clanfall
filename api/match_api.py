import logging
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chain.ledger import LedgerError
from config import PROJECT_ROOT, config
from llm.groq_client import GroqClient
from memory.knowledge_graph import player_node
from models import GameEvent
from orchestration.match_runner import MatchOrchestrator, MatchOutcome, MatchStateError
from simulation.engine import EngineError
from simulation.map import ROOMS, edges
from simulation.scenario import SCENARIOS, Scenario

logger = logging.getLogger("clanfall.api")

orchestrator = MatchOrchestrator()

app = FastAPI(
    title="Clanfall Match-Memory API",
    description="Pluggable per-match memory engine (episodic -> semantic -> graph) with verifiable match records.",
    version=config.system.version,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EXEMPT_PREFIXES = ("/docs", "/openapi.json", "/redoc", "/dashboard", "/assets", "/healthz")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    expected = config.api_key or ""
    path = request.url.path
    if not expected or path == "/" or path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)
    if not secrets.compare_digest(request.headers.get("X-API-Key", ""), expected):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


@app.exception_handler(MatchStateError)
async def _match_state_error(_: Request, exc: MatchStateError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(EngineError)
async def _engine_error(_: Request, exc: EngineError):
    return JSONResponse(status_code=422, content={"detail": f"rule violation: {exc}"})


@app.exception_handler(LedgerError)
async def _ledger_error(_: Request, exc: LedgerError):
    return JSONResponse(status_code=502, content={"detail": f"ledger rejected the call: {exc}"})


class StartRequest(BaseModel):
    scenario: Optional[str] = "reactor_lie"
    players: Optional[List[str]] = None
    impostors: Optional[List[str]] = None


class RunRequest(BaseModel):
    scenario: str = "reactor_lie"


class OptInRequest(BaseModel):
    player: str


def _dump(model) -> Optional[Dict[str, Any]]:
    return model.model_dump(mode="json") if model is not None else None


def pipeline_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    ctx = state.get("memory_context")
    return {
        "event": _dump(state.get("stored_event")),
        "situation": _dump(state.get("situation")),
        "new_facts": [f.model_dump(mode="json") for f in state.get("new_facts", [])],
        "evidence": _dump(state.get("evidence")),
        "retrieval": _context_payload(ctx),
        "logs": state.get("logs", []),
    }


def _context_payload(ctx) -> Optional[Dict[str, Any]]:
    if ctx is None:
        return None
    return {
        "query": ctx.query,
        "vector_engine": ctx.vector_engine,
        "latency_ms": ctx.retrieval_latency_ms,
        "ranked": [
            {
                "fact_id": r.fact.fact_id,
                "fact_type": r.fact.fact_type.value,
                "subject": r.fact.subject,
                "text": r.fact.text,
                "vector_similarity": r.vector_similarity,
                "graph_relevance": r.graph_relevance,
                "final_score": r.final_score,
            }
            for r in ctx.retrieved_facts
        ],
    }


def _outcome_payload(outcome: MatchOutcome) -> Dict[str, Any]:
    return {
        "passport": outcome.passport.model_dump(mode="json"),
        "player_passports": [p.model_dump(mode="json") for p in outcome.player_passports],
        "wipe": outcome.wipe,
        "timeline": outcome.timeline,
        "export_dir": outcome.export_dir,
        "opted_in": outcome.opted_in,
    }


@app.get("/")
def root():
    return {
        "system": config.system.app_name,
        "version": config.system.version,
        "dashboard": "/dashboard/",
        "docs": "/docs",
    }


@app.get("/healthz")
def healthz():
    mem = orchestrator.memory
    return {
        "status": "ok",
        **mem.health(),
        "knowledge_graph": mem.graph.engine,
        "ledger": {"mode": orchestrator.ledger.mode, "address": orchestrator.ledger.address},
        "passport_nft": ({"mode": orchestrator.nft.mode, "address": orchestrator.nft.address}
                         if orchestrator.nft else "disabled"),
        "leaderboard": {"mode": orchestrator.leaderboard.mode, "address": orchestrator.leaderboard.address},
        "groq": "configured" if GroqClient().is_configured else "unset (template evidence)",
    }


@app.get("/scenarios")
def list_scenarios():
    return {
        "map": {"rooms": ROOMS, "edges": edges()},
        "scenarios": [
            {"key": s.key, "title": s.title, "description": s.description, "players": s.players,
             "expected_result": s.expected_result, "steps": len(s.steps)}
            for s in SCENARIOS.values()
        ],
    }


@app.post("/match/start")
def start_match(req: StartRequest):
    if req.players:
        scenario = Scenario(
            key="external",
            title="External match",
            description="Events supplied by an external game via POST /event.",
            players=req.players,
            impostors=req.impostors or [],
            steps=[],
        )
    else:
        if req.scenario not in SCENARIOS:
            raise HTTPException(status_code=400, detail=f"unknown scenario; available: {sorted(SCENARIOS)}")
        scenario = req.scenario
    try:
        session = orchestrator.start_match(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "match_id": session.match_id,
        "scenario": session.scenario.key,
        "commitments": session.role_book.public_view(),
        "ledger": {
            "mode": session.ledger.mode,
            "contract": session.ledger.address,
            "commit_tx": session.commit_receipt.tx_hash,
            "explorer_url": session.commit_receipt.explorer_url,
        },
        "player_passports": [p.model_dump(mode="json") for p in session.passports.values()],
        "memory_at_start": orchestrator.memory.stats(),
        "pre_start_wipe": session.pre_start_wipe,
    }


@app.post("/match/step")
def step_match():
    records = orchestrator.step()
    session = orchestrator.session
    return {
        "records": records,
        "done": bool(session and session.engine.done),
        "working_memory": orchestrator.memory.working.get_snapshot(),
        "memory": orchestrator.memory.stats(),
    }


@app.post("/event")
def receive_event(event: GameEvent):
    """Ingest one game event from any game: episodic -> situation -> semantic/graph -> (evidence)."""
    return pipeline_payload(orchestrator.ingest(event))


@app.post("/match/end")
def end_match():
    return _outcome_payload(orchestrator.end_match())


@app.post("/match/run")
def run_match(req: RunRequest):
    if req.scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"unknown scenario; available: {sorted(SCENARIOS)}")
    return _outcome_payload(orchestrator.run_scenario(req.scenario))


@app.get("/status")
def status():
    session = orchestrator.session
    return {
        "active_match": session.match_id if session else None,
        "scenario": session.scenario.key if session else None,
        "engine_done": bool(session and session.engine.done),
        "working_memory": orchestrator.memory.working.get_snapshot(),
    }


@app.get("/memory")
def memory_state():
    mem = orchestrator.memory
    graph = orchestrator.graph
    return {
        "stats": mem.stats(),
        "vector_engine": mem.vector_store.engine,
        "facts": [f.model_dump(mode="json") for f in mem.semantic.all_facts()],
        "rules": [r.model_dump(mode="json") for r in mem.semantic.get_rules()],
        "knowledge_graph": mem.graph.get_summary(),
        "last_retrieval": _context_payload(graph.memory_agent.last_context),
    }


@app.get("/memory/graph")
def memory_graph():
    return orchestrator.memory.graph.export()


@app.get("/evidence")
def evidence(q: str = Query(..., min_length=3), player: Optional[str] = None, k: int = Query(5, ge=1, le=20)):
    """Ad-hoc hybrid query, e.g. ?q=where was Blue right before the body was found&player=Blue"""
    focus = [player_node(player)] if player else []
    body = orchestrator.memory.working.last_body
    if body:
        _, body_focus = orchestrator.graph.memory_agent.evidence_query("opening")
        focus += body_focus
    ctx = orchestrator.graph.memory_agent.retrieve(q, focus=focus, top_k=k)
    return _context_payload(ctx)


@app.post("/reset")
def reset_state():
    """Abort any active match and wipe every memory layer."""
    return orchestrator.abort_match()


@app.get("/passports/last")
def last_passport():
    if orchestrator.last_outcome is None:
        raise HTTPException(status_code=404, detail="no finished match yet")
    return _outcome_payload(orchestrator.last_outcome)


@app.post("/leaderboard/opt-in")
def leaderboard_opt_in(req: OptInRequest):
    """A player from the last finished match chooses to publish their own result. Never automatic."""
    return orchestrator.opt_in_leaderboard(req.player)


@app.get("/leaderboard")
def leaderboard():
    lb = orchestrator.leaderboard
    return {
        "mode": lb.mode,
        "contract": lb.address,
        "note": "Opt-in only and separate from match memory, which is wiped every match.",
        "standings": orchestrator.leaderboard_standings(),
    }


DASHBOARD_DIR = PROJECT_ROOT / "dashboard" / "static"
ASSETS_DIR = PROJECT_ROOT / "assets"


@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse(url="/dashboard/")


if DASHBOARD_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
