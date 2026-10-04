import pytest
from fastapi.testclient import TestClient

from bridge.server import app

client = TestClient(app)


def test_bridge_health():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "mock_chain" in data
    assert "mock_memory" in data


def test_bridge_deal_and_game_flow():
    # 1. POST /deal
    deal_res = client.post("/deal", json={
        "players": ["Red", "Blue", "Green", "Yellow"],
        "impostors": ["Red"]
    })
    assert deal_res.status_code == 200
    deal_data = deal_res.json()
    assert "match_id" in deal_data
    assert len(deal_data["commitments"]) == 4

    match_id = deal_data["match_id"]

    # 2. POST /kill
    kill_res = client.post("/kill", json={
        "match_id": match_id,
        "killer": "Red",
        "victim": "Blue",
        "location": "Electrical"
    })
    assert kill_res.status_code == 202

    # 3. POST /report -> verify surfaced memory evidence
    rep_res = client.post("/report", json={
        "match_id": match_id,
        "reporter": "Green"
    })
    assert rep_res.status_code == 200
    rep_data = rep_res.json()
    assert rep_data["surfaced"] is True
    assert "evidence" in rep_data
    assert len(rep_data["evidence"]) > 0

    # 4. POST /vote
    vote_res = client.post("/vote", json={
        "match_id": match_id,
        "voter": "Green",
        "target": "Red"
    })
    assert vote_res.status_code == 202

    # 5. POST /eject
    eject_res = client.post("/eject", json={
        "match_id": match_id,
        "ejected": "Red"
    })
    assert eject_res.status_code == 202

    # 6. POST /gameover
    over_res = client.post("/gameover", json={
        "match_id": match_id,
        "winner": "CREW",
        "reason": "Impostor ejected"
    })
    assert over_res.status_code == 200
    assert over_res.json()["status"] == "finalized"


def test_per_match_memory_wipe():
    """
    Critical requirement: Run two matches back to back with /reset in between.
    Confirm /report's evidence query in match 2 returns zero results referencing match 1.
    """
    # --- MATCH 1 ---
    client.post("/reset")
    deal1 = client.post("/deal", json={"players": ["Alice", "Bob"], "impostors": ["Alice"]}).json()
    m1_id = deal1["match_id"]

    # Ingest kill event in Match 1
    client.post("/kill", json={"match_id": m1_id, "killer": "Alice", "victim": "Bob", "location": "MedBay"})

    # Check evidence in Match 1
    ev1 = client.post("/report", json={"match_id": m1_id, "reporter": "Alice"}).json()["evidence"]
    assert "body" in ev1.lower() or "surfaced" in ev1.lower()

    client.post("/gameover", json={"match_id": m1_id, "winner": "IMPOSTOR"})

    # --- RESET ---
    reset_res = client.post("/reset")
    assert reset_res.status_code == 200

    # --- MATCH 2 ---
    deal2 = client.post("/deal", json={"players": ["Charlie", "David"], "impostors": ["Charlie"]}).json()
    m2_id = deal2["match_id"]
    assert m2_id != m1_id

    # Query evidence at start of Match 2 before any events
    ev2 = client.post("/report", json={"match_id": m2_id, "reporter": "Charlie"}).json()["evidence"]

    # Confirm match 2 evidence contains ZERO references to match 1 entities ("Bob", "MedBay")
    assert "Bob" not in ev2
    assert "MedBay" not in ev2
