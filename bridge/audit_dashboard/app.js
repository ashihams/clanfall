const COLOR_MAP = {
  Red: "#ff3366",
  Blue: "#00d4ff",
  Green: "#00ff88",
  Yellow: "#ffd700",
  Orange: "#ff8a00",
  Pink: "#ff4fd8",
  Purple: "#b47aff",
  Black: "#8892a6",
  White: "#f4f4f4",
  Brown: "#c07a4a",
};

document.addEventListener("DOMContentLoaded", initDashboard);

function initDashboard() {
  bindNav();
  setupEvents();
  setupShell();
  setupSSE();
  tickClock();
  refreshAll();
  setInterval(refreshAll, 900);
  setInterval(tickClock, 1000);
}

function bindNav() {
  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((b) => b.classList.remove("is-active"));
      document.querySelectorAll(".deck").forEach((d) => d.classList.remove("is-active"));
      btn.classList.add("is-active");
      const deck = document.getElementById(`deck-${btn.dataset.deck}`);
      if (deck) deck.classList.add("is-active");
    });
  });
}

function tickClock() {
  const el = document.getElementById("sys-clock");
  if (el) el.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false });
}

async function refreshAll() {
  await Promise.all([fetchHealth(), fetchLive(), fetchPassport()]);
}

async function fetchHealth() {
  try {
    const data = await getJSON("/health");
    const el = document.getElementById("health");
    if (!el || !data) return;
    el.innerHTML = `
      <span class="cyber-badge status-pulse"><span class="led green"></span> BRIDGE: ${esc(data.status).toUpperCase()}</span>
      <span class="cyber-badge"><span class="led ${data.pygame_live ? "green" : "red"}"></span> PYGAME: ${data.pygame_live ? "LIVE" : "OFFLINE"}</span>
      <span class="cyber-badge"><span class="led cyan"></span> CHAIN: ${data.mock_chain ? "MOCK LEDGER" : "SEPOLIA"}</span>
      <span class="cyber-badge"><span class="led magenta"></span> MEMORY: ${data.mock_memory ? "MOCK" : "AEROCORTEX"}</span>
    `;
  } catch (_) { /* bridge down */ }
}

async function fetchLive() {
  try {
    const data = await getJSON("/game/live");
    renderLive(data);
  } catch (_) { /* ignore */ }
}

async function fetchPassport() {
  try {
    const res = await fetch("/passports/last");
    if (!res.ok) return;
    const data = await res.json();
    renderPassport(data.passport || data);
  } catch (_) { /* no passport */ }
}

function renderLive(data) {
  const game = data.game || {};
  const session = data.session || {};
  const memory = data.memory || {};
  const live = !!data.pygame_live;
  const crew = game.crew || [];
  const alive = crew.filter((c) => c.alive).length;
  const tasksDone = (game.tasks || []).filter((t) => t.done).length;
  const sab = game.sabotage || {};
  const meeting = game.meeting || {};

  setText("hud-pygame", live ? "LINK LOCKED" : "NO SIGNAL");
  setHTML("hud-pygame-sub", live ? `HEARTBEAT ${fmtAge(data.updated_at)}` : '<button class="cyber-btn-ghost" onclick="processCommand(\'launch\')" style="margin-top:2px; border-color:var(--accent); color:var(--accent);">▶ LAUNCH CLIENT</button>');
  setText("hud-phase", (game.phase || session.phase || "IDLE").toUpperCase());
  setText("hud-match-id", session.match_id ? `ID ${session.match_id.slice(0, 12)}` : "NO SESSION");
  setText("hud-crew", `${alive} / ${crew.length || session.players.length} ALIVE`);
  setText("hud-mode", `MODE: ${game.mode || "—"} · R${game.round || 1}`);
  setText("hud-tasks", `${game.missions_done ?? tasksDone} / ${game.missions_total || 8}`);
  setText("hud-tokens", `NC ${game.tokens ?? "—"}`);
  const sabLabel = sab.reactor ? "REACTOR MELT" : sab.lights ? "LIGHTS DOWN" : "CLEAR";
  setText("hud-sabotage", sabLabel);
  setText("hud-fps", `FPS ${game.fps || 0}`);
  setText("map-live", live ? "● LIVE" : "● DARK");
  setText("footer-status", `PYGAME ${live ? "ONLINE" : "OFFLINE"} · EVENTS ${memory.episodic_events || 0} · KECCAK-256`);

  if (meeting.evidence) setText("evidence-text", meeting.evidence);
  else if (memory.last_evidence) setText("evidence-text", memory.last_evidence);

  setText("summary-match-id", session.match_id || "—");
  setText("summary-outcome", session.winner || (game.phase === "gameover" ? "FINAL" : "PENDING"));
  setText("summary-duration", `${session.duration_s || 0}s`);
  setText("summary-events", `${memory.episodic_events || (data.events || []).length}`);
  setText("passport-badge", `STATUS: ${(session.phase || "IDLE").toUpperCase()}`);

  renderMinimap(crew, game.map);
  renderCrewTable(crew, session.impostors || []);
  renderTasks(game.tasks || [], game.missions_done, game.missions_total);
  renderSabotage(sab, game.cooldowns || {}, meeting);
  renderOccupancy(crew);
  renderMemory(memory, data.events || []);
  renderWallet(game, session);
  renderProof(session, live);
  if (!live && !(data.events || []).length) {
    /* keep historical feed from SSE */
  } else {
    mergeEventLog(data.events || []);
  }
}

