# Python LinkedIn Tools Monorepo PRD

## Status

Draft for Hanif review. Do not implement until this PRD is approved.

## Summary

Create a new Python monorepo that ports all current LinkedIn tooling from
`linkedin-network-automation` and adds the new recommend-only LinkedIn
opportunity intelligence system.

This is a full-port project. The migration is not complete until every existing
LinkedIn tool in the current Go/JavaScript repo has a Python equivalent with
verified behavior, state migration, browser automation parity, and safety gates.
Implementation may be ordered internally, but release scope is not partial.

## Goals

1. Move all current LinkedIn tools into one Python monorepo.
2. Preserve existing behavior for networking, acceptance tracking, pending
   cleanup, recruiter/agency outreach, and opportunity intelligence.
3. Add a first-class recommend-only opportunity intelligence pipeline for
   finding buying-signal comments from direct buyers.
4. Keep send-capable workflows strictly separated from recommend-only research
   workflows.
5. Use Python as the primary implementation language for CLI, data processing,
   browser automation, reporting, storage, and tests.
6. Make source experiments measurable, repeatable, and auditable.
7. Provide a local UI for reviewing the pertinent state, evidence, queues,
   safety status, and experiment results across the system.

## Non-Goals

1. Do not build outreach automation for opportunity-intel leads.
2. Do not weaken existing real-send or real-withdraw safety gates.
3. Do not mix recruiter/agency outreach state with networking state.
4. Do not mix recommend-only opportunity intelligence with send-capable modules.
5. Do not cut over from the current repo until parity and migration checks pass.

## Proposed Repository

Recommended repo name:

```text
linkedin-tools
```

Architecture decisions from review:

- Repository name: `linkedin-tools`.
- Compatibility command names exist only through migration.
- New state lives under `~/Library/Application Support/linkedin-tools/`; old
  state is read through explicit import commands.
- SQLite is the primary durable store for all apps.
- Browser automation uses the existing logged-in Chrome profile named
  `LinkedIn` as the default browser profile.
- The current Go/JavaScript repo is archived after parity and approved cutover.

Proposed layout:

```text
linkedin-tools/
  pyproject.toml
  uv.lock
  README.md
  docs/
    prd.md
    safety.md
    migration.md
    data-contracts.md

  apps/
    network_automation/
      cli.py
      controller.py
      acceptance.py
      pending_cleanup.py
      reservoir.py

    recruiter_agency_outreach/
      cli.py
      sourcing.py
      accounts.py
      drafts.py
      dashboard.py
      guarded_send.py

    opportunity_intel/
      cli.py
      sources.py
      post_discovery.py
      post_prioritization.py
      ranking.py
      experiments.py
      review_queue.py

    comment_extractor/
      cli.py
      linkedin_post_comments.py
      debug_artifacts.py

    review_ui/
      cli.py
      server.py
      routes.py
      view_models.py

  packages/
    linkedin_common/
      urls.py
      schemas.py
      time.py
      logging.py
      config.py

    linkedin_browser/
      sessions.py
      playwright.py
      selectors.py
      artifacts.py
      safety.py

    linkedin_salesnav/
      searches.py
      capture.py
      audit.py
      messages.py

    linkedin_storage/
      sqlite.py
      json_store.py
      migrations.py

    linkedin_reports/
      markdown.py
      tables.py
      dashboards.py

    linkedin_ui/
      components.py
      tables.py
      actions.py
      auth.py

    linkedin_experiments/
      metrics.py
      calibration.py
      gates.py

  data/
    .gitkeep

  tests/
    unit/
    integration/
    fixtures/
```

## App Boundaries

### `network_automation`

Owns connection-request networking runs and acceptance lifecycle.

Port from current `cmd/linkedin-network-run`, `internal/app`, and related
Sales Navigator scripts.

Responsibilities:

- Run controller state and planning.
- Candidate reservoirs.
- Sales Navigator capture import.
- Audits and reconciliation.
- Guarded connection requests.
- Acceptance outcome tracking.
- Accepted follow-up drafts and guarded accepted-follow-up sends.
- Pending invitation cleanup and guarded withdrawals.

Safety:

