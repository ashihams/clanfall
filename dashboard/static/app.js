const KEY = new URLSearchParams(location.search).get("key") || "";
const COLORS = { Red: "#ff5a5a", Blue: "#4f8dff", Green: "#3fd17a", Yellow: "#ffd23f" };
const ROOM_POS = {
  Cafeteria: [210, 50], MedBay: [70, 150], Storage: [210, 160], Electrical: [340, 250], Reactor: [110, 260],
};
const $ = (id) => document.getElementById(id);
let mapData = null, scenarios = [], playing = false, busy = false, dead = new Set();

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...(KEY ? { "X-API-Key": KEY } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function feed(text, cls = "", t = null) {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<span class="t">${t === null ? "" : t.toFixed(0) + "s"}</span><span>${text}</span>`;
  $("feed").appendChild(li);
  $("feed").scrollTop = $("feed").scrollHeight;
}

function describe(e) {
  const d = e.detail || {};
  switch (e.action) {
    case "match_start": return `${d.players.join(", ")} spawn in ${e.location}`;
    case "move": return `${e.actor} walks ${d.from} → ${e.location}`;
    case "task": return `${e.actor} completes a task in ${e.location}`;
    case "kill": return "▒▒ sealed event written to the log — memory cannot read it ▒▒";
    case "report": return `${e.actor} REPORTS ${e.target}'s body in ${e.location}`;
    case "meeting_start": return `EMERGENCY MEETING #${e.meeting_id}`;
    case "claim": return `${e.actor}: “I was in ${e.location} at ${e.claimed_at}s.”`;
    case "voting_open": return "Voting opens";
    case "vote": return `${e.actor} votes ${e.target ?? "skip"}`;
    case "eject": return `${e.target ?? "Nobody"} was ejected  ${JSON.stringify(d.tally || {})}`;
    case "game_over": return `GAME OVER — ${d.result} (${d.reason})`;
  }
  return e.action;
}

function drawMap(working) {
  const svg = $("map");
  if (!mapData) return;
  let html = "";
  for (const [a, b] of mapData.edges) {
    const [x1, y1] = ROOM_POS[a], [x2, y2] = ROOM_POS[b];
    html += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#222b37" stroke-width="3"/>`;
  }
  const body = working && working.last_body;
  for (const room of mapData.rooms) {
    const [x, y] = ROOM_POS[room];
    const hot = body && body.room === room;
    html += `<rect x="${x - 48}" y="${y - 20}" width="96" height="40" rx="6" fill="#0d1218" stroke="${hot ? "#ff4d5e" : "#2c3846"}" stroke-width="${hot ? 2 : 1}"/>`;
    html += `<text x="${x}" y="${y - 25}" text-anchor="middle">${room}</text>`;
  }
  const locs = (working && working.locations) || {};
  const byRoom = {};
  for (const [p, room] of Object.entries(locs)) (byRoom[room] = byRoom[room] || []).push(p);
  for (const [room, players] of Object.entries(byRoom)) {
    const [x, y] = ROOM_POS[room] || [0, 0];
    players.forEach((p, i) => {
      const cx = x - 30 + i * 20, isDead = dead.has(p) || (working.alive && !working.alive.includes(p));
      html += `<circle cx="${cx}" cy="${y}" r="7" fill="${COLORS[p] || "#aaa"}" opacity="${isDead ? 0.25 : 1}"/>`;
      if (isDead) html += `<text x="${cx}" y="${y + 4}" text-anchor="middle" fill="#fff">✕</text>`;
    });
  }
  svg.innerHTML = html;
  $("clock").textContent = `t = ${(working && working.clock) || 0}s`;
}

const STAT_KEYS = [
  ["episodic_events", "episodic events"], ["semantic_facts", "semantic facts"], ["vector_documents", "vectors"],
  ["graph_nodes", "graph nodes"], ["graph_edges", "graph edges"], ["evidence_items", "evidence"],
  ["stored_documents", "mongo docs"],
];
function drawStats(stats, flash = false) {
  $("stats").innerHTML = STAT_KEYS.map(([k, label]) =>
    `<div class="stat${flash ? " flash" : ""}"><b>${stats[k]}</b><span>${label}</span></div>`).join("");
  if (flash) setTimeout(() => document.querySelectorAll(".stat").forEach((s) => s.classList.remove("flash")), 1600);
}

