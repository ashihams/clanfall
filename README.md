# Clanfall

**A pluggable match-memory engine for social-deduction games.** It remembers everything that happens in a match, surfaces the evidence that matters at the meeting, then **forgets everything** when the match ends, and proves on-chain that roles were fixed before play started and that the event log was not edited afterwards.

Built for the Cambridge × Arcade AI Hackathon (Game Tech Track). Forked from [AeroCortex](https://github.com/VantyxLabs/AeroCortex): the hybrid memory core is reused, the UAV modules are gone.

```
game events ──► episodic log ──► IF-THEN rules ──► semantic facts ──► vector store (Tencent VectorDB / Pinecone / local)
                    │                                    │
                    │                                    └──► knowledge graph (NetworkX)
                    │
meeting called ──► hybrid retrieval  0.6 × vector + 0.4 × graph ──► ranked evidence card
                    │
match ends ──► keccak(event log) + role reveal in ONE tx ──► Match Passport ──► memory wipe (all layers → 0)
```

---

## Quick start (mock mode, no keys needed)

```powershell
cd clanfall
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

python demo.py          # two back-to-back matches with a memory wipe in between
python -m pytest        # 66 tests
python main.py --api    # API + dashboard on http://127.0.0.1:8000/dashboard/
```

Port 8000 in use? Set `API_PORT=8790` (or any free port) first.

## What the demo shows

| Match | Scenario | What happens | Outcome |
|---|---|---|---|
| 1 | `reactor_lie` | Blue kills Green in Electrical, then claims he was in Reactor. The engine catches the alibi contradiction (score ≈ 0.96) and the crew vote him out. | CREW_VICTORY |
| 2 | `clean_getaway` | Red kills Yellow in Reactor and tells the truth. Green wandered through Reactor, so the observable evidence points at an innocent player. The engine only knows what was public, and the impostor wins. | IMPOSTOR_VICTORY |

Between the matches, the demo prints every memory counter (episodic, semantic, vector, graph, evidence) dropping to zero, then asks match 2's engine about match 1's players and gets nothing back.

At the end of each match a verifiable record is written to `data/passports/<match_id>/`:

- `event_log.json`: the exact bytes that were hashed
- `match_passport.json`: host-signed outcome, revealed roles + salts, the ledger tx, and the outcome sigil
- `player_passports.json`: per-player wallet-signed passports

Verify any record independently:

```powershell
python -m chain.verify data/passports/<match_id>            # hash, signatures, every seat commitment
python -m chain.verify data/passports/<match_id> --onchain  # also reads the MatchLedger contract
```

---

## How it works

### Simulation (`simulation/`)
Four scripted players on a five-room map (Cafeteria, MedBay, Storage, Electrical, Reactor). Timestamped moves and tasks, one impostor, one kill, a report triggers a meeting, everyone claims a location, majority vote (strict majority over every other option including skip, ties eject nobody), win conditions checked after every eject.

### Memory (`memory/`), wiped every match
| Layer | Holds | Implementation |
|---|---|---|
| Working | phase, clock, who's alive, locations, current meeting | `working_memory.py` |
| Episodic | append-only, timestamped event log (the thing that gets hashed) | `episodic_memory.py` |
| Semantic | facts derived by IF-THEN rules: presence near body, last seen with, alibi contradiction, alibi confirmed | `semantic_memory.py` |
| Graph | players, rooms, events, facts and their relationships | `knowledge_graph.py` (NetworkX; backend is pluggable) |

**Kills are hidden.** They go into the hash-committed episodic log but never into facts, the graph, or working memory, so the engine can't leak the impostor. It has to *infer* from public events, like a player would.

Retrieval reuses the AeroCortex hybrid fusion: each score list is min-max normalised, then `final = 0.6 × vector_similarity + 0.4 × graph_relevance`. Graph relevance is a fact-type weight times proximity (1 / (1 + hops)) to the body, room and victim.

`MatchMemory.reset()` clears every layer **and every vector backend** (remote and local mirror), and returns before/after counts. `MatchOrchestrator.end_match()` raises if anything is left. Nothing is ever written to disk except the exported passport record.

### Pipeline (`orchestration/`, `agents/`)
LangGraph: `ingest → situation → consolidate → (memory → evidence) | END`. The situation agent is deterministic and only asks for evidence at `MEETING_START` and `VOTING_OPEN`. The evidence agent writes a template headline (Groq can optionally rephrase it).

### Chain (`chain/`)
1. **Match start:** every player gets a fresh wallet. Roles are committed as `keccak256(abi.encodePacked(uint8 role, uint256 seat, bytes32 salt, bytes32 matchId))` via `MatchLedger.commitRoles`. Players receive a signed Player Passport with the role hidden.
2. **Match end:** `MatchLedger.revealAndFinalize` reveals roles + salts **and records the event-log hash in the same transaction**. The contract re-computes each commitment and rejects any mismatch.
3. The host signs a **Match Passport** (EIP-191 over canonical JSON); player passports are re-issued with roles revealed.

`CHAIN_MODE=mock` uses `MockLedger`, an in-process replica with the same checks and error names. Tests run the real Solidity contract on a local EVM (`eth-tester`) as well, including tamper detection.

### NFT Match Passport (`chain/nft.py`, `MatchPassport.sol`)
At match end, after the reveal, one ERC-721 (OpenZeppelin v5) is minted per match. `mintPassport` refuses unless MatchLedger already shows the match as **Revealed** with the **same event-log hash**, so the token is anchored to the commit-reveal record. The token URI is an on-chain `data:` JSON with the result, counts, players and roles, the event-log hash, and the outcome sigil as the image. The NFT record goes into the signed Match Passport; `chain.verify --onchain` checks the token, its stored hash, and its metadata. A mint failure never blocks a match from finishing.

### Opt-in leaderboard (`chain/leaderboard.py`, `ClanfallLeaderboard.sol`): separate from match memory
Match memory is wiped every match and the leaderboard never reads it. Nothing is published unless a player asks:
1. The player's per-match wallet signs a consent message naming the profile wallet they want to publish under (match wallets are throwaway, so a record needs a wallet the player keeps).
2. The host checks that signature against the revealed Player Passport, then signs an attestation of the result (won or lost).
3. **The profile wallet itself** calls `optInAndSubmit`. The contract checks that the match is revealed on MatchLedger, that the attestation came from the host (so nobody can claim a win they didn't get), and that the match hasn't already been submitted.

On testnet the host sends the profile wallet a tiny gas top-up first. In this simulation the bots' wallets live in `data/profiles/` (git-ignored); in a real game, players bring their own.

### Outcome sigils (`assets/sigils/`)
Static images, one per outcome. `manifest.json` maps `CREW_VICTORY` / `IMPOSTOR_VICTORY` / `UNRESOLVED` to an SVG in the same folder; the chosen one goes into the Match Passport and the NFT metadata.

---

## Going live

Everything falls back to mock/local automatically, so each of these is optional and independent.

### Tencent Cloud VectorDB
1. Create a VectorDB instance (requires Tencent Cloud real-name verification), enable the public endpoint, and add your IP to the allowlist.
2. In `.env`: `TENCENT_VDB_URL`, `TENCENT_VDB_KEY` (and `TENCENT_VDB_USERNAME` if not `root`).
3. Smoke test: `python scripts/tencent_hello.py` (uses a throwaway collection and drops it).
4. `VECTOR_BACKEND=auto` (default) will use Tencent; writes are mirrored to the local store so retrieval survives a dropout.

### Pinecone (used when Tencent isn't configured, as in AeroCortex)
1. In `.env`: `PINECONE_API_KEY`, and `PINECONE_INDEX` (a 64-dim cosine serverless index; created if it doesn't exist).
2. With `VECTOR_BACKEND=auto`, the order is Tencent, then Pinecone, then local. Force it with `VECTOR_BACKEND=pinecone`.
3. The index can be shared with other apps. Each Clanfall process writes only to its own `clanfall-<id>` namespace and deletes exactly that namespace on every wipe (and on exit).
4. Serverless Pinecone is eventually consistent, so the backend counts only what it wrote (the wipe check stays exact), waits briefly for fresh writes, never returns vectors from a wiped match, and answers from the local mirror if Pinecone hasn't caught up yet.

### Testnet MatchLedger (Base Sepolia by default)
1. Get a **testnet-only** key and fund it from a Base Sepolia faucet.
2. `.env`: `CHAIN_RPC_URL`, `CHAIN_PRIVATE_KEY`.
3. `python -m chain.contracts.deploy` deploys whatever isn't configured yet (MatchLedger, MatchPassport, ClanfallLeaderboard) and prints the `.env` lines: `CHAIN_MODE=testnet`, `MATCH_LEDGER_ADDRESS`, `PASSPORT_NFT_ADDRESS`, `LEADERBOARD_ADDRESS`.
4. Run `python demo.py --opt-in Red Blue`. The Match Passport now has real tx hashes, an NFT link on Basescan, and the opt-in submissions. If a testnet commit fails mid-demo, that match falls back to the mock ledger and says so.

Current Base Sepolia deployment:

| Contract | Address |
|---|---|
| MatchLedger | [`0x843A4FAf20DF36a527CbfDb18Dc7f2AE86bEC8DD`](https://sepolia.basescan.org/address/0x843A4FAf20DF36a527CbfDb18Dc7f2AE86bEC8DD) |
| MatchPassport (ERC-721) | [`0xB12986E9ca8F396B678009a4983487614b045F9C`](https://sepolia.basescan.org/address/0xB12986E9ca8F396B678009a4983487614b045F9C) |
| ClanfallLeaderboard | [`0x7CaFB667340AfDf22A661321aC3Be6fEb3C6946C`](https://sepolia.basescan.org/address/0x7CaFB667340AfDf22A661321aC3Be6fEb3C6946C) |

Recompile the contracts (only if you edit them): `python -m chain.contracts.compile` (solc 0.8.24 via py-solc-x; OpenZeppelin v5.0.2 is cloned into `chain/contracts/lib/` on first run).

### Groq (optional)
`GROQ_ENABLED=true` + `GROQ_API_KEY` rephrases evidence headlines. Off by default; the templates are fine.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | vector backend, graph, ledger mode, Groq |
| GET | `/scenarios` | map + built-in scenarios |
| POST | `/match/start` | `{"scenario": "reactor_lie"}`, or `{"players": [...], "impostors": [...]}` for an external game |
| POST | `/match/step` | advance the scripted match one step |
| POST | `/event` | ingest one `GameEvent` from any game |
| POST | `/match/end` | hash + reveal + passports + wipe |
| POST | `/match/run` | run a whole scenario in one call |
| GET | `/status`, `/memory`, `/memory/graph` | live state |
| GET | `/evidence?q=...&player=...&k=5` | ad-hoc hybrid query |
| POST | `/reset` | abort the match and wipe memory |
| GET | `/passports/last` | last finished match record (includes the NFT) |
| POST | `/leaderboard/opt-in` | `{"player": "Red"}`: a player from the last match chooses to publish their result |
| GET | `/leaderboard` | opt-in standings |

Set `API_KEY` to require an `X-API-Key` header (dashboard: `/dashboard/?key=...`).

### Plugging in another game
Call `POST /match/start` with your players and impostors, stream your game's events to `POST /event`, call `POST /match/end`. The engine only needs `seq`/`timestamp`, `actor`, `action`, `location`, and `target`. See `models.GameEvent`.

---

## Layout

```
agents/          situation, memory (hybrid retrieval), learning (within-match), evidence
api/             FastAPI app
chain/           hashing, commit-reveal, ledgers (mock + web3), passports, NFT, leaderboard, verify CLI, contracts/
config/          settings + config.yaml (env overrides)
dashboard/       static SPA (map, event feed, memory counters, evidence, passport)
memory/          working, episodic, semantic, knowledge graph, vector store, MatchMemory
orchestration/   LangGraph pipeline + MatchOrchestrator
simulation/      map, scenarios, match engine
assets/sigils/   outcome sigils + manifest
tests/           memory wipe, rules, retrieval, engine, chain (mock + EVM), API
```

## From AeroCortex

| AeroCortex | Clanfall |
|---|---|
| Telemetry anomaly detection | Deterministic situation agent over game events |
| Mission episodes (persistent) | Match episodic log (wiped every match, hashed on-chain) |
| Recovery IF-THEN rules | Deduction rules (presence, last seen, alibi contradiction/confirmation) |
| Neo4j / NetworkX graph | NetworkX graph (pluggable backend) |
| Pinecone + Chroma fallback | Tencent VectorDB, then Pinecone, then local |
| 0.6 / 0.4 hybrid retrieval | Unchanged |
| Cross-mission learning | **Removed on purpose**: memory never outlives a match |
| UAV safety agent, planner, AWS/Lambda | Deleted |

## Stretch status
Built: NFT Match Passport (ERC-721) and the opt-in leaderboard, both live on Base Sepolia.
Not built: Tencent TGDB graph backend, per-player avatars.