- Real connection sends require explicit `--allow-send`.
- Real accepted-follow-up sends require explicit `--allow-send`.
- Real pending withdrawals require explicit `--allow-withdraw`.
- Finish/reconcile commands must remain audit-backed.

### `recruiter_agency_outreach`

Owns recruiter/agency sourcing, drafting, dashboards, and guarded message sends.

Port from current `cmd/recruiter-agency-outreach`, `internal/outreach`, and
related Sales Navigator scripts.

Responsibilities:

- Recruiter and agency source capture.
- Account-first agency sourcing.
- Agency pool state.
- Contact promotion.
- Draft generation.
- Messageability validation.
- Dashboards and local review UI.
- Guarded sends for already-drafted messages.

Safety:

- Must not send connection requests.
- Must not write to networking controller state.
- Real LinkedIn messages require explicit `--allow-send`.
- `run-daily` remains sourcing/drafting/validation only and must reject send
  flags.

### `opportunity_intel`

Owns recommend-only buying-signal discovery and source experiments.

Port from current `cmd/linkedin-opportunity-intel`, `internal/opportunity`,
`configs/opportunity-sources.json`, `configs/opportunity-comment-signal-queries.json`,
and `docs/opportunity-intel-source-spike.md`.

Responsibilities:

- Source registry.
- Query packs.
- Creator/operator/product/company watchlists.
- Post discovery.
- Post prioritization.
- Actual-comment batch workspaces.
- Provider/manual/browser comment imports.
- Normalization and dedupe.
- Buyer-signal ranking.
- Source gates and experiment metrics.
- Calibration templates and reports.
- Review queue exports.
- Source decision and action plan.

Safety:

- Recommend-only.
- No send commands.
- No import from send-capable modules.
- No recruiter/staffing/job-seeker targets.
- Rows count only when they include actual LinkedIn `comment_text` from a named
  person plus LinkedIn post/profile URLs.

### `comment_extractor`

Owns browser-based extraction of comments from known LinkedIn post URLs.

This is a separate app inside the monorepo, not a separate repo.

Responsibilities:

- Input: post queue rows.
- Open LinkedIn post URLs.
- Expand comments/replies where possible.
- Extract visible comment text and commenter identity.
- Preserve screenshots/raw debug artifacts on failure.
- Output raw comment rows.

Non-responsibilities:

- No scoring.
- No source decisions.
- No outreach or sends.
- No opportunity recommendations.

### `review_ui`

Owns the local browser UI for reviewing the system.

Responsibilities:

- Show current status across all app namespaces.
- Show opportunity-intel source experiments, post queues, captured comments,
  ranked review queues, calibration labels, and source reports.
- Show networking controller status, candidate queues, audit status,
  acceptance drafts, pending cleanup plans, and send/withdraw safety state.
- Show recruiter/agency sourcing status, agency pool, lead queue, drafts,
  latest run, send readiness, and blockers.
- Show browser/session status, latest artifacts, failed captures, screenshots,
  and retryable errors.
- Provide review actions that mutate local state only when the underlying CLI
  behavior already supports that state transition.

Safety:

- The UI must not create new real-action paths.
- Any UI action that can send, message, or withdraw must call the same guarded
  command path as the CLI and require the same explicit approval flag or an
  equivalent explicit confirmation gate.
- Opportunity-intel UI pages are recommend-only and must not expose send,
  message, connect, or withdraw controls.
- The UI must make read-only versus state-changing actions visually clear.

## Existing Tool Inventory To Port

### `linkedin-network-run`

Top-level commands to port:

- `start`
- `audit`
- `import-audit`
- `next`
- `record`
- `record-send-result`
- `send-next`
- `send-guarded`
- `drain-stale-candidates`
- `reconcile-audit`
- `top-up-reconcile`
- `source-exhausted`
- `needs-reaudit`
- `resume-blocked`
- `import-capture`
- `record-top-up-result`
- `next-candidate`
- `candidates`
- `plan`
- `status`
- `report`
- `finish`
- `acceptance`
- `reservoir`
- `tune-sources`
- `pending-cleanup`

Acceptance subcommands to port:

- `seed-history`
- `import`
- `serve`
- `seed`
- `export`
- `report`
- `draft-followups`
- `send-followup`
- `dry-run-followups`
- `send-ready-followups`

