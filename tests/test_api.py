import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from api import match_api
    from chain.ledger import MockLedger
    from orchestration.match_runner import MatchOrchestrator

    match_api.orchestrator = MatchOrchestrator(ledger=MockLedger(), export_dir=tmp_path)
    return TestClient(match_api.app)


def test_run_match_and_wipe(client):
    res = client.post("/match/run", json={"scenario": "reactor_lie"})
    assert res.status_code == 200
    body = res.json()
    assert body["passport"]["result"] == "CREW_VICTORY"
    assert body["wipe"]["wiped"] is True
    assert client.get("/memory").json()["stats"]["semantic_facts"] == 0
    assert client.get("/passports/last").json()["passport"]["match_id"] == body["passport"]["match_id"]


def test_step_through_match(client):
    start = client.post("/match/start", json={"scenario": "clean_getaway"}).json()
    assert len(start["commitments"]) == 4
    assert all(p["role"] is None for p in start["player_passports"])
    assert client.post("/match/start", json={"scenario": "reactor_lie"}).status_code == 409

    done = False
    while not done:
        done = client.post("/match/step").json()["done"]
    assert client.get("/memory").json()["stats"]["semantic_facts"] > 0
    hits = client.get("/evidence", params={"q": "who was in Reactor near the body", "player": "Green"}).json()
    assert hits["ranked"]

    end = client.post("/match/end").json()
    assert end["passport"]["result"] == "IMPOSTOR_VICTORY"
    assert {p["role"] for p in end["player_passports"]} == {"CREW", "IMPOSTOR"}


def test_external_game_via_event_endpoint(client):
    start = client.post("/match/start", json={"players": ["A", "B", "C"], "impostors": ["C"]}).json()
    mid = start["match_id"]
    events = [
        {"timestamp": 0, "match_id": mid, "action": "match_start", "location": "Hall", "detail": {"players": ["A", "B", "C"]}},
        {"timestamp": 3, "match_id": mid, "action": "move", "actor": "C", "location": "Lab"},
        {"timestamp": 4, "match_id": mid, "action": "move", "actor": "B", "location": "Lab"},
        {"timestamp": 6, "match_id": mid, "action": "kill", "actor": "C", "target": "B", "location": "Lab"},
        {"timestamp": 8, "match_id": mid, "action": "move", "actor": "C", "location": "Hall"},
        {"timestamp": 12, "match_id": mid, "action": "move", "actor": "A", "location": "Lab"},
        {"timestamp": 13, "match_id": mid, "action": "report", "actor": "A", "target": "B", "location": "Lab"},
    ]
    for e in events:
        assert client.post("/event", json=e).status_code == 200
    kill_payload = client.post("/event", json={
        "timestamp": 14, "match_id": mid, "action": "meeting_start", "meeting_id": 1, "location": "Lab",
    }).json()
    assert kill_payload["evidence"]["subject"] == "C"

    wrong = dict(events[1], match_id="someone-else", timestamp=20)
    assert client.post("/event", json=wrong).status_code == 409

    end = client.post("/match/end").json()
    assert end["passport"]["kill_count"] == 1 and end["wipe"]["wiped"]


def test_reset_endpoint(client):
    client.post("/match/start", json={"scenario": "reactor_lie"})
    for _ in range(14):
        client.post("/match/step")
    assert client.get("/memory").json()["stats"]["episodic_events"] > 0
    wipe = client.post("/reset").json()
    assert wipe["wiped"] is True
    assert client.get("/status").json()["active_match"] is None


def test_nft_and_opt_in_leaderboard_endpoints(client):
    assert client.post("/leaderboard/opt-in", json={"player": "Red"}).status_code == 409  # no finished match
    body = client.post("/match/run", json={"scenario": "reactor_lie"}).json()
    assert body["passport"]["nft"]["token_id"] == 1
    assert client.get("/leaderboard").json()["standings"] == []

    entry = client.post("/leaderboard/opt-in", json={"player": "Red"}).json()
    assert entry["won"] is True and entry["record"]["wins"] == 1
    assert client.post("/leaderboard/opt-in", json={"player": "Red"}).status_code == 409
    assert client.post("/leaderboard/opt-in", json={"player": "Nobody"}).status_code == 409
    assert client.get("/passports/last").json()["opted_in"]["Red"]["profile_address"] == entry["profile_address"]

    standings = client.get("/leaderboard").json()["standings"]
    assert [(r["player"], r["wins"], r["losses"]) for r in standings] == [("Red", 1, 0)]
    assert client.get("/memory").json()["stats"]["semantic_facts"] == 0


def test_health_and_dashboard(client):
    health = client.get("/healthz").json()
    assert health["ledger"]["mode"] == "mock"
    assert health["passport_nft"]["mode"] == "mock" and health["leaderboard"]["mode"] == "mock"
    assert client.get("/scenarios").json()["map"]["rooms"]
    assert client.get("/dashboard/").status_code == 200
