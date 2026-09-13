/* Universal Web Scraper dashboard */
const $ = (id) => document.getElementById(id);
let pollTimer = null;
let currentDetailJob = null;

async function api(path, opts) {
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

/* ---------------- submit ---------------- */
$("scrape-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("submit-btn");
  const msg = $("submit-msg");
  btn.disabled = true;
  btn.textContent = "Submitting…";
  msg.classList.add("hidden");
  try {
    const body = {
      url: $("url").value.trim(),
      rendering: $("rendering").value,
      pagination: { max_pages: parseInt($("max-pages").value, 10) || 5 },
      extraction: { mode: $("mode").value },
      popup_handling: $("popup-handling").checked,
      output: { screenshot: $("screenshot").checked, include_raw_html: $("raw-html").checked },
    };
    if ($("respect-robots").checked === false) body.respect_robots = false;
    const schemaRaw = $("schema").value.trim();
    if (schemaRaw) {
      body.extraction.schema = JSON.parse(schemaRaw);
    }
    const job = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    msg.className = "msg ok";
    msg.textContent = `Job ${job.job_id.slice(0, 8)} queued — tracking below.`;
    showDetail(job.job_id);
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = `Failed: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Scrape";
    msg.classList.remove("hidden");
    refresh();
  }
});

/* ---------------- jobs table ---------------- */
const stateBadge = (state) =>
  `<span class="badge ${state}">${state.replace(/_/g, " ")}</span>`;
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function refresh() {
  try {
    const [list, stats] = await Promise.all([api("/api/jobs?limit=50"), api("/api/stats")]);
    $("stat-jobs").textContent = stats.jobs_total;
    $("stat-completed").textContent = stats.jobs_completed;
    $("stat-failed").textContent = stats.jobs_failed;
    $("stat-items").textContent = stats.items_extracted;
    $("stat-domains").textContent = stats.domains_tracked;

    const tbody = $("jobs-body");
    if (!list.jobs.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">no jobs yet — submit one above</td></tr>';
      return;
    }
    tbody.innerHTML = list.jobs.map((j) => {
      const s = j.stats || {};
      const dur = s.duration_seconds ? `${s.duration_seconds}s` : "—";
      return `<tr>
        <td class="mono">${j.job_id.slice(0, 8)}</td>
        <td><a href="${esc(j.url)}" target="_blank" rel="noopener" style="color:var(--accent-2);text-decoration:none">${esc(j.url.slice(0, 60))}</a></td>
        <td>${stateBadge(j.state)}</td>
        <td class="mono">${s.items_extracted ?? 0}</td>
        <td class="mono">${s.pages_scraped ?? 0}</td>
        <td class="mono">${esc(s.rendering_mode || "—")}</td>
        <td class="mono">${dur}</td>
        <td class="mono">${new Date(j.created_at).toLocaleTimeString()}</td>
        <td><button class="link" onclick="showDetail('${j.job_id}')">view</button>
            <button class="link" onclick="delJob('${j.job_id}')">del</button></td>
      </tr>`;
    }).join("");
    $("queue-info").textContent = `(${list.total} total)`;
  } catch (e) {
    console.error(e);
  }
}

async function delJob(id) {
  if (!confirm("Delete this job and its results?")) return;
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); } catch {}
  if (currentDetailJob === id) hideDetail();
  refresh();
}

/* ---------------- detail ---------------- */
async function showDetail(jobId) {
  currentDetailJob = jobId;
  $("detail-panel").classList.remove("hidden");
  $("detail-id").textContent = jobId.slice(0, 8);
  $("detail-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  await loadDetail(jobId);
}

function hideDetail() {
  currentDetailJob = null;
  $("detail-panel").classList.add("hidden");
}
$("detail-close").addEventListener("click", hideDetail);

async function loadDetail(jobId) {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    if (currentDetailJob !== jobId) return;
    const s = job.stats || {};
    const cards = [
      ["state", job.state], ["items", s.items_extracted ?? 0],
      ["pages", s.pages_scraped ?? 0], ["duplicates skipped", s.duplicates_skipped ?? 0],
      ["popups dismissed", s.popups_dismissed ?? 0], ["challenges", s.challenges_encountered ?? 0],
      ["rendering", s.rendering_mode || "—"], ["duration", `${s.duration_seconds ?? 0}s`],
      ["extraction", s.extraction_strategy || "—"],
      ["pagination", (s.pagination || {}).strategy || "—"],
    ];
    $("detail-stats").innerHTML = cards.map(([k, v]) =>
      `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
    $("detail-errors").innerHTML = (job.error_log || []).map((e) =>
      `<div class="err">${esc(JSON.stringify(e))}</div>`).join("");

    const res = await api(`/api/jobs/${jobId}/results?limit=200`);
    $("detail-count").textContent = `(${res.total} items)`;
    $("detail-results").innerHTML = res.items.length
      ? res.items.map((i) => `<pre>${esc(JSON.stringify(i.data, null, 2))}</pre>`).join("")
      : '<div class="empty">no results yet</div>';

    if (["completed", "partially_completed", "failed"].includes(job.state)) {
      const arts = await api(`/api/jobs/${jobId}/artifacts`);
      const html = arts.artifacts.map((f) =>
        f.endsWith(".png")
          ? `<a href="/api/jobs/${jobId}/artifacts/${f}" target="_blank">${f}</a>`
          : `<a href="/api/jobs/${jobId}/artifacts/${f}" target="_blank">${f}</a>`).join("");
      const shot = arts.artifacts.filter((f) => f.endsWith(".png"))
        .map((f) => `<img src="/api/jobs/${jobId}/artifacts/${f}" loading="lazy">`).join("");
      $("detail-artifacts").innerHTML = html ? `<h3>Artifacts</h3>${html}${shot}` : "";
    }
  } catch (e) {
    console.error(e);
  }
}

/* ---------------- polling ---------------- */
function startPolling() {
  pollTimer = setInterval(() => {
    refresh();
    if (currentDetailJob) loadDetail(currentDetailJob);
  }, 3000);
}

refresh();
startPolling();