Reservoir and pending-cleanup behavior to port:

- Reservoir capture/import/fill-run/report/clear/tune behavior.
- Pending cleanup start/plan/next/withdraw-next/status/finish behavior.
- Audit-backed finish behavior and age-threshold safety boundary.

Browser scripts to port:

- `scripts/salesnav-capture.js`
- `scripts/salesnav-audit.js`
- `scripts/salesnav-send-one.js`
- `scripts/salesnav-send-message-one.js`
- `scripts/salesnav-acceptance-outcomes.js`
- `scripts/salesnav-accepted-research.js`
- `scripts/salesnav-pending-capture.js`
- `scripts/salesnav-pending-withdraw-current.js`
- `scripts/salesnav-pending-withdraw-one.js`
- `scripts/salesnav-saved-searches.js`
- `scripts/salesnav-account-capture.js`
- CDP variants if still used: `salesnav-cdp-*`.

### `recruiter-agency-outreach`

Top-level commands to port:

- `run-daily`
- `capture`
- `capture-accounts`
- `import-capture`
- `import-accounts`
- `accounts`
- `agency-pool`
- `lead show`
- `queue`
- `draft`
- `dashboard`
- `last-run`
- `recommend-next-run`
- `revise`
- `serve`
- `send-ready`
- `send-message`
- `mark-message`
- `reject`
- `report`

Nested agency-pool behavior to port:

- Diagnose account pool.
- Pick next action.
- Build source artifact.
- Import source artifact.
- Import reviewed directory.
- Account-first sourcing.
- Contactability and drill-down tracking.
- Website/CMS agency evidence.

Browser scripts to port or consolidate:

- `scripts/salesnav-capture.js`
- `scripts/salesnav-account-capture.js`
- `scripts/salesnav-send-message-one.js`
- `scripts/salesnav-saved-searches.js`

### `linkedin-opportunity-intel`

Top-level commands to port:

- `sources`
- `query-pack`
- `collection-queue`
- `collection-coverage`
- `prepare-batch`
- `run-batch`
- `batch-status`
- `provider-readiness`
- `process-batch`
- `outx-interactions-csv`
- `outx-preflight`
- `outx-create-watchlists`
- `outx-fetch-interactions`
- `outx-fetch-watchlists`
- `validate-batch`
- `review-queue`
- `calibration-template`
- `calibration-report`
- `source-decision`
- `action-plan`
- `export-captures-csv`
- `merge-comments-csv`
- `provider-export-csv`
- `octolens-fetch-mentions`
- `apify-post-comments-run`
- `run-history`
- `checkpoint`
- `gate-report`
- `iteration-plan`
- `import-signals`
- `run-spike`
- `public-post-capture`
- `evaluate`
- `profile-enrich`
- `salesnav-feeder`
- `salesnav-activity run-spike`
- `salesnav-activity capture`
- `salesnav-activity evaluate`

Browser scripts to port:

- `scripts/linkedin-opportunity-search-comments-capture.js`
- `scripts/linkedin-opportunity-profile-enrich.js`
- `scripts/linkedin-opportunity-salesnav-activity-capture.js`

Configs and docs to port:

- `configs/opportunity-sources.json`
- `configs/opportunity-comment-signal-queries.json`
- `docs/comment-signal-import-template.csv`
- `docs/opportunity-intel-source-spike.md`
- `docs/python-linkedin-tools-pre-port-salvage.md`

Before implementation, use
`docs/python-linkedin-tools-pre-port-salvage.md` as source material for
requirements discovered during the previous source-spike implementation.

## New Opportunity Intelligence Requirements

### Source Registry

The system must support source records for:

- Creator/operator profiles.
- Company/product pages.
- Competitor-adjacent service providers.
- Complementary products.
- Keyword searches.
- Known high-signal posts.
- Manual seed lists.

Each source must include:

- `source_id`
- `source_type`
- `label`
- `url` or query fields
- `hypothesis`
- `target_needs`
- `priority`
- `enabled`
- `safety_notes`

### Post Discovery

The system must discover candidate posts from:

- LinkedIn post search.
- Creator/profile activity.
- Company page posts.
- Manual post imports.
- Search-engine seed imports when useful.

Output contract:

```text
post_queue.jsonl
```

