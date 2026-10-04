document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
});

function initDashboard() {
  fetchHealth();
  fetchSession();
  fetchAuditData();
  setupSSE();
  setupEvents();
  setupShell();

  // Poll periodic telemetry every 3 seconds
  setInterval(() => {
    fetchHealth();
    fetchSession();
  }, 3000);
}

// ------------------------------------------------------------------
// Health & Session Telemetry
// ------------------------------------------------------------------
async function fetchHealth() {
  try {
    const res = await fetch("/health");
    if (!res.ok) return;
    const data = await res.json();
    const el = document.getElementById("health");
    if (el && data) {
      el.innerHTML = `
        <span class="cyber-badge status-pulse"><span class="led green"></span> STATUS: ${data.status.toUpperCase()}</span>
        <span class="cyber-badge"><span class="led cyan"></span> CHAIN: ${data.mock_chain ? "MOCK LEDGER" : "SEPOLIA EVM"}</span>
        <span class="cyber-badge"><span class="led magenta"></span> MEMORY: ${data.mock_memory ? "MOCK MEMORY" : "AEROCORTEX LIVE"}</span>
      `;
    }
  } catch (err) {
    console.error("Health check error:", err);
  }
}

async function fetchSession() {
  try {
    const res = await fetch("/session");
    if (!res.ok) return;
    const data = await res.json();

    if (data.match_id) {
      const matchIdEl = document.getElementById("hud-match-id");
      if (matchIdEl) matchIdEl.textContent = data.match_id;

      const summaryMatchId = document.getElementById("summary-match-id");
      if (summaryMatchId) summaryMatchId.textContent = data.match_id;

      const matchStatusEl = document.getElementById("hud-match-status");
      if (matchStatusEl) matchStatusEl.innerHTML = `<span class="dot active"></span> STATUS: ACTIVE (PLAYERS: ${data.players.length})`;
    }

    if (data.last_evidence) {
      const evText = document.getElementById("evidence-text");
      if (evText) evText.textContent = data.last_evidence;
    }

    if (data.players && data.players.length > 0) {
      renderPlayerRoster(data.players, data.impostors || []);
    }
  } catch (err) {
    console.log("No active session fetched.");
  }
}

// ------------------------------------------------------------------
// Passport & Audit Verification Data
// ------------------------------------------------------------------
async function fetchAuditData() {
  try {
    const res = await fetch("/passports/last");
    if (!res.ok) return;
    const data = await res.json();
    renderPassport(data.passport || data);
  } catch (err) {
    console.log("No completed match passport yet.");
  }
}

function renderPassport(p) {
  if (!p) return;

  const badgeEl = document.getElementById("passport-badge");
  if (badgeEl) badgeEl.textContent = `STATUS: ${p.result || "FINISHED"}`;

  const outcomeEl = document.getElementById("summary-outcome");
  if (outcomeEl) outcomeEl.textContent = p.result || "FINISHED";

  const durEl = document.getElementById("summary-duration");
  if (durEl) durEl.textContent = `${p.duration_s || 0}s`;

  const evEl = document.getElementById("summary-events");
  if (evEl) evEl.textContent = `${p.event_count || 0}`;

  const sigilImg = document.getElementById("outcome-sigil");
  const sigilCap = document.getElementById("sigil-caption");
  if (sigilImg && p.sigil) {
    sigilImg.src = p.sigil.image ? `/audit_static/${p.sigil.image}` : "/audit_static/crew_victory.svg";
    sigilImg.classList.remove("hidden");
    if (sigilCap) sigilCap.textContent = p.sigil.title || p.result;
  }

  const tableBody = document.querySelector("#commit-table tbody");
  if (tableBody && p.players) {
    tableBody.innerHTML = p.players.map(pl => `
      <tr>
        <td class="mono font-bold">${pl.seat}</td>
        <td class="mono">${pl.player}</td>
        <td><strong style="color: ${pl.role === 'IMPOSTOR' ? 'var(--destructive)' : 'var(--accent)'}">${pl.role}</strong></td>
        <td class="mono small muted">${(pl.commitment || "").slice(0, 20)}...</td>
        <td style="color: ${pl.verified_locally ? 'var(--accent)' : 'var(--destructive)'}">
          ${pl.verified_locally ? '&#10003; VERIFIED' : 'UNVERIFIED'}
        </td>
      </tr>
    `).join("");
  }

  const hashEl = document.getElementById("log-hash");
  if (hashEl && p.event_log_hash) {
    hashEl.innerHTML = `<p class="mono small accent-text">${p.event_log_hash}</p>`;
  }

  const linksEl = document.getElementById("tx-links");
  if (linksEl && p.ledger) {
    linksEl.innerHTML = `
      <p style="margin-top:6px;">Commit Tx: <a href="${p.ledger.commit_explorer_url || '#'}" target="_blank" class="cyan-text">${(p.ledger.commit_tx || 'N/A').slice(0, 18)}...</a></p>
      <p style="margin-top:4px;">Reveal Tx: <a href="${p.ledger.reveal_explorer_url || '#'}" target="_blank" class="cyan-text">${(p.ledger.reveal_tx || 'N/A').slice(0, 18)}...</a></p>
    `;
  }
}