function renderMinimap(crew, map) {
  const svg = document.getElementById("minimap");
  const legend = document.getElementById("map-legend");
  if (!svg) return;
  const w = 640, h = 360;
  const rooms = [
    [80, 140, 90, 70, "REACTOR"],
    [190, 70, 90, 60, "U.ENG"],
    [190, 240, 90, 60, "L.ENG"],
    [300, 110, 80, 50, "SEC"],
    [300, 180, 90, 50, "ELEC"],
    [410, 70, 140, 90, "CAFE"],
    [560, 40, 60, 50, "WPN"],
    [430, 200, 80, 50, "STOR"],
    [530, 210, 70, 50, "SHLD"],
  ];
  let html = `<rect width="${w}" height="${h}" fill="#07070d"/>`;
  html += `<path d="M40 180 H600 M320 20 V340" stroke="rgba(0,255,136,0.12)" />`;
  rooms.forEach(([x, y, rw, rh, label]) => {
    html += `<rect x="${x}" y="${y}" width="${rw}" height="${rh}" fill="rgba(0,255,136,0.04)" stroke="rgba(0,212,255,0.35)"/>`;
    html += `<text x="${x + 8}" y="${y + 16}" fill="#6b7280" font-size="9" font-family="Share Tech Mono">${label}</text>`;
  });
  crew.forEach((c) => {
    const nx = Math.min(0.98, Math.max(0.02, c.nx ?? (c.x || 0) / ((map && map.width) || 5120)));
    const ny = Math.min(0.98, Math.max(0.02, c.ny ?? (c.y || 0) / ((map && map.height) || 2880)));
    const cx = nx * w;
    const cy = ny * h;
    const fill = COLOR_MAP[c.color] || "#00ff88";
    const r = c.kind === "player" ? 7 : 5;
    html += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${c.alive ? fill : "#331018"}" stroke="${c.alive ? fill : "#ff3366"}" stroke-width="1.5" opacity="${c.alive ? 1 : 0.5}"/>`;
    html += `<text x="${cx + 8}" y="${cy - 8}" fill="${fill}" font-size="9" font-family="JetBrains Mono">${esc(c.color || c.name)}</text>`;
  });
  svg.innerHTML = html;
  if (legend) {
    legend.innerHTML = crew.map((c) => {
      const fill = COLOR_MAP[c.color] || "#00ff88";
      return `<li><span class="swatch" style="background:${fill}"></span>${esc(c.color)} · ${esc(c.room || "—")} · ${c.alive ? "ALIVE" : "DOWN"}</li>`;
    }).join("") || `<li class="muted">No actors on overlay</li>`;
  }
}

function renderCrewTable(crew, impostors) {
  const body = document.querySelector("#crew-table tbody");
  if (!body) return;
  if (!crew.length) {
    body.innerHTML = `<tr><td colspan="6" class="muted center mono">No pygame telemetry yet. Launch the client with --client while this bridge is running.</td></tr>`;
    return;
  }
  body.innerHTML = crew.map((c) => {
    const fill = COLOR_MAP[c.color] || "#00ff88";
    const isImp = impostors.includes(c.name) || impostors.includes(c.color) || c.impostor;
    return `<tr>
      <td><span class="p-dot" style="background:${fill}"></span>${esc(c.color || c.name)}${isImp ? ' <span class="magenta-text">IMP</span>' : ""}</td>
      <td class="muted">${esc(c.kind || "unit")}</td>
      <td>${esc(c.room || "—")}</td>
      <td class="mono">${esc(c.state || "—")}${c.venting ? " / VENT" : ""}</td>
      <td><span class="vital ${c.alive ? "" : "dead"}">${c.alive ? "ALIVE" : "DEAD"}</span></td>
      <td class="mono">${c.voted == null ? "—" : esc(String(c.voted))}</td>
    </tr>`;
  }).join("");
}