Required fields:

- `post_id`
- `post_url`
- `source_id`
- `source_type`
- `post_author_name`
- `post_author_url`
- `post_text`
- `discovered_at`
- `priority_score`
- `priority_reasons`

### Post Prioritization

The prioritizer must rank posts before extraction using:

- Source priority.
- Topic match.
- Recency.
- Visible comment count when available.
- Post text likely to trigger operator pain comments.
- Exclusion terms for hiring, job seeking, recruiting, courses, templates, and
  vendor self-promotion.

### Comment Extraction

Input contract:

```text
post_queue.jsonl
```

Output contract:

```text
raw_comments.jsonl
```

Required fields:

- `post_url`
- `comment_text`
- `commenter_name`
- `commenter_profile_url`

Optional fields:

- `comment_id`
- `comment_url`
- `commenter_headline`
- `commenter_company`
- `commented_at`
- `relationship`
- `post_author_name`
- `post_text`
- `capture_artifacts`

The extractor must preserve debug artifacts for failed or partial captures.

### Normalization And Dedupe

The normalizer must:

- Canonicalize LinkedIn post/profile URLs.
- Deduplicate repeated comment rows.
- Validate required fields.
- Reject placeholder or malformed rows.
- Preserve source attribution.
- Preserve raw row references.

### Buyer-Signal Ranking

The ranker must identify:

- Direct buyer fit.
- First-person pain.
- Offer fit.
- Explicit ask.
- Urgency.
- Recruiter/staffing/job-seeker/vendor/agency noise.
- Evidence quote.
- Recommended review action.

Offer-fit categories:

- Internal tools.
- AI workflows/automation.
- Product engineering.
- Dashboards/reporting.
- Spreadsheet-heavy operations.
- Prototype productionization.

### Source Experiment Reporting

The reporter must answer:

- Which source buckets produced valid comments?
- Which source buckets produced qualified direct-buyer comments?
- Which creators/products/queries produced the best yield?
- Which sources are noisy and should be removed?
- Which query patterns should be changed?
- Which source configuration should be tested next?

Core metrics:

- Posts discovered.
- Posts extracted.
- Raw comments collected.
- Valid comments.
- Qualified comments.
- Qualified comments per 100 valid comments.
- Direct-buyer rate.
- Noise rate.
- Warm/hot count.
- Warm/hot per 100.
- Review queue count.

### Opportunity Review UI

The UI must make the opportunity-intel workflow reviewable without opening raw
CSV/JSON files for normal use.

Required views:

- Source registry: enabled/disabled sources, type, priority, hypothesis,
  target needs, and latest yield.
- Post queue: discovered posts, source attribution, prioritization reasons,
  extraction status, and extraction artifacts.
- Comment extraction runs: post URL, comments found, failures, screenshots,
  raw artifacts, and retry recommendation.
- Ranked comments: commenter, headline, profile URL, comment text, post URL,
  evidence quote, fit reasons, reject reasons, level, score, and source.
- Experiment report: source-level yield, qualified comments per 100, direct
  buyer rate, noise rate, warm/hot rate, best/worst sources, and next source
  decision.
- Calibration: labeling queue, machine label, human label, disagreement view,
  precision/recall, and false positive/false negative examples.

Opportunity UI actions:

- Mark source enabled/disabled.
- Add notes to a source, post, comment, or experiment run.
- Mark a comment as qualified, rejected, maybe, or skip for calibration.
- Export a review queue.
- Open LinkedIn profile or post URLs in the browser.
- Re-run read-only extraction or report commands.

Opportunity UI restrictions:

- No outreach automation.
- No send buttons.
- No connect buttons.
- No generated outreach messages.
- No hidden mutation of source, comment, or calibration labels.

### Cross-System Review UI

The UI must also surface the operational state of the existing ported tools.

Required networking views:

- Current run status and plan.
- Candidate reservoir and next candidate.
- Sent audit and reconciliation status.
- Acceptance candidates, accepted follow-up drafts, dry-run readiness, and send
  history.
- Pending cleanup plan, threshold, dry-run status, and withdraw history.

Required recruiter/agency views:

- Latest run summary.
- Agency account pool.
- Recruiter and agency lead queues.
- Draft status and messageability status.
- Send-ready queue and guarded send history.
- Source-quality and blocker diagnostics.

