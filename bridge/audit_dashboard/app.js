document.addEventListener("DOMContentLoaded", () => {
  fetchHealth();
  fetchAuditData();
  setupSSE();
});

async function fetchHealth() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const el = document.getElementById("health");
    if (el && data) {
      el.innerHTML = `
        <span class="badge">Status: ${data.status}</span>
        <span class="badge">Chain: ${data.mock_chain ? "Mock" : "Testnet"}</span>
        <span class="badge">Memory: ${data.mock_memory ? "Mock" : "Live"}</span>
      `;
    }
  } catch (err) {
    console.error("Health check error:", err);
  }
}

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
  const summaryEl = document.getElementById("match-summary");
  if (summaryEl) {
    summaryEl.innerHTML = `
      <p><strong>Match ID:</strong> <span class="mono">${p.match_id}</span></p>
      <p><strong>Result:</strong> ${p.result || "FINISHED"}</p>
      <p><strong>Duration:</strong> ${p.duration_s || 0}s</p>
      <p><strong>Total Events:</strong> ${p.event_count || 0}</p>
    `;
  }

  const sigilImg = document.getElementById("outcome-sigil");
  const sigilCap = document.getElementById("sigil-caption");
  if (sigilImg && p.sigil) {
    sigilImg.src = p.sigil.image ? `/assets/sigils/${p.sigil.image}` : "/assets/sigils/crew_victory.svg";
    sigilImg.classList.remove("hidden");
    if (sigilCap) sigilCap.textContent = p.sigil.title || p.result;
  }

  const tableBody = document.querySelector("#commit-table tbody");
  if (tableBody && p.players) {
    tableBody.innerHTML = p.players.map(pl => `
      <tr>
        <td>${pl.seat}</td>
        <td>${pl.player}</td>
        <td><strong>${pl.role}</strong></td>
        <td class="mono small">${(pl.commitment || "").slice(0, 18)}...</td>
        <td style="color: ${pl.verified_locally ? 'var(--success)' : 'var(--danger)'}">
          ${pl.verified_locally ? '✓ Verified' : 'Unverified'}
        </td>
      </tr>
    `).join("");
  }

  const hashEl = document.getElementById("log-hash");
  if (hashEl && p.event_log_hash) {
    hashEl.innerHTML = `<p class="mono small">${p.event_log_hash}</p>`;
  }

  const linksEl = document.getElementById("tx-links");
  if (linksEl && p.ledger) {
    linksEl.innerHTML = `
      <p>Commit Tx: <a href="${p.ledger.commit_explorer_url || '#'}" target="_blank" class="muted">${p.ledger.commit_tx || 'N/A'}</a></p>
      <p>Reveal Tx: <a href="${p.ledger.reveal_explorer_url || '#'}" target="_blank" class="muted">${p.ledger.reveal_tx || 'N/A'}</a></p>
    `;
  }
}

function setupSSE() {
  const feed = document.getElementById("event-feed");
  if (!feed) return;
  const evtSource = new EventSource("/events");
  evtSource.onmessage = (event) => {
    const li = document.createElement("li");
    li.textContent = event.data;
    feed.prepend(li);
  };
}
