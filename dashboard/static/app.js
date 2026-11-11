(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const RUNNING = ["running", "paused", "stopping"];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  };

  let lastLogId = 0, lastState = null, deliveryLoaded = false, activeTab = "overview", sequencesLoaded = 0;

  async function api(path, opts) {
    const o = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
    const res = await fetch(path, o);
    const body = await res.json().catch(() => ({}));
    return { ok: res.ok, body };
  }
  const post = (path, data) => api(path, { method: "POST", body: JSON.stringify(data || {}) });

  function flash(text, isError, target) {
    const el = target;
    if (!el) return;
    el.textContent = text || "";
    el.className = "message" + (isError ? " error" : "");
  }

  // ----- tabs ----------------------------------------------------------------
  function showTab(name) {
    activeTab = name;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
    refreshTab();
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

  // ----- state ---------------------------------------------------------------------
  function renderStatus(s) {
    const labels = { idle: "Idle", running: "Running", paused: "Paused", stopping: "Stopping…",
      completed: "Completed", stopped: "Stopped", error: "Error" };
    $("status-pill").textContent = "Discovery: " + (labels[s.status] || s.status);
    $("status-pill").className = "pill pill-" + s.status;
    $("send-pill").textContent = s.send_enabled ? "Sending: ON" : "Sending: OFF (preview only)";
    $("send-pill").className = "pill " + (s.send_enabled ? "pill-running" : "pill-idle");
    const llm = s.llm || {};
    $("llm-pill").textContent = llm.enabled ? "LLM: " + llm.model : "LLM: off";
    $("llm-pill").title = llm.enabled && !llm.has_key ? "No API key set in .env" : (llm.provider || "");

    const running = RUNNING.includes(s.status), paused = s.status === "paused";
    $("btn-start").disabled = running;
    $("mode").disabled = running;
    $("btn-pause").hidden = paused;
    $("btn-pause").disabled = !running || paused || s.status === "stopping";
    $("btn-resume").hidden = !paused;
    $("btn-stop").disabled = !(running && s.status !== "stopping");
    $("activity").textContent = s.activity || (running ? "Working…" : "");
    $("message").textContent = s.error ? "Last run error: " + s.error : (s.message || "");
    $("message").className = "message" + (s.error ? " error" : "");
  }

  function renderOverview(s) {
    const c = s.counts || {}, f = s.funnel || {};
    $("m-total").textContent = c.total ?? 0;
    $("m-email").textContent = c.with_email ?? 0;
    $("m-discovered").textContent = c.discovered ?? 0;
    $("f-sent").textContent = f.sent ?? 0;
    $("m-24h").textContent = s.sent_last_24h ?? 0;
    $("m-cap").textContent = s.daily_cap ?? 0;
    $("f-opened").textContent = f.opened ?? 0;
    $("f-clicked").textContent = f.clicked ?? 0;
    $("f-replied").textContent = f.replied ?? 0;
    $("f-unsub").textContent = f.unsubscribed ?? 0;
    $("f-pending").textContent = f.pending_approval ?? 0;
    $("outbox-badge").textContent = f.pending_approval ?? 0;
    $("stages").innerHTML = s.stages.map((st, i) => {
      const pct = st.total ? Math.min(100, Math.round((st.done / st.total) * 100)) : (st.status === "done" ? 100 : 0);
      const count = st.total ? `${st.done} / ${st.total}` : (st.status === "done" ? "done" : "waiting");
      const state = { pending: "waiting", running: "running", done: "done" }[st.status] || st.status;
      return `<li class="stage ${esc(st.status)}"><div class="stage-top"><span class="stage-name">${i + 1}. ${esc(st.label)}</span>
        <span class="stage-state">${esc(state)}</span></div><div class="stage-count">${esc(count)}</div>
        <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div></li>`;
    }).join("");
  }

  function renderDiscovery(s) {
    const rows = s.recent || [];
    if (rows.length) {
      $("recent-body").innerHTML = rows.map((r) => `<tr>
        <td class="wrap"><span class="chan chan-${esc(r.channel)}">${esc(r.channel)}</span>
          <a href="${esc(r.link)}" target="_blank" rel="noopener">${esc(r.name)}</a>
          <div class="muted small">${esc(r.role ? r.role + " · " : "")}${esc(r.company && r.company !== r.name ? r.company : "")}</div></td>
        <td><span class="tier tier-${esc(r.tier)}">${esc(r.tier)}</span></td>
        <td>${esc(r.score)}</td><td class="wrap small">${esc(r.contact)}</td></tr>`).join("");
    }
    const plan = s.plan || [];
    $("plan-count").textContent = plan.length ? `${plan.length} searches` : "";
    if (plan.length) {
      $("plan").innerHTML = plan.map((p) =>
        `<div class="plan-row"><span class="chan chan-${esc(p.channel)}">${esc(p.channel)}</span>${esc(p.query)}</div>`).join("");
    }
    const runs = s.runs || [];
    if (runs.length) {
      $("runs-body").innerHTML = runs.map((r) => {
        const st = r.stats || {};
        return `<tr><td>#${esc(r.id)}</td><td>${esc(st.mode || "")}</td>
          <td><span class="pill pill-${esc(r.status)}">${esc(r.status)}</span></td>
          <td>${esc(fmt(r.started_at))}</td><td>${esc(fmt(r.finished_at))}</td>
          <td>${esc(st.added ?? "—")}</td><td>${esc(st.duplicates ?? "—")}</td><td>${esc(st.errors ?? "—")}</td></tr>`;
      }).join("");
    }
  }

  function renderSettings(s) {
    const d = s.delivery_settings || {};
    if (!deliveryLoaded) {
      $("delivery-channel").value = d.channel || "email";
      $("delivery-recipient").value = d.recipient || "";
      deliveryLoaded = true;
    }
    $("recipient-label").textContent = $("delivery-channel").value === "sms" ? "Phone number (E.164)" : "Email address";
    const notes = [s.delivery_enabled ? "Weekly list sending is ON." : "Weekly list: files only (delivery.enabled is off)."];
    if (s.delivery && s.delivery.note) notes.push("Last run: " + s.delivery.note);
    $("delivery-note").textContent = notes.join(" ");
    $("btn-autopilot").textContent = "Autopilot: " + (s.autopilot ? "on" : "off");
    $("send-note").innerHTML = [
      `Provider: <strong>${esc(s.provider)}</strong>`,
      `Brevo key: <strong>${s.brevo_configured ? "set" : "missing"}</strong>`,
      `Reply checks (IMAP): <strong>${s.imap_configured ? "configured" : "not configured"}</strong>`,
      `Postal address in footer: <strong>${s.postal_address_set ? "set" : "missing — set outreach.postal_address"}</strong>`,
      `Daily cap: <strong>${esc(s.daily_cap)}</strong>`,
    ].join("<br>");
  }

  function renderFunnelAndSequences(s) {
    const f = s.funnel || {};
    const steps = [["Sent", f.sent], ["Delivered", f.delivered], ["Opened", f.opened], ["Clicked", f.clicked],
      ["Replied", f.replied], ["Bounced", f.bounced], ["Unsubscribed", f.unsubscribed]];
    const max = Math.max(1, f.sent || 1);
    $("funnel").innerHTML = steps.map(([label, n]) => `<li class="funnel-row">
      <span class="funnel-label">${esc(label)}</span>
      <div class="bar"><div class="bar-fill" style="width:${Math.round(((n || 0) / max) * 100)}%"></div></div>
      <span class="funnel-n">${esc(n ?? 0)}</span></li>`).join("");
    const seqs = s.sequences || [];
    $("sequences").innerHTML = seqs.length ? seqs.map((q) => `<div class="plan-row"><strong>${esc(q.name)}</strong>
      &nbsp;— ${q.steps.map((st) => `day ${esc(st.day)}: ${esc(st.template)}`).join(" · ")}</div>`).join("")
      : `<span class="muted">No sequences configured.</span>`;
    if (sequencesLoaded !== seqs.length) {
      $("enroll-seq").innerHTML = seqs.map((q) => `<option value="${esc(q.name)}">${esc(q.name)}</option>`).join("");
      sequencesLoaded = seqs.length;
    }
  }

  async function refreshState() {
    try {
      const { ok, body } = await api("/api/state");
      if (!ok) return;
      lastState = body;
      renderStatus(body);
      renderOverview(body);
      renderDiscovery(body);
      renderSettings(body);
      renderFunnelAndSequences(body);
    } catch (e) { /* server restarting */ }
  }

  async function pollLogs() {
    const { ok, body } = await api(`/api/logs?after=${lastLogId}`);
    if (!ok || !body.entries) return;
    const box = $("log");
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    for (const e of body.entries) {
      lastLogId = Math.max(lastLogId, e.id);
      const div = document.createElement("div");
      div.className = "line";
      div.innerHTML = `<span class="ts">${esc(e.ts)}</span> <span class="lvl-${esc(e.level)}">${esc(e.message)}</span>`;
      box.appendChild(div);
    }
    while (box.childElementCount > 600) box.removeChild(box.firstChild);
    if (body.entries.length && ($("autoscroll").checked || atBottom)) box.scrollTop = box.scrollHeight;
  }

  // ----- database --------------------------------------------------------------------
  async function loadContacts() {
    const p = new URLSearchParams({ limit: "300", q: $("q").value, region: $("f-region").value,
      source: $("f-source").value, status: $("f-status").value });
    const { ok, body } = await api("/api/contacts?" + p);
    if (!ok) return;
    const rows = body.contacts || [];
    $("contacts-body").innerHTML = rows.length ? rows.map((c) => `<tr>
      <td><input type="checkbox" class="pick" value="${esc(c.id)}"></td>
      <td class="wrap"><strong>${esc(c.full_name || "—")}</strong><div class="muted small">${esc(c.title || "")}</div></td>
      <td class="wrap">${esc(c.company || "")}</td><td>${esc(c.email || "—")}</td>
      <td>${esc(c.region || "")}</td><td>${esc(c.source || "")}</td>
      <td>${c.fit_score != null ? `<span class="tier tier-${esc(c.tier)}">${esc(c.fit_score)}</span>` : "—"}</td>
      <td>${esc(c.status || "")}</td></tr>`).join("")
      : `<tr><td colspan="8" class="muted">No contacts match. Import a list above.</td></tr>`;
  }
  ["q", "f-region", "f-source", "f-status"].forEach((id) => $(id).addEventListener("input", loadContacts));
  $("check-all").addEventListener("change", (e) => document.querySelectorAll(".pick").forEach((b) => (b.checked = e.target.checked)));

  $("import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("import-file").files[0];
    if (!file) { flash("Choose a file first.", true, $("import-result")); return; }
    const fd = new FormData();
    fd.append("file", file);
    flash("Importing…", false, $("import-result"));
    const res = await fetch("/api/import", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    flash(body.message || "Import finished.", !res.ok, $("import-result"));
    loadContacts();
  });

  async function enrol(ids, all) {
    const seq = $("enroll-seq").value;
    const payload = all ? { region: $("f-region").value, source: $("f-source").value, sequence: seq }
      : { contact_ids: ids, sequence: seq };
    if (!all && !ids.length) { flash("Tick the contacts you want to enrol.", true, $("db-msg")); return; }
    const { ok, body } = await post("/api/enroll", payload);
    flash(body.message, !ok, $("db-msg"));
    loadContacts();
  }
  $("btn-enroll-selected").addEventListener("click", () =>
    enrol([...document.querySelectorAll(".pick:checked")].map((b) => Number(b.value)), false));
  async function loadMissingBasis() {
    const { ok, body } = await api("/api/lawful-basis/missing?region=europe");
    if (ok) $("missing-basis").textContent = body.missing;
  }
  $("btn-basis-europe").addEventListener("click", async () => {
    const basis = $("basis-choice").value;
    const n = $("missing-basis").textContent;
    if (!confirm(`Record "${basis}" as the lawful basis for ${n} Europe contact(s)? Only do this if you genuinely hold that basis for them.`)) return;
    const { ok, body } = await post("/api/lawful-basis", { basis, region: "europe", only_missing: true });
    flash(body.message, !ok, $("basis-msg"));
    loadMissingBasis();
  });
  $("btn-basis-selected").addEventListener("click", async () => {
    const ids = [...document.querySelectorAll(".pick:checked")].map((b) => Number(b.value));
    if (!ids.length) { flash("Tick the contacts first.", true, $("basis-msg")); return; }
    const basis = $("basis-choice").value;
    if (!confirm(`Record "${basis}" for ${ids.length} ticked contact(s)? Only do this if you genuinely hold that basis.`)) return;
    const { ok, body } = await post("/api/lawful-basis", { basis, contact_ids: ids });
    flash(body.message, !ok, $("basis-msg"));
    loadMissingBasis();
    loadContacts();
  });

  $("btn-enroll-filter").addEventListener("click", () => {
    if (confirm("Enrol every matching contact with an email? Nothing is sent until each email is approved.")) enrol([], true);
  });

  // ----- outbox -------------------------------------------------------------------------
  const STATUS_LABEL = { draft: "Waiting for approval", approved: "Approved", sent: "Sent",
    rejected: "Rejected", failed: "Failed" };

  async function loadOutbox() {
    const status = $("outbox-status").value;
    const p = new URLSearchParams({ mode: $("outbox-mode").value });
    if (status) p.set("status", status);
    $("outbox-csv").href = "/files/outbox.csv?mode=" + $("outbox-mode").value;
    const { ok, body } = await api("/api/outbox?" + p);
    if (!ok) return;
    const list = body.messages || [];
    if (!list.length) { $("outbox-list").innerHTML = `<p class="muted">Nothing here.</p>`; return; }
    $("outbox-list").innerHTML = list.map((m) => `
      <article class="mail" data-id="${esc(m.id)}">
        <header>
          <div><strong>${esc(m.full_name || m.to_email || "—")}</strong>
            <span class="muted small"> · ${esc(m.title || "")}${m.company ? " at " + esc(m.company) : ""} · ${esc(m.region || "")}</span></div>
          <div><span class="pill pill-${esc(m.status)}">${esc(STATUS_LABEL[m.status] || m.status)}</span>
            <span class="muted small">email ${esc(m.step)} · ${esc(m.variant)}</span></div>
        </header>
        <div class="mail-to small muted">To: ${esc(m.to_email || "—")}</div>
        <div class="mail-subject" data-field="subject">${esc(m.subject)}</div>
        <pre class="mail-body" data-field="body">${esc(m.body)}</pre>
        ${m.error ? `<div class="message error small">Note: ${esc(m.error)}</div>` : ""}
        ${m.fit_reason ? `<div class="small muted">Why this contact: ${esc(m.fit_reason)}</div>` : ""}
        <footer class="mail-actions">
          ${m.status === "draft" ? `
            <button class="btn btn-tiny" data-act="edit">Edit</button>
            <button class="btn btn-tiny btn-primary" data-act="approve">Approve</button>
            <button class="btn btn-tiny btn-danger" data-act="reject">Reject &amp; pause sequence</button>` : ""}
          ${m.status === "approved" ? `<button class="btn btn-tiny btn-danger" data-act="reject">Withdraw</button>` : ""}
          ${m.sent_at ? `<span class="muted small">sent ${esc(fmt(m.sent_at))}</span>` : ""}
        </footer>
      </article>`).join("");
  }

  $("outbox-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".mail");
    const id = card.dataset.id;
    const act = btn.dataset.act;
    const obMode = $("outbox-mode").value;
    if (act === "approve") {
      const { ok, body } = await post(`/api/outbox/${id}/approve`, { mode: obMode });
      flash(body.message, !ok, $("outbox-msg"));
    } else if (act === "reject") {
      if (!confirm("Withdraw this email and pause this contact's sequence?")) return;
      const { ok, body } = await post(`/api/outbox/${id}/reject`, { mode: obMode });
      flash(body.message, !ok, $("outbox-msg"));
    } else if (act === "edit") {
      const subj = card.querySelector('[data-field="subject"]');
      const body = card.querySelector('[data-field="body"]');
      if (btn.textContent === "Edit") {
        subj.outerHTML = `<input class="edit-subject" value="${esc(subj.textContent)}">`;
        body.outerHTML = `<textarea class="edit-body">${esc(body.textContent)}</textarea>`;
        btn.textContent = "Save";
        return;
      }
      const { ok, body: res } = await post(`/api/outbox/${id}/edit`, {
        subject: card.querySelector(".edit-subject").value,
        body: card.querySelector(".edit-body").value,
        mode: obMode,
      });
      flash(res.message, !ok, $("outbox-msg"));
    }
    await loadOutbox();
    refreshState();
  });

  $("outbox-status").addEventListener("change", loadOutbox);
  $("outbox-mode").addEventListener("change", loadOutbox);
  $("btn-approve-all").addEventListener("click", async () => {
    if (!confirm("Approve every draft? You can still withdraw an email before it is sent.")) return;
    const { body } = await post("/api/outbox/approve-all", { mode: $("outbox-mode").value });
    flash(body.message, false, $("outbox-msg"));
    loadOutbox();
  });
  $("btn-tick").addEventListener("click", async () => {
    const { ok, body } = await post("/api/tick");
    flash(body.message, !ok, $("outbox-msg"));
    loadOutbox();
    refreshState();
  });

  // ----- enrolments ---------------------------------------------------------------------
  async function loadEnrollments() {
    const status = $("enroll-status").value;
    const { ok, body } = await api("/api/enrollments" + (status ? "?status=" + status : ""));
    if (!ok) return;
    const rows = body.enrollments || [];
    $("enrollments-body").innerHTML = rows.length ? rows.map((r) => `<tr>
      <td class="wrap">${esc(r.full_name || r.email)}<div class="muted small">${esc(r.company || "")}</div></td>
      <td>${esc(r.sequence_name)}</td><td>${esc(r.step)}</td>
      <td><span class="pill pill-${esc(r.status)}">${esc(r.status)}</span></td>
      <td>${esc(fmt(r.next_action_at))}</td></tr>`).join("")
      : `<tr><td colspan="5" class="muted">None yet.</td></tr>`;
    $("enroll-body").innerHTML = rows.slice(0, 12).map((r) => `<tr>
      <td>${esc(r.full_name || r.email)}</td><td>${esc(r.sequence_name)}</td><td>${esc(r.step)}</td>
      <td><span class="pill pill-${esc(r.status)}">${esc(r.status)}</span></td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">Nobody enrolled yet.</td></tr>`;
  }
  $("enroll-status").addEventListener("change", loadEnrollments);

  // ----- discovery controls ---------------------------------------------------------------
  async function control(path, confirmText, data) {
    if (confirmText && !confirm(confirmText)) return;
    const { ok, body } = await post(path, data);
    flash(body.message, !ok, $("message"));
    refreshState();
  }
  $("btn-start").addEventListener("click", () => control("/api/start", null, { mode: $("mode").value }));
  $("btn-pause").addEventListener("click", () => control("/api/pause"));
  $("btn-resume").addEventListener("click", () => control("/api/resume"));
  $("btn-stop").addEventListener("click", () => control("/api/stop", "Stop discovery? Contacts already added are kept."));
  $("mode").addEventListener("change", () => {});

  // ----- settings -----------------------------------------------------------------------
  $("delivery-channel").addEventListener("change", () => {
    $("recipient-label").textContent = $("delivery-channel").value === "sms" ? "Phone number (E.164)" : "Email address";
  });
  $("delivery-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const { ok, body } = await post("/api/delivery", { channel: $("delivery-channel").value, recipient: $("delivery-recipient").value });
    flash(body.message, !ok, $("delivery-note"));
    deliveryLoaded = false;
  });
  $("btn-autopilot").addEventListener("click", async () => {
    const on = !(lastState && lastState.autopilot);
    const { body } = await post("/api/autopilot", { on });
    flash(body.message, false, $("send-note"));
    refreshState();
  });

  async function loadWebhook() {
    const { body } = await api("/api/webhook-url");
    $("webhook-url").textContent = body.url || "";
  }

  // ----- IB screening -------------------------------------------------------------------
  const IB_LABEL = { capital_raiser: "Capital raiser", hedger: "Hedger",
    execution_only: "Execution-only", uncertain: "Uncertain" };
  const IB_CLASS = { capital_raiser: "ib-raiser", hedger: "ib-hedger",
    execution_only: "ib-exec", uncertain: "ib-uncertain" };
  const RECO_CLASS = { "High-priority review": "reco-high", "Review": "reco-review", "Exclude": "reco-exclude" };

  async function loadFirms() {
    const mode = $("firm-mode").value;
    const p = new URLSearchParams({
      ib_type: $("firm-ibtype").value, recommendation: $("firm-reco").value,
      decision: $("firm-decision").value, outreach: $("firm-outreach").value,
      lead_quality: $("firm-quality").value, mode,
    });
    $("firm-download").href = "/files/NFA_Master_IB_List_latest.xlsx?mode=" + mode;
    const { ok, body } = await api("/api/firms?" + p);
    if (!ok) return;
    const c = body.counts || {};
    $("firm-counts").textContent =
      `${c.total || 0} firms · qualified ${c.qualified || 0} · uncertain ${c.quality_uncertain || 0} · ` +
      `irrelevant ${c.irrelevant || 0} · not screened ${c.unclassified || 0} — ` +
      `capital-raisers ${c.capital_raiser || 0} · hedgers ${c.hedger || 0} · ` +
      `execution-only ${c.execution_only || 0} · ` +
      `high-priority ${c.high_priority || 0} · kept ${c.kept || 0} · emails drafted ${c.queued || 0}`;
    const rows = body.firms || [];
    if (!rows.length) { $("firms-body").innerHTML = `<tr><td colspan="9" class="muted">No firms match. Load the master list, then screen.</td></tr>`; return; }
    $("firms-body").innerHTML = rows.map((f) => {
      const ib = f.ib_type || "";
      const quality = (f.lead_quality || "").toLowerCase();
      const qTag = quality ? `<div class="small"><span class="qtag q-${esc(quality)}">${esc(quality)}</span></div>` : "";
      const typeCell = (ib ? `<span class="ibtag ${IB_CLASS[ib] || ""}">${esc(IB_LABEL[ib] || ib)}</span>` : '<span class="muted small">not screened</span>') + qTag;
      const raises = (f.raises_for_cta || "").toLowerCase();
      const raisesCell = raises === "yes" ? '<span class="yes">Yes</span>'
        : raises === "no" ? '<span class="muted">No</span>'
        : raises ? '<span class="maybe">Unclear</span>' : "—";
      const contact = f.contact_email ? esc(f.contact_email) : '<span class="muted">no work email</span>';
      const who = [f.contact_name, f.contact_title].filter(Boolean).join(", ");
      const reco = f.recommendation || "";
      const ev = f.evidence_text ? `<div class="evidence">“${esc(f.evidence_text)}”${f.evidence_url ? ` <a href="${esc(f.evidence_url)}" target="_blank" rel="noopener">source</a>` : ""}</div>` : "—";
      const decided = (f.decision || "").toLowerCase();
      const dec = decided === "keep" ? '<span class="yes">Kept</span>'
        : decided === "exclude" ? '<span class="muted">Excluded</span>' : "";
      return `<tr>
        <td class="wrap"><strong>${esc(f.firm_name)}</strong>
          ${f.website ? `<div class="small"><a href="${esc(f.website)}" target="_blank" rel="noopener">${esc(f.website.replace(/^https?:\/\//, ""))}</a></div>` : `<div class="small muted">no website yet</div>`}</td>
        <td>${typeCell}</td>
        <td class="small">${raisesCell}</td>
        <td class="wrap small">${esc(f.client_segment || "—")}</td>
        <td class="wrap small">${contact}<div class="muted">${esc(who)}</div></td>
        <td><span class="tier tier-${esc(f.tier || "")}">${esc(f.tier || "")}</span> ${esc(f.lead_score ?? "")}</td>
        <td class="small"><span class="${RECO_CLASS[reco] || ""}">${esc(reco || "—")}</span>
          <div class="muted small">${esc(f.outreach_status && f.outreach_status !== "none" ? f.outreach_status : "")}</div></td>
        <td class="wrap small">${ev}</td>
        <td class="nowrap">${dec}
          <button class="btn btn-tiny" data-keep="${esc(f.firm_key)}">Keep</button>
          <button class="btn btn-tiny" data-exclude="${esc(f.firm_key)}">Exclude</button></td>
      </tr>`;
    }).join("");
  }

  $("firms-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-keep], button[data-exclude]");
    if (!btn) return;
    const firm_key = btn.dataset.keep || btn.dataset.exclude;
    const decision = btn.dataset.keep ? "keep" : "exclude";
    const { ok, body } = await post("/api/firms/decision", { firm_key, decision, mode: $("firm-mode").value });
    flash(body.message, !ok, $("firm-message"));
    loadFirms();
  });

  $("firm-import-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("firm-import-file").files[0];
    if (!file) { flash("Choose the .xlsx file first.", true, $("firm-import-result")); return; }
    const fd = new FormData();
    fd.append("file", file);
    flash("Loading…", false, $("firm-import-result"));
    const res = await fetch("/api/firms/import", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    flash(body.message || "Done.", !res.ok, $("firm-import-result"));
    loadFirms();
  });
  $("btn-firm-run").addEventListener("click", async () => {
    const { ok, body } = await post("/api/start", { kind: "firms", mode: $("firm-mode").value });
    flash(body.message, !ok, $("firm-message"));
    refreshState();
  });
  ["firm-ibtype", "firm-quality", "firm-reco", "firm-decision", "firm-outreach", "firm-mode"].forEach((id) =>
    $(id).addEventListener("change", loadFirms));

  // ----- loops -----------------------------------------------------------------------------
  function refreshTab() {
    if (activeTab === "database") { loadContacts(); loadMissingBasis(); }
    if (activeTab === "outbox") loadOutbox();
    if (activeTab === "sequences") loadEnrollments();
    if (activeTab === "firms") loadFirms();
    if (activeTab === "settings") loadWebhook();
  }

  setInterval(async () => {
    await refreshState();
    await pollLogs();
  }, 2000);
  setInterval(() => {
    if (activeTab === "outbox" && !document.querySelector(".edit-body")) loadOutbox();
    if (activeTab === "sequences") loadEnrollments();
    if (activeTab === "database") loadContacts();
    if (activeTab === "firms") loadFirms();
  }, 8000);

  refreshState().then(() => { pollLogs(); loadContacts(); loadEnrollments(); loadMissingBasis(); });
})();
