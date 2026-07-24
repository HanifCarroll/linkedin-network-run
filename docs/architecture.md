# linkedin-tools Architecture

`linkedin-tools` is a Python monorepo for LinkedIn networking, opportunity intelligence, comment extraction,
and local review tools.
The top-level CLI is `uv run linkedin-tools`.

## Apps

- `apps/network_automation`: Sales Navigator connection-request controller,
  source reservoirs, sent-page audit reconciliation, acceptance tracking,
  accepted follow-ups, and pending-invitation cleanup.
- `apps/opportunity_intel`: recommend-only source registry, query packs,
  post queues, provider imports, capture batches, scoring, and review exports.
- `apps/comment_extractor`: browser-backed and saved-HTML extraction for
  LinkedIn post comments.
- `apps/review_ui`: local FastAPI/Jinja review surfaces for opportunities,
  networking, browser artifacts, and guarded
  actions.

## Shared Packages

- `packages/linkedin_browser`: browser artifacts, reusable page helpers, state
  classification, and guarded browser action primitives.
- `packages/linkedin_storage`: SQLite migration and legacy-state import helpers.
- `packages/linkedin_ui`: shared review UI support.
- `packages/linkedin_common`: state paths, progress, URL, and profile identity helpers.

## State

Runtime state lives under `~/Library/Application Support/linkedin-tools/`.
Each app owns its namespace:

```text
network-automation/
opportunity-intel/
comment-extractor/
review-ui/
```

## Browser Execution

Browser-backed commands use Playwriter only. Operators can select an existing
Playwriter session with `LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>` or select the
browser used for new Playwriter sessions with
`LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>`. Browser artifacts are written
back to app-owned state or explicit output directories so controller state can
be audited after uncertain browser behavior.

## Safety Boundaries

- Connection requests are owned by `linkedin-tools network`.
- Acceptance follow-ups are owned by `linkedin-tools network acceptance`.
- The network controller retains exact-profile safeguards before guarded browser actions.
- Opportunity intelligence is recommend-only.
- Real sends and withdrawals require explicit flags close to the browser action:
  `--allow-send` or `--allow-withdraw`.
