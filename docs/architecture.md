# linkedin-tools Architecture

`linkedin-tools` is a Python monorepo for LinkedIn networking,
recruiter/agency/advisor outreach, opportunity intelligence, comment extraction,
and local review tools.
The top-level CLI is `uv run linkedin-tools`.

## Apps

- `apps/network_automation`: Sales Navigator connection-request controller,
  source reservoirs, sent-page audit reconciliation, acceptance tracking,
  accepted follow-ups, and pending-invitation cleanup.
- `apps/recruiter_agency_outreach`: recruiter, agency, and advisor sourcing,
  lead capture, drafting, dashboards, guarded message dry-runs, and guarded
  message sends.
- `apps/opportunity_intel`: recommend-only source registry, query packs,
  post queues, provider imports, capture batches, scoring, and review exports.
- `apps/comment_extractor`: browser-backed and saved-HTML extraction for
  LinkedIn post comments.
- `apps/review_ui`: local FastAPI/Jinja review surfaces for opportunities,
  networking, recruiter/agency/advisor state, browser artifacts, and guarded
  actions.

## Shared Packages

- `packages/linkedin_browser`: Playwright/Chrome session management, browser
  artifacts, and guarded browser action primitives.
- `packages/linkedin_salesnav`: Sales Navigator capture, audit, saved-search,
  and profile primitives.
- `packages/linkedin_storage`: SQLite, JSONL, and CSV helpers.
- `packages/linkedin_reports`: report rendering helpers.
- `packages/linkedin_ui`: shared review UI support.
- `packages/linkedin_experiments`: experiment metrics and gate helpers.
- `packages/linkedin_common`: config, progress, URL, schema, and utility code.

## State

Runtime state lives under `~/Library/Application Support/linkedin-tools/`.
Each app owns its namespace:

```text
network-automation/
recruiter-agency-outreach/
opportunity-intel/
comment-extractor/
review-ui/
```

## Browser Execution

Browser-backed network commands default to Playwriter. Operators can select an
existing Playwriter session with `LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>` or
select the browser used for new Playwriter sessions with
`LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>`. Methods that have not been
ported fail explicitly with `Playwriter <method> is not ported yet`; rerun the
same command with `LINKEDIN_TOOLS_BROWSER_BACKEND=playwright` when the Python
Playwright CDP fallback is needed. Browser artifacts are written back to
app-owned state or explicit output directories so controller state can be
audited after uncertain browser behavior.

## Safety Boundaries

- Connection requests are owned by `linkedin-tools network`.
- Acceptance follow-ups are owned by `linkedin-tools network acceptance`.
- Recruiter/agency outreach sends drafted messages only; it must not click
  `Connect`.
- Opportunity intelligence is recommend-only.
- Real sends and withdrawals require explicit flags close to the browser action:
  `--allow-send` or `--allow-withdraw`.