function renderTasks(tasks, done, total) {
  const el = document.getElementById("task-board");
  const pctEl = document.getElementById("task-pct");
  const n = tasks.length || total || 8;
  const d = tasks.filter((t) => t.done).length || done || 0;
  if (pctEl) pctEl.textContent = `${Math.round((d / n) * 100) || 0}%`;
  if (!el) return;
  if (!tasks.length) {
    el.innerHTML = `<p class="muted mono">Task board hydrates from pygame mission flags.</p>`;
    return;
  }
  el.innerHTML = tasks.map((t) => `
    <div class="task-row">
      <div class="task-meta"><span>${esc(t.name)}</span><span class="${t.done ? "accent-text" : "muted"}">${t.done ? "DONE" : "OPEN"}</span></div>
      <div class="bar ${t.done ? "done" : ""}"><span style="width:${t.done ? 100 : 12}%"></span></div>
    </div>`).join("");
}

function renderSabotage(sab, cds, meeting) {
  const grid = document.getElementById("sabotage-grid");
  const meet = document.getElementById("meeting-panel");
  const cd = document.getElementById("cooldown-panel");
  if (grid) {
    grid.innerHTML = [
      ["LIGHTS", sab.lights ? "BLACKOUT" : "NOMINAL", sab.lights],
      ["REACTOR", sab.reactor ? `MELT ${sab.reactor_timer || 0}s` : "STABLE", sab.reactor],
      ["CRITICAL", sab.critical ? "YES" : "NO", sab.critical],
    ].map(([k, v, bad]) => `<div class="task-row"><div class="task-meta"><span>${k}</span><span class="${bad ? "magenta-text" : "accent-text"}">${v}</span></div>
      <div class="bar ${bad ? "warn" : "done"}"><span style="width:${bad ? 88 : 100}%"></span></div></div>`).join("");
  }
  if (meet) {
    meet.innerHTML = `
      <p class="task-meta"><span>STATUS</span><span class="${meeting.active ? "magenta-text" : "accent-text"}">${meeting.active ? "IN SESSION" : "SEALED"}</span></p>
      <p class="muted small">Report: ${meeting.report ? "BODY" : "—"} · Button: ${meeting.button ? "YES" : "NO"}</p>
      <p class="muted small">Timer: ${meeting.timer || 0}s · Eject: ${meeting.eject_colour || "none"}</p>
      <p class="terminal-output" style="min-height:auto;margin-top:10px">> ${esc(meeting.evidence || "No meeting evidence.")}</p>`;
  }
  if (cd) {
    cd.innerHTML = `
      <div class="task-row"><div class="task-meta"><span>KILL CD</span><span>${cds.kill || 0}s</span></div><div class="bar"><span style="width:${Math.min(100, ((15 - (cds.kill || 0)) / 15) * 100)}%"></span></div></div>
      <div class="task-row"><div class="task-meta"><span>MEETING CD</span><span>${cds.meeting || 0}s</span></div><div class="bar"><span style="width:${Math.min(100, ((15 - (cds.meeting || 0)) / 15) * 100)}%"></span></div></div>`;
  }
}

function renderOccupancy(crew) {
  const el = document.getElementById("occupancy");
  if (!el) return;
  const rooms = {};
  crew.forEach((c) => {
    const r = c.room || "Unknown";
    rooms[r] = rooms[r] || { n: 0, dead: 0 };
    rooms[r].n += 1;
    if (!c.alive) rooms[r].dead += 1;
  });
  const keys = Object.keys(rooms);
  el.innerHTML = keys.length
    ? keys.map((r) => `<div class="occ-cell"><span class="s-label">${esc(r)}</span><strong>${rooms[r].n}</strong><span class="muted small">${rooms[r].dead} down</span></div>`).join("")
    : `<p class="muted">Occupancy appears once pygame heartbeats.</p>`;
}

