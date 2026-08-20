<script lang="ts">
// @ts-nocheck
import { onMount } from "svelte";
import {
  groupJobs,
  bucketFor,
  primaryRoleFor,
  messageOwnerId,
  draftActionFor,
  groupOutreachKind,
  outreachKindLabel,
  sectionCounts,
  showsApplicationReminder,
  visibleGroups,
} from "$view/grouping";
onMount(() => {
  const DEGREE_TITLE = {
    "1st": "1st-degree connection",
    "2nd": "2nd-degree connection",
    "3rd": "3rd-degree connection",
  };
  const el = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const jsonArray = (value) => {
    try {
      const parsed = JSON.parse(value || "[]");
      return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
    } catch {
      return [];
    }
  };

  const SECTION_LABELS = {
    all: "All outreach",
    direct: "Direct outreach",
    application_followup: "Apply + follow up",
  };
  const SECTIONS = ["all", "direct", "application_followup"];
  const FILTERS = [
    { key: "all", label: "All" },
    { key: "needs_review", label: "Pending" },
    { key: "approved", label: "Approved" },
    { key: "skipped", label: "Rejected" },
    { key: "sent", label: "Sent" },
  ];
  const state = {
    jobs: [],
    people: [],
    groups: [],
    section: "all",
    filter: "needs_review",
    query: "",
    selectedKey: null,
    override: new Map(),
  };

  function refreshGroups() {
    state.groups = groupJobs(state.jobs);
  }
  function selectedGroup() {
    return state.groups.find((g) => g.key === state.selectedKey) ?? null;
  }
  function primaryOf(group) {
    return primaryRoleFor(group.jobs, state.override.get(group.key));
  }
  function groupKind(group) {
    return groupOutreachKind(group.jobs);
  }
  function sectionGroups() {
    return state.section === "all"
      ? state.groups
      : state.groups.filter((g) => groupKind(g) === state.section);
  }
  function recipientName(group) {
    const primary = primaryOf(group);
    return String((primary.hiringTeam || [])[0]?.name ?? "").trim();
  }

  function sectionCountsAll() {
    const c = sectionCounts(state.groups);
    c.all = state.groups.length;
    return c;
  }

  function counts() {
    const c = { needs_review: 0, approved: 0, skipped: 0, sent: 0, all: 0 };
    const groups = sectionGroups();
    for (const g of groups) c[bucketFor(g.jobs)] += 1;
    c.all = groups.length;
    return c;
  }

  function visible() {
    return visibleGroups(state.groups, state.section, state.filter, state.query);
  }

  function defaultsActive() {
    return state.query === "" && state.filter === "needs_review" && state.section === "all";
  }

  function restoreFilterState() {
    const params = new URLSearchParams(window.location.search);
    const section = params.get("section");
    const filter = params.get("status");
    state.section = SECTIONS.includes(section) ? section : "all";
    state.filter = FILTERS.some((item) => item.key === filter) ? filter : "needs_review";
    state.query = (params.get("q") ?? "").trim();
    el("search").value = state.query;
  }

  function persistFilterState(mode = "replace") {
    const url = new URL(window.location.href);
    if (state.section === "all") url.searchParams.delete("section");
    else url.searchParams.set("section", state.section);
    if (state.filter === "needs_review") url.searchParams.delete("status");
    else url.searchParams.set("status", state.filter);
    if (state.query === "") url.searchParams.delete("q");
    else url.searchParams.set("q", state.query);
    window.history[`${mode}State`]({}, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function badge(deg) {
    if (!deg) return "";
    return `<span class="badge d${deg[0]}" title="${DEGREE_TITLE[deg] || ""}">${esc(deg)}</span>`;
  }

  function triageBadge(job) {
    const label = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit" }[
      job.triageBucket
    ];
    return label ? `<span class="badge triage">${label}</span>` : "";
  }

  function reviewBadge(bucket) {
    if (bucket === "needs_review") return `<span class="badge rev needs_review">Pending</span>`;
    const label = { approved: "Approved", skipped: "Rejected", sent: "Sent" }[bucket];
    return `<span class="badge rev ${bucket}">${label}</span>`;
  }

  function classLine(job) {
    return [
      job.workFocus ? `Doing: ${job.workFocus}` : "",
      job.productSystem ? `Building: ${job.productSystem}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function roleStateLabel(role) {
    if (role.status === "sent")
      return { bucket: "sent", label: "Sent", note: "message sent for this role" };
    if (role.review === "approved")
      return { bucket: "approved", label: "Approved", note: "owns the approved message" };
    if (role.review === "skipped") return { bucket: "skipped", label: "Rejected", note: "" };
    return { bucket: "needs_review", label: "Pending", note: "" };
  }

  function renderDescription(text) {
    const connectors = new Set([
      "of",
      "the",
      "and",
      "in",
      "for",
      "to",
      "with",
      "a",
      "an",
      "on",
      "at",
      "&",
    ]);
    const isHeading = (line) => {
      const t = line.trim();
      if (!t || t.length > 48) return false;
      if (/[.,;:|]$/.test(t) || /[•·()]/.test(t)) return false;
      const words = t.split(/\s+/).filter(Boolean);
      return (
        words.length > 0 &&
        words.every((w) => /^[A-Z0-9]/.test(w) || connectors.has(w.toLowerCase()))
      );
    };
    return text
      .split("\n")
      .filter((l) => l.trim())
      .map((line) => {
        const t = line.trim();
        return isHeading(t) ? `<div class="desc-heading">${esc(t)}</div>` : `<div>${esc(t)}</div>`;
      })
      .join("");
  }

  function renderSections() {
    const c = sectionCountsAll();
    el("sections").innerHTML = SECTIONS.map(
      (k) =>
        `<button class="section-btn${state.section === k ? " active" : ""}" data-section="${k}">${SECTION_LABELS[k]} <span class="n">${c[k]}</span></button>`,
    ).join("");
  }

  function renderFilters() {
    const c = counts();
    const clear = defaultsActive()
      ? ""
      : `<button class="filter clear" data-clear type="button">Clear</button>`;
    el("filters").innerHTML =
      FILTERS.map(
        (f) =>
          `<button class="filter${state.filter === f.key ? " active" : ""}" data-filter="${f.key}">${f.label} <span class="n">${c[f.key]}</span></button>`,
      ).join("") + clear;
  }

  function renderList() {
    const list = el("list");
    const groups = visible();
    if (!groups.length) {
      const message =
        sectionGroups().length === 0
          ? `No people in ${SECTION_LABELS[state.section]} yet`
          : "No people match your search or filters";
      list.innerHTML = `<div class="empty">${esc(message)}</div>`;
      return;
    }
    list.innerHTML = groups
      .map((group) => {
        const primary = primaryOf(group);
        const bucket = bucketFor(group.jobs);
        const kind = groupKind(group);
        const name = recipientName(group);
        const displayName = name || primary.title || "—";
        const roles = group.jobs.length;
        const side = [
          state.section === "all"
            ? `<span class="badge kind ${kind === "direct" ? "kind-direct" : "kind-applied"}">${esc(outreachKindLabel(kind))}</span>`
            : "",
          roles > 1
            ? `<span class="badge dup" title="${roles} roles list this person">${roles} roles</span>`
            : "",
          triageBadge(primary),
          reviewBadge(bucket),
        ].join("");
        const sel = group.key === state.selectedKey ? " selected" : "";
        const cl = classLine(primary);
        return `<div class="row${sel}" data-key="${esc(group.key)}" role="button" tabindex="0">
        <div class="row-main">
          <div class="row-name">${esc(displayName)}</div>
          <div class="row-title">${esc(primary.title || "—")}${primary.company ? ` · ${esc(primary.company)}` : ""}</div>
          ${cl ? `<div class="row-class">${esc(cl)}</div>` : ""}
        </div>
        <div class="row-side">${side}</div>
      </div>`;
      })
      .join("");
  }

  function roleChip(role, primaryId, ownerId) {
    const st = roleStateLabel(role);
    const current = role.id === primaryId;
    return `<li data-select-job="${esc(role.id)}" role="button" tabindex="0"${current ? ' class="current"' : ""}>
      <div class="role-top">
        <div class="role-title">${esc(role.title || "—")}</div>
        <div class="role-badges">${current ? '<span class="badge rev">Primary</span>' : ""}<span class="badge rev ${st.bucket}">${st.label}</span></div>
      </div>
      <div class="role-company">${esc(role.company || "—")}${role.location ? `<span class="loc"> · ${esc(role.location)}</span>` : ""}</div>
      <div class="role-footer">${role.id === ownerId ? `<span class="role-owner">${esc(st.note)}</span>` : ""}<a class="role-url" href="${esc(role.postingUrl)}" target="_blank" rel="noopener">View posting ↗</a></div>
    </li>`;
  }

  function renderDetail() {
    const d = el("detail");
    const group = selectedGroup();
    if (!group) {
      d.innerHTML = `<div class="empty">Select an opportunity to review</div>`;
      return;
    }
    const primary = primaryOf(group);
    const bucket = bucketFor(group.jobs);
    const name = recipientName(group);
    const displayName = name || primary.title || "—";
    const first = (primary.hiringTeam || [])[0];
    const ownerId = messageOwnerId(group.jobs);
    const action = draftActionFor(group.jobs, primary.id);
    const rolesHtml = `<div class="team"><h3>Roles for this person <span class="count">${group.jobs.length}</span></h3><ul class="roles">${group.jobs.map((role) => roleChip(role, primary.id, ownerId)).join("")}</ul></div>`;
    const workBrief =
      primary.workFocus || primary.productSystem
        ? `<div class="work-brief">
            ${primary.workFocus ? `<div><span>Doing</span><strong>${esc(primary.workFocus)}</strong></div>` : ""}
            ${primary.productSystem ? `<div><span>Building</span><strong>${esc(primary.productSystem)}</strong></div>` : ""}
          </div>`
        : "";
    const facts = [
      primary.workplaceType ? `<span class="fact">${esc(primary.workplaceType)}</span>` : "",
      primary.employmentType ? `<span class="fact">${esc(primary.employmentType)}</span>` : "",
      primary.matchedTerm
        ? `<span class="fact flag">Matched: ${esc(primary.matchedTerm)}</span>`
        : "",
      primary.applyMethod ? `<span class="fact">${esc(primary.applyMethod)}</span>` : "",
      primary.promoted ? `<span class="fact flag">Promoted</span>` : "",
      primary.activelyReviewing ? `<span class="fact flag">Actively reviewing</span>` : "",
    ]
      .filter(Boolean)
      .join("");
    const activity = [primary.filterReason, primary.postedAt, primary.applicantCount]
      .filter(Boolean)
      .map(esc)
      .join(" · ");
    const benefits = primary.benefits || [];
    const descHtml = primary.description
      ? `<div class="description"><h3>About the job</h3><div class="body">${renderDescription(primary.description)}</div></div>`
      : "";
    const summaryBlocks = [
      primary.productSummary
        ? `<div class="block"><div class="label">What you'd build</div>${esc(primary.productSummary)}</div>`
        : "",
    ]
      .filter(Boolean)
      .join("");
    const fitBrief = `<div class="summary"><h3>Fit brief</h3>
      <div class="block"><div class="label">Company</div>${esc(primary.companySummary || primary.company || "Unknown")}</div>
      <div class="block"><div class="label">What you'd work on</div>${esc(primary.workSummary)}</div>
      <div class="block"><div class="label">Responsibilities</div>${primary.responsibilities.length ? `<ul>${primary.responsibilities.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "Unknown"}</div>
      <div class="block"><div class="label">Skill match</div>${primary.skillMatches.length ? `<ul>${primary.skillMatches.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "Unknown"}</div>
      <div class="block"><div class="label">Gaps</div>${primary.skillGaps.length ? `<ul>${primary.skillGaps.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : "Unknown"}</div>
    </div>`;
    const summaryHtml = summaryBlocks
      ? `<div class="summary"><h3>Summary</h3>${summaryBlocks}</div>`
      : "";
    const benefitsHtml = benefits.length
      ? `<div class="benefits"><h3>Benefits</h3><div class="list">${benefits.map((b) => `<span class="benefit">${esc(b)}</span>`).join("")}</div></div>`
      : "";
    const statusLabel = {
      needs_review: "pending",
      approved: "approved",
      skipped: "rejected",
      sent: "sent",
    }[bucket];
    const reminderHtml = showsApplicationReminder(group.jobs)
      ? `<div class="reminder"><strong>Apply first, then send this application note the same day or next day.</strong> Keep it brief: name the role and company, include one specific relevant proof, and don't ask for a call. No message is sent from this page.</div>`
      : "";
    const draftHtml = action.editable
      ? `<div class="draft">
          <h3>Draft message</h3>
          <label for="draft-subject">Subject</label>
          <input type="text" id="draft-subject" placeholder="(optional — only used when the composer shows a subject field)" value="${esc(primary.subject ?? "")}">
          <label for="draft-body">Body</label>
          <textarea id="draft-body" spellcheck="true">${esc(primary.message ?? "")}</textarea>
          <div class="draft-actions">
            <button data-action="save">Save</button>
            <button data-action="approve" class="primary">${action.replace ? "Use this draft instead" : "Approve &amp; next"}</button>
            <button data-action="skip">Reject &amp; next</button>
          </div>
          <div class="draft-hint">Blank lines between paragraphs are preserved. No message is sent from this page.</div>
        </div>`
      : `<div class="draft">
          <h3>Draft message</h3>
          ${primary.subject ? `<div class="draft-subject-read">${esc(primary.subject)}</div>` : ""}
          <div class="draft-read">${esc(primary.message ?? "")}</div>
          ${
            action.canReturn
              ? `<div class="draft-actions"><button data-action="return">Return to review</button></div>`
              : ""
          }
        </div>`;
    d.innerHTML = `
      <div class="detail-head">
        <div class="detail-kicker"><span class="status ${bucket}">${statusLabel}</span>${triageBadge(primary)}</div>
        <div class="person-line"><h2>${esc(displayName)}</h2>${badge(first?.degree)}</div>
        <div class="company">${esc(primary.title || "—")}${primary.company ? ` <span>at</span> ${esc(primary.company)}` : ""}</div>
        <div class="person-context">
          ${primary.location ? `<span>${esc(primary.location)}</span>` : ""}
          ${first?.headline ? `<span>${esc(first.headline)}</span>` : ""}
          ${first?.profileUrl ? `<a class="role-url" href="${esc(first.profileUrl)}" target="_blank" rel="noopener">Hiring profile ↗</a>` : ""}
        </div>
        ${workBrief}
        ${facts ? `<div class="facts">${facts}</div>` : ""}
        ${activity ? `<div class="activity">${activity}</div>` : ""}
        <div class="detail-actions"><a class="open" href="${esc(primary.postingUrl)}" target="_blank" rel="noopener">Open posting ↗</a></div>
      </div>
      ${rolesHtml}
      ${fitBrief}
      ${reminderHtml}
      ${draftHtml}
      ${summaryHtml}
      ${descHtml}
      ${benefitsHtml}
      ${(primary.evidenceGaps?.length ?? 0) ? `<div class="meta">Evidence gaps: ${primary.evidenceGaps.map(esc).join(", ")}</div>` : ""}
      <div class="meta">ID <span class="mono">${esc(primary.id)}</span> · captured ${esc((primary.collectedAt || "").slice(0, 10))}</div>`;
  }

  function renderSummary() {
    const c = counts();
    el("summary").textContent =
      `${SECTION_LABELS[state.section]} — ${c.needs_review} pending · ${c.approved} approved · ${c.skipped} rejected · ${c.sent} sent`;
  }

  function renderAll() {
    renderSummary();
    renderSections();
    renderFilters();
    renderList();
    renderDetail();
  }

  function flash(message) {
    const f = el("flash");
    f.textContent = message;
    f.classList.add("show");
    setTimeout(() => f.classList.remove("show"), 2200);
  }

  function flashError(message) {
    const f = el("flash");
    f.textContent = message;
    f.style.background = "#B3261E";
    f.classList.add("show");
    setTimeout(() => {
      f.classList.remove("show");
      f.style.background = "";
    }, 3200);
  }

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok || payload.ok !== true) {
      const err = payload?.error ?? {};
      const error = new Error(err.message ?? "request failed");
      error.code = err.code ?? "ERROR";
      if (err.details) error.details = err.details;
      throw error;
    }
    return payload.data;
  }

  async function loadPeople() {
    const res = await fetch("/api/salesnav/people");
    if (!res.ok) return;
    const payload = await res.json();
    state.people = payload?.data ?? [];
  }

  function renderPeople() {
    const elPeople = el("salesnav-people");
    if (!elPeople) return;
    elPeople.innerHTML = state.people.length
      ? state.people
          .map((p) => {
            const firmBrief = [p.services, p.concreteFact, p.firmReason].filter(Boolean),
              sourceUrls = jsonArray(p.firmSourceUrls),
              researchedWebsite = sourceUrls.find((value) => !value.includes("linkedin.com")),
              unknowns = jsonArray(p.firmUnknowns);
            return `<article class="sn-person">
                <div class="sn-person-main">
                  <div class="sn-person-kicker">${esc(p.lane)} · ${esc(p.slot)}</div>
                  <strong>${esc(p.name)}</strong>
                  <div>${esc(p.title)} at ${esc(p.organizationName || p.company)}</div>
                  ${p.location ? `<small>${esc(p.location)}</small>` : ""}
                  <p><b>Why this person:</b> ${esc(p.selectionReason || `Matched ${p.matchedRole}`)}</p>
                  ${firmBrief.length ? `<p><b>Why this firm:</b> ${firmBrief.map(esc).join(" · ")}</p>` : ""}
                  ${unknowns.length ? `<p><b>Unknowns:</b> ${unknowns.map(esc).join(" · ")}</p>` : ""}
                </div>
                <div class="sn-person-actions">
                  <a href="${esc(p.profileUrl)}" target="_blank" rel="noopener">Profile ↗</a>
                  ${p.companyUrl ? `<a href="${esc(p.companyUrl)}" target="_blank" rel="noopener">LinkedIn company ↗</a>` : ""}
                  ${p.websiteUrl || researchedWebsite ? `<a href="${esc(p.websiteUrl || researchedWebsite)}" target="_blank" rel="noopener">Website ↗</a>` : ""}
                  <button data-sn-review="approved" data-run="${esc(p.runId)}" data-person="${esc(p.personId)}">Approve</button>
                  <button data-sn-review="rejected" data-run="${esc(p.runId)}" data-person="${esc(p.personId)}">Reject</button>
                </div>
              </article>`;
          })
          .join("")
      : '<div class="empty">No Sales Navigator people pending.</div>';
  }

  async function reviewPerson(button) {
    const res = await fetch(
      `/api/salesnav/people/${encodeURIComponent(button.dataset.run)}/${encodeURIComponent(button.dataset.person)}/review`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ review: button.dataset.snReview }),
      },
    );
    if (!res.ok) return;
    await loadPeople();
    renderPeople();
    flash(button.dataset.snReview === "approved" ? "Approved" : "Rejected");
  }

  async function loadJobs() {
    const res = await fetch("/api/jobs");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    state.jobs = Array.isArray(payload) ? payload : (payload?.data ?? []);
    refreshGroups();
    if (state.selectedKey !== null && !state.groups.some((g) => g.key === state.selectedKey)) {
      state.selectedKey = null;
    }
  }

  function subjVal() {
    return el("draft-subject").value;
  }
  function bodyVal() {
    return el("draft-body").value;
  }
  function currentPrimaryId() {
    const g = selectedGroup();
    return g ? primaryOf(g).id : null;
  }

  async function saveDraft() {
    const jobId = currentPrimaryId();
    if (!jobId) return;
    try {
      const data = await post(`/api/jobs/${encodeURIComponent(jobId)}/draft`, {
        subject: subjVal(),
        message: bodyVal(),
      });
      state.jobs = state.jobs.map((j) => (j.id === jobId ? data : j));
      refreshGroups();
      renderAll();
      flash("Saved");
    } catch (e) {
      flashError(e.message);
    }
  }

  async function approve() {
    const jobId = currentPrimaryId();
    if (!jobId) return;
    let saved = null;
    try {
      if (bodyVal().trim()) {
        saved = await post(`/api/jobs/${encodeURIComponent(jobId)}/draft`, {
          subject: subjVal(),
          message: bodyVal(),
        });
        state.jobs = state.jobs.map((j) => (j.id === jobId ? saved : j));
        refreshGroups();
        renderAll();
      }
    } catch (e) {
      flashError(e.message);
      return;
    }
    try {
      await post(`/api/jobs/${encodeURIComponent(jobId)}/review`, { review: "approved" });
      await loadJobs();
      advanceAfterAction();
      renderAll();
      flash("Approved");
    } catch (e) {
      const details = e.details ?? null;
      if (e.code === "DUPLICATE_APPROVED_PROFILE" && details?.status === "drafted") {
        const title = details.title ?? "the other role";
        const replace = confirm(
          `This person already has an approved draft for "${title}". Use this draft instead?`,
        );
        if (replace) {
          try {
            await post(`/api/jobs/${encodeURIComponent(jobId)}/review`, {
              review: "approved",
              replaceId: details.jobId,
            });
            await loadJobs();
            advanceAfterAction();
            renderAll();
            flash("Approved — previous approval replaced");
          } catch (e2) {
            flashError(e2.message);
          }
        } else {
          flash("Saved. Existing approval unchanged.");
        }
      } else if (e.code === "DUPLICATE_APPROVED_PROFILE" && details?.status === "sent") {
        flash(`Saved. This person was already contacted for "${details.title ?? "that role"}".`);
      } else {
        flashError(e.message);
      }
    }
  }

  async function skipPerson() {
    const jobId = currentPrimaryId();
    if (!jobId) return;
    try {
      await post(`/api/recipients/${encodeURIComponent(jobId)}/review`, { review: "skipped" });
      await loadJobs();
      advanceAfterAction();
      renderAll();
      flash("Rejected");
    } catch (e) {
      flashError(e.message);
    }
  }

  async function returnPerson() {
    const group = selectedGroup();
    const jobId = group ? primaryOf(group).id : null;
    const key = group ? group.key : null;
    if (!jobId) return;
    try {
      await post(`/api/recipients/${encodeURIComponent(jobId)}/review`, { review: "needs_review" });
      state.filter = "needs_review";
      await loadJobs();
      state.selectedKey = key;
      renderAll();
      flash("Returned to review");
    } catch (e) {
      flashError(e.message);
    }
  }

  function advanceAfterAction() {
    state.selectedKey =
      sectionGroups().find((g) => bucketFor(g.jobs) === "needs_review")?.key ?? null;
  }

  el("search").addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    persistFilterState();
    if (state.selectedKey !== null && !visible().some((g) => g.key === state.selectedKey)) {
      state.selectedKey = visible()[0]?.key ?? null;
    }
    renderFilters();
    renderList();
    renderDetail();
  });
  el("showDraft").addEventListener("change", (e) => {
    el("detail").classList.toggle("hide-draft", !e.target.checked);
  });
  el("sections").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-section]");
    if (!btn) return;
    state.section = btn.dataset.section;
    persistFilterState("push");
    state.selectedKey = visible()[0]?.key ?? null;
    renderAll();
  });
  el("filters").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-filter]");
    if (!btn) return;
    state.filter = btn.dataset.filter;
    persistFilterState("push");
    state.selectedKey = visible()[0]?.key ?? null;
    renderAll();
  });
  el("filters").addEventListener("click", (e) => {
    if (!e.target.closest("[data-clear]")) return;
    state.query = "";
    state.filter = "needs_review";
    state.section = "all";
    el("search").value = "";
    persistFilterState("push");
    state.selectedKey = visible()[0]?.key ?? null;
    renderAll();
  });
  el("list").addEventListener("click", (e) => {
    const row = e.target.closest(".row");
    if (!row) return;
    state.selectedKey = row.dataset.key;
    renderList();
    renderDetail();
  });
  el("list").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest(".row");
    if (!row) return;
    e.preventDefault();
    state.selectedKey = row.dataset.key;
    renderList();
    renderDetail();
  });
  el("salesnav-people").addEventListener("click", (e) => {
    const button = e.target.closest("[data-sn-review]");
    if (button) reviewPerson(button);
  });
  el("detail").addEventListener("click", (e) => {
    if (e.target.closest("a[href]")) return;
    const role = e.target.closest("[data-select-job]");
    if (role) {
      const group = selectedGroup();
      if (group) {
        state.override.set(group.key, role.dataset.selectJob);
        renderList();
        renderDetail();
      }
      return;
    }
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "save") return saveDraft();
    if (action === "approve") return approve();
    if (action === "skip") return skipPerson();
    if (action === "return") return returnPerson();
  });
  el("detail").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const role = e.target.closest("[data-select-job]");
    if (!role) return;
    e.preventDefault();
    const group = selectedGroup();
    if (group) {
      state.override.set(group.key, role.dataset.selectJob);
      renderList();
      renderDetail();
    }
  });

  window.addEventListener("popstate", () => {
    restoreFilterState();
    state.selectedKey = visible()[0]?.key ?? null;
    renderAll();
  });

  restoreFilterState();
  Promise.all([loadJobs(), loadPeople()])
    .then(() => {
      renderPeople();
      state.selectedKey =
        sectionGroups().find((g) => bucketFor(g.jobs) === "needs_review")?.key ??
        sectionGroups()[0]?.key ??
        null;
      renderAll();
    })
    .catch((e) => {
      el("summary").textContent = "Failed to load";
      el("list").innerHTML = `<div class="error">Could not load people: ${esc(e.message)}</div>`;
    });
});
</script>


<header>
  <div class="brand">
    <h1>Outreach Review Queue</h1>
    <p class="sub" id="summary">Loading…</p>
  </div>
  <div class="controls">
    <label class="switch"><input type="checkbox" id="showDraft" role="switch" checked> Show draft</label>
    <input type="search" id="search" placeholder="Filter name, role, company, location, doing…" autocomplete="off">
  </div>
</header>
<div class="sections" id="sections"></div>
<div class="filters" id="filters"></div>
<section class="salesnav-review"><h2>Sales Navigator people</h2><div id="salesnav-people"></div></section>
<main>
  <section class="list" id="list"></section>
  <section class="detail" id="detail"><div class="empty">Select an opportunity to review</div></section>
</main>
<div class="flash" id="flash"></div>