Required browser/artifact views:

- Configured browser profile.
- Current session state.
- Latest Playwright artifacts.
- Failed browser actions.
- Screenshots and raw JSON artifacts.
- Rate-limit or account-safety warnings.

## Safety Requirements

1. Send-capable commands must be isolated under send-capable apps only.
2. `opportunity_intel` and `comment_extractor` must not import guarded-send
   modules.
3. Every real send requires explicit `--allow-send`.
4. Every real withdrawal requires explicit `--allow-withdraw`.
5. Browser automation must reuse sessions/tabs where possible and avoid tab
   explosion.
6. Browser operations must write artifacts for ambiguous outcomes.
7. Account or network rate-limit responses must be blocking evidence, not
   retry noise.
8. Source extraction must not infer missing required fields from generic page
   text.
9. Classifiers must preserve evidence quotes and reject reasons.
10. Recommend-only review outputs must not contain send buttons or send
    commands.
11. The local UI must enforce the same action boundaries as the CLI.
12. UI pages must label read-only, state-changing, and real-action controls
    distinctly.
13. UI real-action controls must never bypass CLI safety gates.
14. Alpine.js state must remain local presentation state only; SQLite and
    server routes own durable state and mutations.

## Storage Requirements

Storage:

- SQLite is the primary durable app state for all namespaces.
- JSONL is used for raw capture/extraction streams.
- CSV is used for human review and provider import/export.
- Markdown is used for reports.
- JSON is used for machine-readable reports, run manifests, and raw artifacts.

Default state roots:

```text
~/Library/Application Support/linkedin-tools/network-automation/
~/Library/Application Support/linkedin-tools/recruiter-agency-outreach/
~/Library/Application Support/linkedin-tools/opportunity-intel/
```

Migration must read the current state roots:

```text
~/Library/Application Support/linkedin-network-run/
~/Library/Application Support/recruiter-agency-outreach/
/tmp/linkedin-opportunity-signals/
```

Migration must not delete or mutate old state during import.

## CLI Requirements

The monorepo should expose one top-level CLI:

```text
linkedin-tools
```

With app namespaces:

```text
linkedin-tools network ...
linkedin-tools recruiter-agency ...
linkedin-tools opportunity ...
linkedin-tools comments ...
```

Compatibility shims should exist for current command names during migration:

```text
linkedin-network-run ...
recruiter-agency-outreach ...
linkedin-opportunity-intel ...
```

The shims can call into the new Python implementation but must preserve command
contracts until cutover. After migration and approved cutover, the compatibility
command names should be removed or archived in favor of the new `linkedin-tools`
namespace architecture.

## Browser Automation Requirements

Use Python Playwright as the primary browser automation layer.

Default browser profile:

- Use the existing logged-in Chrome profile named `LinkedIn`.
- Store the profile selection in config rather than hard-coding a personal
  default profile.
- Do not use Hanif's ordinary personal browser profile as the automation
  default.
- Browser-session tooling must support changing the configured profile if the
  local Chrome profile path changes.

Browser layer must provide:

- Persistent session support.
- Named session reuse.
- Tab/page reuse.
- Screenshot artifacts.
- HTML/DOM snapshots where safe.
- Structured JSON outputs.
- Timeout controls.
- Rate-limit/error classification.
- Guarded click helpers for send/withdraw flows.

Existing Playwriter behavior must be mapped to equivalent Python Playwright
behavior before cutover.

## Migration Requirements

Full migration means:

1. Every current CLI command has a Python equivalent.
2. Every current browser script has a Python Playwright equivalent or a
   documented consolidated replacement.
3. Existing state can be imported or read without data loss.
4. Existing reports can be regenerated from migrated state.
5. Current tests have Python equivalents.
6. Send/withdraw safety gates have parity tests.
7. Opportunity-intel recommend-only boundaries have import-boundary tests.
8. The current Go repo can be frozen or archived after successful cutover.

## Multi-Thread Execution Requirement

Implementation should be orchestrated through multiple Codex threads with
separate goals and explicit ownership boundaries. The primary thread owns
coordination, architecture decisions, integration order, acceptance criteria,
and final cutover. Subthreads own scoped workstreams with non-overlapping file
paths, documented deliverables, tests, and handoff notes.