function drawEvidence(ev) {
  const contradiction = ev.fact_type === "ALIBI_CONTRADICTION";
  const card = `<div class="card${contradiction ? " contradiction" : ""}">
    <div class="label">MEETING #${ev.meeting_id} · ${ev.stage.toUpperCase()}${contradiction ? " · LIVE CATCH" : ""}</div>
    <div>${esc(ev.headline)}</div>
    <div class="score">score ${ev.final_score.toFixed(2)} = 0.6 × vector ${ev.vector_similarity.toFixed(2)} + 0.4 × graph ${ev.graph_relevance.toFixed(2)} · ${ev.source}</div>
  </div>`;
  const box = $("evidence");
  if (box.querySelector(".muted")) box.innerHTML = "";
  box.insertAdjacentHTML("afterbegin", card);
}

async function refreshFacts() {
  const mem = await api("/memory");
  $("vector-engine").textContent = mem.vector_engine;
  drawStats(mem.stats);
  $("facts").innerHTML = mem.facts.length
    ? mem.facts.map((f) => `<li><i>${f.fact_type} · conf ${f.confidence}</i>${esc(f.text)}</li>`).join("")
    : '<li class="muted">None yet.</li>';
}

function resetBoard() {
  $("feed").innerHTML = "";
  $("evidence").innerHTML = '<p class="muted">Evidence appears when a meeting opens.</p>';
  $("passport").classList.add("hidden");
  $("lb-optin").innerHTML = "";
  dead = new Set();
}

async function start() {
  resetBoard();
  const res = await api("/match/start", "POST", { scenario: $("scenario").value });
  $("ledger-mode").textContent = `${res.ledger.mode} ledger`;
  $("commitments").innerHTML = "<tbody>" + res.commitments.map((c) =>
    `<tr><td style="color:${COLORS[c.player]}">${c.seat} ${c.player}</td><td>${c.commitment.slice(0, 26)}…</td><td class="muted">role sealed</td></tr>`).join("") + "</tbody>";
  $("commit-tx").innerHTML = `commit tx ${res.ledger.explorer_url ? `<a href="${res.ledger.explorer_url}" target="_blank">${res.ledger.commit_tx}</a>` : res.ledger.commit_tx}`;
  feed(`Memory at start: ${res.memory_at_start.episodic_events} events, ${res.memory_at_start.semantic_facts} facts — a blank slate.`, "fact");
  feed(`Roles committed on the ${res.ledger.mode} ledger before any play.`, "fact");
  $("btn-step").disabled = $("btn-play").disabled = false;
  $("btn-start").disabled = true;
  await step();
}

async function step() {
  if (busy) return false;
  busy = true;
  try {
    const res = await api("/match/step", "POST");
    let sawEvidence = false;
    for (const rec of res.records) {
      const e = rec.event;
      if (e.action === "report" && e.target) dead.add(e.target);
      const caught = (rec.logs || []).some((l) => l.includes("ALIBI_CONTRADICTION"));
      feed(esc(describe(e)), e.action === "kill" ? "sealed" : (caught ? "catch" : e.action), e.timestamp);
      for (const log of rec.logs || []) {
        if (log.includes("[SEMANTIC]")) feed("memory → " + esc(log.split("] ").slice(1).join("] ")), log.includes("CONTRADICTION") ? "catch" : "fact");
      }
      if (rec.evidence) { drawEvidence(rec.evidence); sawEvidence = true; }
    }
    drawMap(res.working_memory);
    await refreshFacts();
    if (res.done) await finish();
    return sawEvidence;
  } catch (err) {
    feed("error: " + esc(err.message), "catch");
    stopPlay();
  } finally {
    busy = false;
  }
}