function renderMemory(memory, events) {
  const metrics = document.getElementById("memory-metrics");
  if (metrics) {
    const rows = [
      ["Episodic events", memory.episodic_events || 0, 80],
      ["Kill traces", memory.kills || 0, 40],
      ["Vote traces", memory.votes || 0, 40],
      ["Fact triples", memory.fact_triples || 0, 80],
    ];
    metrics.innerHTML = rows.map(([k, v, cap]) => `
      <div class="task-row"><div class="task-meta"><span>${k}</span><span class="accent-text">${v}</span></div>
      <div class="bar"><span style="width:${Math.min(100, (v / cap) * 100)}%"></span></div></div>`).join("")
      + `<p class="muted small">Per-match wipe: ${memory.wipe_armed ? "ARMED" : "OFF"} · Engine: ${memory.mock ? "MOCK" : "LIVE"}</p>`;
  }
  const list = document.getElementById("episodic-list");
  if (list) {
    const items = (events || []).slice().reverse().slice(0, 40);
    list.innerHTML = items.length
      ? items.map((e) => `<li><span class="muted">[${fmtTime(e.ts)}]</span> ${esc(e.msg || JSON.stringify(e))}</li>`).join("")
      : `<li class="muted">No episodic traces yet.</li>`;
  }
}

function renderWallet(game, session) {
  const w = document.getElementById("wallet-panel");
  const m = document.getElementById("market-panel");
  if (w) {
    w.innerHTML = `
      <p class="hud-value">${game.tokens ?? "—"} NC</p>
      <p class="muted small">Stake 10 NC on match entry. +25 NC on win (pygame hooks).</p>
      <p class="muted small">Local player: ${esc(game.local_player || "—")}</p>`;
  }
  if (m) {
    const n = (game.crew || []).length || (session.players || []).length;
    m.innerHTML = `
      <div class="grid-summary">
        <div class="summary-item"><span class="s-label">POOL</span><span class="s-val">${n} PLAYERS</span></div>
        <div class="summary-item"><span class="s-label">ROUND</span><span class="s-val">${game.round || 1}</span></div>
        <div class="summary-item"><span class="s-label">REWARD</span><span class="s-val">25 NC</span></div>
        <div class="summary-item"><span class="s-label">BOTS</span><span class="s-val">${game.bot_count ?? "—"}</span></div>
      </div>`;
  }
}

function renderProof(session) {
  const el = document.getElementById("proof-panel");
  if (!el) return;
  el.innerHTML = `
    <p class="task-meta"><span>LEDGER</span><span class="cyan-text">${esc(session.ledger_mode || "mock")}</span></p>
    <p class="task-meta"><span>MEMORY</span><span>${session.memory_mock ? "MOCK" : "LIVE"}</span></p>
    <p class="task-meta"><span>PLAYERS COMMITTED</span><span>${(session.players || []).length}</span></p>
    <p class="muted small" style="margin-top:10px">Roles stay hashed until pygame gameover reveals salts.</p>`;
}

function renderPassport(p) {
  if (!p) return;
  if (p.result) setText("passport-badge", `STATUS: ${p.result}`);
  if (p.result && p.result !== "IN_PROGRESS") setText("summary-outcome", p.result);
  const img = document.getElementById("outcome-sigil");
  const cap = document.getElementById("sigil-caption");
  if (img && p.sigil) {
    img.src = p.sigil.image ? `/audit_static/${p.sigil.image}` : "/audit_static/crew_victory.svg";
    img.classList.remove("hidden");
    if (cap) cap.textContent = p.sigil.title || p.result;
  }
  const tbody = document.querySelector("#commit-table tbody");
  if (tbody && p.players) {
    tbody.innerHTML = p.players.map((pl) => `
      <tr>
        <td class="mono">${pl.seat}</td>
        <td>${esc(pl.player)}</td>
        <td style="color:${pl.role === "IMPOSTOR" ? "var(--destructive)" : "var(--accent)"}">${esc(pl.role)}</td>
        <td class="muted small mono">${esc((pl.commitment || "").slice(0, 22))}…</td>
        <td class="accent-text">${pl.verified_locally ? "VERIFIED" : "UNVERIFIED"}</td>
      </tr>`).join("");
  }
  const hashEl = document.getElementById("log-hash");
  if (hashEl && p.event_log_hash) hashEl.innerHTML = `<p class="accent-text">${esc(p.event_log_hash)}</p>`;
  const links = document.getElementById("tx-links");
  if (links && p.ledger) {
    links.innerHTML = `
      <p>Commit: <a href="${p.ledger.commit_explorer_url || "#"}" target="_blank" rel="noopener">${esc((p.ledger.commit_tx || "n/a").slice(0, 22))}…</a></p>
      <p>Reveal: <a href="${p.ledger.reveal_explorer_url || "#"}" target="_blank" rel="noopener">${esc((p.ledger.reveal_tx || "n/a").slice(0, 22))}…</a></p>`;
  }
}