Supporting execution plan:

```text
docs/python-linkedin-tools-multi-thread-execution.md
```

Each subthread must receive:

- Launch setting: `5.5 extra high fast`.
- A concrete goal.
- Owned directories/files.
- Prohibited directories/files.
- Expected deliverables.
- Required tests.
- Handoff artifact path.
- Integration dependencies.
- Completion criteria.

No subthread should make architectural changes outside its assigned scope
without routing the decision back through the primary orchestration thread.

## Verification Plan

### Unit Tests

Required for:

- URL canonicalization.
- Source registry validation.
- State transitions.
- Importers.
- CSV/JSONL schemas.
- Deduplication.
- Buyer-signal classification.
- Gate metrics.
- Safety gates.

### Integration Tests

Required for:

- Networking plan/status/send dry-runs.
- Acceptance import/draft/dry-run flows.
- Pending cleanup dry-run flows.
- Recruiter/agency daily run without real sends.
- Recruiter/agency guarded send dry-run.
- Opportunity batch prepare/process/report.
- Comment extractor fixture pages.

### Browser Verification

Before cutover, verify:

- Sales Navigator capture.
- Sales Navigator audit.
- Guarded connection send dry-run.
- Guarded message send dry-run.
- Acceptance outcome capture.
- Pending invitation cleanup dry-run.
- LinkedIn post comment extraction.
- Comment extraction debug artifacts on failure.

### Parity Tests

For each ported command:

- Same required flags.
- Same default safety behavior.
- Same output schema or documented compatible replacement.
- Same state mutation semantics.
- Same real-action guard behavior.

## Rollout Requirements

Rollout must not be partial. Internal implementation can be sequenced, but the
new monorepo is not the default production toolchain until all existing tools
are ported and verified.

Suggested internal order:

1. Scaffold Python monorepo.
2. Port shared schemas, storage, reports, and browser session layer.
3. Port `opportunity_intel` and `comment_extractor`.
4. Port `network_automation`.
5. Port `recruiter_agency_outreach`.
6. Implement state importers and compatibility CLIs.
7. Implement the local review UI.
8. Run parity and browser verification.
9. Archive old Go/JS repo only after user approval.

This is an implementation order, not a staged product release.

## Acceptance Criteria

The PRD is implemented only when all of these are true:

1. The Python monorepo exists and installs with `uv sync`.
2. `linkedin-tools` exposes all planned namespaces.
3. Compatibility commands exist for the three current CLIs.
4. All current networking behavior is ported.
5. All current recruiter/agency behavior is ported.
6. All current opportunity-intel behavior is ported.
7. The new comment extractor works from known post URLs into `raw_comments.jsonl`.
8. Opportunity-intel can run a full source experiment from source registry to
   review queue and source report.
9. Real-send and real-withdraw safety gates are covered by tests.
10. Recommend-only opportunity modules cannot call send/withdraw code.
11. Existing state can be imported without mutating old state.
12. Tests pass.
13. Browser dry-runs pass.
14. The local review UI exposes the required opportunity, networking,
    recruiter/agency, and browser/artifact views.
15. UI safety tests prove recommend-only pages cannot call send/withdraw code
    and real-action controls use the same guarded command paths as the CLI.
16. Hanif reviews and approves cutover.

## Resolved Review Decisions

1. Repo name: `linkedin-tools`.
2. Compatibility command names remain only through migration.
3. New monorepo uses `~/Library/Application Support/linkedin-tools/` as the
   state root and imports old state through explicit import commands.
4. SQLite is the primary durable store.
5. Browser automation uses the existing logged-in Chrome profile named
   `LinkedIn`.
6. The old Go/JavaScript repo is archived after parity and approved cutover.

## Additional UI Decisions

1. UI technology: FastAPI with server-rendered Jinja templates, HTMX for
   server-backed partial updates, and Alpine.js for lightweight local UI state.
   Do not start with a separate React app.
2. UI access: bind to localhost by default and require a generated local access
   token for state-changing or real-action pages.
3. UI cutover scope: all required opportunity, networking, recruiter/agency,
   and browser/artifact views must be available before cutover.