async function finish() {
  stopPlay();
  $("btn-step").disabled = $("btn-play").disabled = true;
  const out = await api("/match/end", "POST");
  const p = out.passport;
  drawStats(out.wipe.after, true);
  $("facts").innerHTML = '<li class="muted">Wiped.</li>';
  feed(`MEMORY WIPED — ${out.wipe.before.semantic_facts} facts, ${out.wipe.before.graph_nodes} graph nodes, ${out.wipe.before.episodic_events} events → 0`, "game_over");
  $("commitments").innerHTML = "<tbody>" + p.players.map((s) =>
    `<tr><td style="color:${COLORS[s.player]}">${s.seat} ${s.player}</td><td>${s.role}</td><td class="${s.verified_locally && s.verified_on_ledger ? "ok" : "bad"}">${s.verified_on_ledger ? "✓ commitment opens" : "✗ mismatch"}</td></tr>`).join("") + "</tbody>";
  const link = (url, tx) => url ? `<a href="${url}" target="_blank">${tx}</a>` : tx;
  const img = p.sigil.image ? `/assets/sigils/${p.sigil.image}` : "";
  $("passport").innerHTML = `
    <div>${img ? `<img src="${img}" alt="${esc(p.sigil.title)}">` : ""}</div>
    <div>
      <h2>Match passport</h2>
      <h3>${p.result.replace("_", " ")}</h3>
      <div class="muted">${esc(p.reason)}</div>
      <table><tbody>
        <tr><td>duration</td><td>${p.duration_s}s</td></tr>
        <tr><td>tasks / meetings / kills</td><td>${p.task_count} / ${p.meeting_count} / ${p.kill_count}</td></tr>
        <tr><td>ejected</td><td>${p.ejections.join(", ") || "—"}</td></tr>
        <tr><td>events</td><td>${p.event_count}</td></tr>
        <tr><td>host signature</td><td>${p.host_address}</td></tr>
      </tbody></table>
    </div>
    <div>
      <h2>Verification <span class="muted">${p.ledger.mode}</span></h2>
      <table><tbody>
        <tr><td>event log hash</td><td>${p.event_log_hash}</td></tr>
        <tr><td>on-ledger hash</td><td class="${p.event_log_hash === p.ledger.on_chain_event_log_hash ? "ok" : "bad"}">${p.ledger.on_chain_event_log_hash}</td></tr>
        <tr><td>ledger phase</td><td>${p.ledger.final_phase}</td></tr>
        <tr><td>commit tx</td><td>${link(p.ledger.commit_explorer_url, p.ledger.commit_tx)}</td></tr>
        <tr><td>reveal tx</td><td>${link(p.ledger.reveal_explorer_url, p.ledger.reveal_tx)}</td></tr>
        ${nftRows(p, link)}
        <tr><td>record</td><td>${esc(out.export_dir || "—")}</td></tr>
      </tbody></table>
    </div>`;
  $("passport").classList.remove("hidden");
  $("btn-start").disabled = false;
  drawOptIn(p.players.map((s) => s.player), out.opted_in || {});
}

function nftRows(p, link) {
  if (p.nft) {
    const token = p.nft.token_url ? `<a href="${p.nft.token_url}" target="_blank">token #${p.nft.token_id}</a>` : `token #${p.nft.token_id}`;
    return `<tr><td>passport NFT</td><td class="ok">${token} · ${p.nft.mode} · ${p.nft.contract}</td></tr>
      <tr><td>mint tx</td><td>${link(p.nft.mint_explorer_url, p.nft.mint_tx)}</td></tr>`;
  }
  return p.nft_error ? `<tr><td>passport NFT</td><td class="bad">not minted: ${esc(p.nft_error)}</td></tr>` : "";
}

function drawOptIn(players, optedIn) {
  $("lb-optin").innerHTML = players.map((name) => optedIn[name]
    ? `<span class="done">${name} published ✓</span>`
    : `<button data-player="${name}" style="border-color:${COLORS[name] || "var(--line)"}">Publish ${name}'s result</button>`).join("");
  $("lb-optin").querySelectorAll("button").forEach((b) => { b.onclick = () => optIn(b); });
}