function mergeEventLog(events) {
  const feed = document.getElementById("event-feed");
  if (!feed || !events.length) return;
  const existing = new Set([...feed.querySelectorAll("li")].map((li) => li.dataset.key));
  events.slice(-30).reverse().forEach((e) => {
    const key = `${e.ts}-${e.msg}`;
    if (existing.has(key)) return;
    const li = document.createElement("li");
    li.dataset.key = key;
    li.innerHTML = `<span class="muted">[${fmtTime(e.ts)}]</span> ${esc(e.msg)}`;
    feed.prepend(li);
  });
}

function setupSSE() {
  const feed = document.getElementById("event-feed");
  if (!feed) return;
  try {
    const src = new EventSource("/events");
    src.onmessage = (event) => {
      let msg = event.data;
      let ts = Date.now() / 1000;
      try {
        const parsed = JSON.parse(event.data);
        msg = parsed.msg || event.data;
        ts = parsed.ts || ts;
      } catch (_) { /* plain string */ }
      const li = document.createElement("li");
      li.dataset.key = `${ts}-${msg}`;
      li.innerHTML = `<span class="muted">[${fmtTime(ts)}]</span> ${esc(msg)}`;
      feed.prepend(li);
      playBlipSound();
    };
  } catch (_) { /* sse unavailable */ }
}

function setupEvents() {
  const launch = document.getElementById("btn-launch");
  const sim = document.getElementById("btn-sim");
  const reset = document.getElementById("btn-reset");
  const report = document.getElementById("btn-report");
  if (launch) launch.addEventListener("click", () => processCommand("launch"));
  if (sim) sim.addEventListener("click", () => processCommand("kill Red Blue"));
  if (reset) reset.addEventListener("click", () => processCommand("reset"));
  if (report) report.addEventListener("click", () => processCommand("report"));
}

function setupShell() {
  const form = document.getElementById("shell-form");
  const input = document.getElementById("shell-input");
  if (form && input) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const cmd = input.value.trim();
      if (!cmd) return;
      input.value = "";
      processCommand(cmd);
    });
  }
  document.querySelectorAll("[data-cmd]").forEach((btn) => {
    btn.addEventListener("click", () => processCommand(btn.dataset.cmd));
  });
}

async function processCommand(cmd) {
  printShell(`> ${cmd}`);
  const parts = cmd.split(/\s+/);
  const verb = (parts[0] || "").toLowerCase();
  try {
    if (verb === "help") {
      printShell("launch · health · deal [p1 p2…] · kill <a> <b> · report [who] · gameover CREW|IMPOSTOR · reset");
    } else if (verb === "launch" || verb === "game") {
      const d = await postJSON("/launch_game", {});
      printShell(`Pygame Launched! (PID: ${d.pid || "active"})`);
    } else if (verb === "health") {
      printShell(JSON.stringify(await getJSON("/health")));
    } else if (verb === "deal") {
      const players = parts.length > 1 ? parts.slice(1) : ["Red", "Blue", "Green", "Yellow", "Orange", "Pink"];
      const d = await postJSON("/deal", { players, impostors: [players[0]] });
      printShell(`Dealt ${d.match_id}. Commitments on ledger.`);
    } else if (verb === "kill") {
      await postJSON("/kill", { killer: parts[1] || "Red", victim: parts[2] || "Blue", location: "Cafeteria" });
      printShell("Kill ingested.");
    } else if (verb === "report") {
      const d = await postJSON("/report", { reporter: parts[1] || "Red" });
      printShell(d.evidence || "Meeting opened.");
      setText("evidence-text", d.evidence || "");
    } else if (verb === "gameover") {
      const d = await postJSON("/gameover", { winner: parts[1] || "CREW", reason: "dashboard" });
      printShell(`Finalized ${d.winner}`);
      fetchPassport();
    } else if (verb === "reset") {
      await postJSON("/reset", {});
      printShell("Wiped.");
    } else {
      printShell(`Unknown: ${verb}`);
    }
    refreshAll();
  } catch (err) {
    printShell(`ERR ${err.message}`);
  }
}

function printShell(text) {
  const el = document.getElementById("shell-output");
  if (!el) return;
  const p = document.createElement("p");
  p.className = "shell-line";
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}

function playBlipSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.02;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.04);
  } catch (_) { /* audio blocked */ }
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(url);
  return res.json();
}

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}
function setHTML(id, v) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = v;
}
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(ts) {
  const d = new Date((ts || 0) * 1000);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}
function fmtAge(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 3 ? "NOW" : `${s.toFixed(1)}s AGO`;
}