function renderPlayerRoster(players, impostors) {
  const roster = document.getElementById("player-roster");
  if (!roster) return;
  const colors = ["red", "blue", "green", "yellow", "cyan", "magenta"];
  roster.innerHTML = players.map((p, idx) => {
    const isImp = impostors.includes(p);
    const col = colors[idx % colors.length];
    return `
      <li class="player-item">
        <span><span class="p-color ${col}"></span> ${p} ${isImp ? '(Impostor)' : ''}</span>
        <span class="p-status alive">ALIVE</span>
      </li>
    `;
  }).join("");
}

// ------------------------------------------------------------------
// SSE Real-Time Cyber Stream
// ------------------------------------------------------------------
function setupSSE() {
  const feed = document.getElementById("event-feed");
  if (!feed) return;
  try {
    const evtSource = new EventSource("/events");
    evtSource.onmessage = (event) => {
      const li = document.createElement("li");
      const timeStr = new Date().toLocaleTimeString('en-US', { hour12: false });
      li.innerHTML = `<span class="muted">[${timeStr}]</span> <span>${event.data}</span>`;
      feed.prepend(li);
      playBlipSound();
    };
  } catch (err) {
    console.error("SSE Connection Error:", err);
  }
}

// ------------------------------------------------------------------
// Buttons & Quick Action Events
// ------------------------------------------------------------------
function setupEvents() {
  const btnSim = document.getElementById("btn-sim");
  if (btnSim) {
    btnSim.addEventListener("click", async () => {
      await fetch("/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ killer: "Red", victim: "Blue", location: "Reactor" })
      });
      printShellOutput("Simulated event: Red killed Blue at Reactor.");
    });
  }

  const btnReset = document.getElementById("btn-reset");
  if (btnReset) {
    btnReset.addEventListener("click", async () => {
      await fetch("/reset", { method: "POST" });
      printShellOutput("Session & Memory pipeline reset complete.");
      fetchHealth();
      fetchSession();
    });
  }
}

// ------------------------------------------------------------------
// Interactive Cyber CLI Terminal Shell
// ------------------------------------------------------------------
function setupShell() {
  const form = document.getElementById("shell-form");
  const input = document.getElementById("shell-input");
  if (!form || !input) return;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const cmd = input.value.trim();
    if (!cmd) return;
    input.value = "";
    processCommand(cmd);
  });
}

function sendCmd(cmd) {
  processCommand(cmd);
}

async function processCommand(cmd) {
  printShellOutput(`> ${cmd}`);
  const parts = cmd.split(" ");
  const verb = parts[0].toLowerCase();

  try {
    if (verb === "help") {
      printShellOutput("Available CLI Commands:");
      printShellOutput("  health                     - Query bridge status");
      printShellOutput("  deal [p1,p2,...]           - Commit roles on EVM");
      printShellOutput("  kill <killer> <victim>     - Record kill event");
      printShellOutput("  report [reporter]          - Trigger body meeting & surface evidence");
      printShellOutput("  gameover [CREW|IMPOSTOR]   - Finalize match & reveal salts");
      printShellOutput("  reset                      - Wipe match memory pipeline");
    } else if (verb === "health") {
      const res = await fetch("/health");
      const d = await res.json();
      printShellOutput(`System Health: ${JSON.stringify(d)}`);
    } else if (verb === "deal") {
      const players = parts.length > 1 ? parts.slice(1) : ["Red", "Blue", "Green", "Yellow"];
      const res = await fetch("/deal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ players, impostors: [players[0]] })
      });
      const d = await res.json();
      printShellOutput(`Roles Dealt! Match ID: ${d.match_id}. Commitments generated.`);
      fetchSession();
      fetchAuditData();
    } else if (verb === "kill") {
      const killer = parts[1] || "Red";
      const victim = parts[2] || "Blue";
      await fetch("/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ killer, victim, location: "Cafeteria" })
      });
      printShellOutput(`Kill recorded: ${killer} -> ${victim}`);
    } else if (verb === "report") {
      const reporter = parts[1] || "Red";
      const res = await fetch("/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reporter })
      });
      const d = await res.json();
      printShellOutput(`Meeting Started! ${d.evidence}`);
      fetchSession();
    } else if (verb === "gameover") {
      const winner = parts[1] || "IMPOSTOR";
      const res = await fetch("/gameover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ winner, reason: "Command execution" })
      });
      const d = await res.json();
      printShellOutput(`Match Finalized! Winner: ${d.winner}`);
      fetchAuditData();
    } else if (verb === "reset") {
      await fetch("/reset", { method: "POST" });
      printShellOutput("Pipeline reset successfully.");
      fetchHealth();
      fetchSession();
    } else {
      printShellOutput(`Unknown command: '${verb}'. Type 'help' for options.`);
    }
  } catch (err) {
    printShellOutput(`Error executing command: ${err.message}`);
  }
}

function printShellOutput(text) {
  const consoleEl = document.getElementById("shell-output");
  if (!consoleEl) return;
  const p = document.createElement("p");
  p.className = "shell-line";
  p.textContent = text;
  consoleEl.appendChild(p);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

// Audio Blip Feedback Effect
function playBlipSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.02, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.05);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.05);
  } catch (e) {
    // AudioContext blocked or unsupported
  }
}