async function optIn(button) {
  const name = button.dataset.player;
  button.disabled = true;
  button.textContent = `publishing ${name}…`;
  try {
    const e = await api("/leaderboard/opt-in", "POST", { player: name });
    const tx = e.explorer_url ? ` <a href="${e.explorer_url}" target="_blank">tx</a>` : "";
    button.outerHTML = `<span class="done">${name} ${e.won ? "won" : "lost"} as ${e.role} → ${e.record.wins}W/${e.record.losses}L${tx}</span>`;
    feed(`${name} chose to publish their result to the leaderboard (${e.mode}). Match memory was not touched.`, "fact");
    await refreshLeaderboard();
  } catch (err) {
    button.disabled = false;
    button.textContent = `Publish ${name}'s result`;
    feed("opt-in failed: " + esc(err.message), "catch");
  }
}

async function refreshLeaderboard() {
  const lb = await api("/leaderboard");
  $("lb-mode").textContent = `${lb.mode} · ${lb.contract}`;
  $("lb-table").innerHTML = "<tbody>" + (lb.standings.length
    ? `<tr><td class="muted">player</td><td class="muted">wins</td><td class="muted">losses</td><td class="muted">profile wallet</td></tr>` +
      lb.standings.map((r) => `<tr><td style="color:${COLORS[r.player] || "inherit"}">${esc(r.player || "—")}</td><td>${r.wins}</td><td>${r.losses}</td><td>${r.address}</td></tr>`).join("")
    : `<tr><td class="muted">No one has opted in yet.</td></tr>`) + "</tbody>";
}

function stopPlay() { playing = false; $("btn-play").textContent = "▶ Auto-play"; }

async function play() {
  if (playing) return stopPlay();
  playing = true;
  $("btn-play").textContent = "❚❚ Pause";
  while (playing) {
    const sawEvidence = await step();
    await new Promise((r) => setTimeout(r, sawEvidence ? 2600 : 650));
  }
}

async function wipe() {
  stopPlay();
  const res = await api("/reset", "POST");
  resetBoard();
  drawStats(res.after, true);
  $("facts").innerHTML = '<li class="muted">Wiped.</li>';
  feed(`Manual wipe: ${res.before.semantic_facts} facts / ${res.before.episodic_events} events → 0`, "game_over");
  $("btn-start").disabled = false;
  $("btn-step").disabled = $("btn-play").disabled = true;
  drawMap(null);
}

async function init() {
  const [health, sc] = await Promise.all([api("/healthz"), api("/scenarios")]);
  mapData = sc.map;
  scenarios = sc.scenarios;
  $("health").innerHTML = `<span>vectors <b>${health.vector_store.engine}</b></span><span>remote <b>${health.vector_store.remote} ${health.vector_store.remote_status}</b></span><span>graph <b>${health.graph.engine}${health.graph.remote !== "none" ? ` (${health.graph.remote} ${health.graph.remote_status})` : ""}</b></span><span>mongo <b>${health.document_store.status}</b></span><span>ledger <b>${health.ledger.mode}</b></span><span>NFT <b>${health.passport_nft.mode || health.passport_nft}</b></span><span>leaderboard <b>${health.leaderboard.mode}</b></span>`;
  $("scenario").innerHTML = scenarios.map((s) => `<option value="${s.key}">${esc(s.title)}</option>`).join("");
  const showDesc = () => { $("scenario-desc").textContent = scenarios.find((s) => s.key === $("scenario").value)?.description || ""; };
  $("scenario").onchange = showDesc;
  showDesc();
  $("btn-start").onclick = start;
  $("btn-step").onclick = step;
  $("btn-play").onclick = play;
  $("btn-reset").onclick = wipe;
  drawMap(null);
  await refreshFacts();
  await refreshLeaderboard();
}

init().catch((err) => feed("cannot reach API: " + esc(err.message), "catch"));
