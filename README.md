# linkedin-tools

Python monorepo for Hanif's LinkedIn networking, recruiter/agency/advisor outreach,
opportunity intelligence, comment extraction, and local review UI tools.

## Current Status

- Active CLI: `uv run linkedin-tools`
- Python package: `linkedin-tools`
- Runtime: Python, `uv`, SQLite, FastAPI/Jinja, and Playwriter
- State root: `~/Library/Application Support/linkedin-tools/`
- Primary namespaces: network automation, recruiter/agency/advisor outreach,
  opportunity intelligence, comment extraction, and review UI

## Install And Verify

```sh
uv sync
uv sync --extra dev
uv run linkedin-tools --help
uv run pytest
uv run ruff check apps packages tests
uv run mypy apps packages tests
```

`uv sync` installs runtime dependencies. Use `uv sync --extra dev` before
running tests, lint, or type checks.

## CLI Namespaces

The primary runtime namespaces are:

```sh
uv run linkedin-tools network --help
uv run linkedin-tools recruiter-agency --help
uv run linkedin-tools opportunity --help
uv run linkedin-tools comments --help
uv run linkedin-tools ui --help
```

## State Layout

Runtime state lives under:

```text
~/Library/Application Support/linkedin-tools/
```

Namespaces:

```text
network-automation/
recruiter-agency-outreach/
opportunity-intel/
comment-extractor/
review-ui/
```

Most runtime examples below use this shell variable:

```sh
state_root="$HOME/Library/Application Support/linkedin-tools"
```

## Network Automation

The network namespace owns Sales Navigator connection-request runs, audit
reconciliation, candidate reservoirs, acceptance tracking, welcome messages,
and pending-invitation cleanup.

```sh
uv run linkedin-tools network --state-dir "$state_root/network-automation" status --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" plan --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" report
uv run linkedin-tools network --state-dir "$state_root/network-automation" sends --date today --timezone local --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" saved-searches --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance run-daily-session --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance prepare-welcome-messages --limit 30
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup audit --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup capture --session auto --load-more 40
```

Browser-backed network commands use Playwriter only. To reuse a specific
Playwriter session, set `LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>`. To create a
new Playwriter session in a specific browser profile, set
`LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>` before running the command.

Scheduled browser automations run directly through the controller command:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  run-session --daily --session auto --target 30 --max-real-sends 30 \
  --refresh-saved-searches --no-fallback --allow-send --finish

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance run-daily-session --session auto --min-age-days 1 --max-age-days 45 \
  --timezone America/Argentina/Buenos_Aires

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance report --daily-days 30 \
  --timezone America/Argentina/Buenos_Aires --json
```

The daily acceptance run stores an immutable `first_observed_accepted_at`
transition for each durably confirmed connection and a coverage record for the
run. Reports group those first observations by local date and show 7-day and
30-day averages. A blocked or missing scan remains visibly incomplete rather
than being reported as zero acceptances. The metric is therefore "new
acceptances confirmed per day"; LinkedIn does not provide a trustworthy exact
acceptance timestamp through this workflow.

`run-session --daily` owns the local-day decision: it resumes an approved
unfinished carryover first, then starts or resumes today's 30-send objective.
The 15/10/5 source mix is the preferred allocation across the three approved
sources, not a completion gate. It refuses unapproved source contracts, unclear
partial-day ledgers, and concurrent mutating controller processes.

Daily source quotas guide selection in declared order. Durable and pending
provisional sends consume active capacity; a proven failed or reverted send
releases capacity. When an approved source is exhausted, its shortfall carries
to the next approved source so the workflow can still reach 30. `--no-fallback`
continues to exclude every source outside the approved three. `--max-real-sends`
limits active sends rather than lifetime attempts, so proven failures can be
replaced while the durable target remains incomplete.

`run-session` pauses at a normal, non-terminal review checkpoint whenever it
captures new connection candidates. The packet at
`/tmp/linkedin-network-session/lead-review-candidates.json` includes a unique
`packet_id`. Write a fresh decisions artifact with that same id and exactly one
decision per candidate:

```json
{
  "packet_id": "<current packet id>",
  "decisions": [
    {
      "lead_key": "linkedin:https://www.linkedin.com/sales/lead/...",
      "status": "approved",
      "reason": "Evidence from the current packet"
    }
  ]
}
```

Apply the decisions, then resume with `--finish`. Repeat for every new packet
until `network report` shows `State: Done` and the durable target is verified:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  apply-lead-decisions /tmp/linkedin-network-session/lead-review-candidates-decisions.json

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  run-session --daily --session auto --target 30 --max-real-sends 30 \
  --no-fallback --allow-send --finish \
  --out-dir /tmp/linkedin-network-session
```

Decisions with a stale packet id, missing candidate, extra candidate, or
duplicate candidate are rejected before any send can continue.

Completion requires 30 durable sends, zero provisional sends, and a final
sent-page reconciliation. The final source distribution is reported for
visibility but does not block completion. The raw pending-page delta is a
sanity check rather than a strict equality gate because accepted invitations
can leave that page; profile evidence may durably confirm those sends. When the
controller recorded exactly the full guarded attempt set, nothing failed or
reverted, and the sent-page delta exactly equals the target, that complete
aggregate reconciliation also confirms any remaining unmatched provisional
request instead of leaving the day open for an unsafe replacement.

Browser failures during `run-session` produce a controller-owned incident JSON,
Markdown packet, structured Playwriter diagnostic, and screenshot in the
session output directory. A non-send failure moves the run to
`NeedsBrowserInspection`; the packet contains a lease limited to inspection,
reload, expected-URL navigation, ordinary obstruction dismissal, session
reconnection, and retry. Use Chrome only on the exact automation-owned tab,
capture before/after artifacts, write the packet-bound receipt, and apply it:

While a controller process is active, poll that exact process until it exits;
never interrupt or kill it during a browser operation. If an external
interruption reaches a real-send call, the supervisor records the candidate as
a possible send and moves the run to the audit-only recovery path before
returning control.

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  browser-inspection status

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  browser-inspection apply \
  /tmp/linkedin-network-session/001-browser-incident-recovery-receipt.json
```

The controller validates the incident and lease ids, allowed actions, specific
evidence, and existing before/after files before restoring the prior run state.
A browser exception during a real send is instead recorded as a provisional
attempt and moves to `NeedsReaudit`; it cannot accept a Chrome recovery receipt
or be replaced by another candidate.

For network sends, Sales Navigator saved searches are the source of truth for
each configured source. The controller stores durable per-source scan progress
in `source-progress.json` and seeds new forced runs from that file when the
saved-search id still matches. That makes daily runs continue forward through a
stable saved search instead of restarting at page 1. A source is exhausted only
when capture reaches the end of results, not merely because a few captures
returned zero connectable rows.

The active consulting mix is 30 connection requests per run:

- 15 from `Consulting - Founder Owner Buyers`
- 10 from `Consulting - Operations Leader Buyers`
- 5 from `Consulting - Trusted Referral Partners`

The first two are buyer sources. The third is for complementary referral or
advisory relationships: fractional COOs and operations or strategy consultants
who serve founder-led businesses without overlapping Hanif's custom software or
automation delivery. Historical recruiter, agency-owner, paid-media, and old
implementation-partner source names remain readable on old acceptance records
but are not part of the active source mix.

Connection-request counts are stored in `$STATE/network.sqlite` as soon as a
send attempt is recorded or a provisional send is confirmed. Use
`network sends --date today --timezone local --json` to answer daily send
counts across replaced active runs. `--sync-history` backfills the SQLite
ledger from older per-run JSONL logs. Existing `send-ledger.jsonl` files remain
readable migration sources.

Every durably confirmed accepted connection is eligible for the same welcome
message. Relationship enrichment is a separate, non-blocking process that
decides whether someone is promising enough for the narrow buyer watchlist.

Prepare and record eligible greetings immediately after the acceptance import:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance prepare-welcome-messages \
  --out /tmp/linkedin-accepted-greetings/eligibility.json \
  --report-out /tmp/linkedin-accepted-greetings/greetings.md \
  --limit 30
```

The controller requires a durable accepted identity, a usable LinkedIn or Sales
Navigator profile URL, and the exact welcome copy. Original connection-review
evidence is retained when available, but it does not gate the welcome.
Enrichment status starts as `missing` and does not block the message.

The exact approved first message is:

> Hey [First name], thanks for connecting. Glad to be in each other’s network, and I’m looking forward to following what you share here.

The guarded welcome run dry-runs the exact stored message and then sends it. It
does not save the person to any Sales Navigator list:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance run-welcome-messages \
  --session auto \
  --limit 30 \
  --allow-send
```

Relationship enrichment runs afterward. It reuses original connection-review
evidence and queues only records whose enrichment is missing, at least 30 days
old, or explicitly selected with `--prioritize-engagement`:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance export-enrichment-queue \
  --out /tmp/linkedin-relationship-radar/enrichment-queue.json \
  --stale-after-days 30 \
  --limit 30

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance launch-enrichment-workers \
  --enrichment-queue /tmp/linkedin-relationship-radar/enrichment-queue.json \
  --sources-dir "$state_root/network-automation/relationship-radar/source-bundles" \
  --jobs-dir /tmp/linkedin-relationship-radar/enrichment-jobs

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance collect-enrichment-workers \
  --enrichment-queue /tmp/linkedin-relationship-radar/enrichment-queue.json \
  --jobs-dir /tmp/linkedin-relationship-radar/enrichment-jobs \
  --out /tmp/linkedin-relationship-radar/enrichment-decisions.json

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance update-relationship-radar \
  --enrichment /tmp/linkedin-relationship-radar/enrichment-decisions.json
```

After research updates the radar, source-backed buyers with a supported
operating reason can be saved to the single narrow list
`Watch - Business Systems Prospects`:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance save-watchlist-leads \
  --session auto \
  --limit 30 \
  --allow-save
```

The list must already exist. The guarded command saves only exact radar
recommendations and verifies the resulting list state. Hanif reviews that list
and comments manually; the system never drafts or posts comments.

Records that remain `needs_review` can enter a separate authenticated-browser
investigation, capped at five records and one completed check per 30 days:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance export-browser-investigation-queue \
  --out /tmp/linkedin-prospect-investigation/queue.json \
  --cooldown-days 30 \
  --limit 5

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance apply-browser-investigation \
  --queue /tmp/linkedin-prospect-investigation/queue.json \
  --enrichment /tmp/linkedin-prospect-investigation/decisions.json
```

The decisions file uses the normal relationship-enrichment artifact schema and
must set `source_path` to the exact queue path. Applying it requires complete
coverage and exact candidate identity. A failed or incomplete Chrome run does
not start the cooldown. Browser investigation is read-only; only the existing
guarded watchlist command may perform a subsequent list save.

Enrichment decisions classify each accepted connection as a buyer, referral
partner, hiring/recruiter relationship, or other. They
also record priority, the exact visible signal and URL when one exists, the
follow-up reason, the next useful action, and the fixed `review_only`
permission boundary. The cumulative radar is written to
`$STATE/relationship-radar/ledger.json` and `.md`; updating it performs no
LinkedIn action.

Research remains `review_only`; it cannot send messages or comments. Welcome
messages and exact-list saves are separate guarded commands with explicit
authorization flags.

Browser-backed commands default to guarded dry-run behavior unless the explicit
real-action flag is provided:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  send-next --session auto --dry-run

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  send-guarded --session auto --single-pass --max-attempts 30 --allow-send
```

Pending cleanup also requires an explicit approval flag for real withdrawals:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup withdraw-next --session auto --dry-run

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup withdraw-next --session auto --allow-withdraw

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  pending-cleanup run-session --session auto --capture-load-more 40 --withdraw-limit 1 --allow-withdraw
```

The normal `pending-cleanup run-session` path audits, captures with deep
scrolling, then withdraws from the already-loaded bottom rows. This avoids
reopening the sent page and trying to find one stale candidate from the top of
LinkedIn's virtualized invitation list. Use `withdraw-next` only for focused
one-person debugging.

If final finish count does not match the verified withdrawals, `run-session`
runs one read-only post-check capture. When that post-check finds no stale
invitations left, the command exits successfully with `Finished with count
warning`; the controller state stays out of `Done` until the count warning is
reviewed. If stale invitations still remain, the command stops with that
blocker.

## Recruiter, Agency, And Advisor Outreach

The recruiter/agency namespace owns agency account sourcing, recruiter/advisor
lead capture, drafting, dashboarding, guarded message dry-runs, and guarded
sends. It must not send connection requests.

```sh
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  dashboard --print-markdown

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  queue --limit 20 --include-drafts

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  report --json
```

Message sends remain guarded:

```sh
uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  send-message --lead-id <lead-id> --session auto --dry-run

uv run linkedin-tools recruiter-agency \
  --state-dir "$state_root/recruiter-agency-outreach" \
  send-message --lead-id <lead-id> --session auto --allow-send
```

## Opportunity Intelligence And Comments

Opportunity intelligence is recommend-only. It ranks and reviews buyer-signal
comments but does not send messages, connect, withdraw, or otherwise take
LinkedIn actions.

```sh
uv run linkedin-tools opportunity status --json
uv run linkedin-tools opportunity sources --json
uv run linkedin-tools opportunity post-queue --out /tmp/linkedin-opportunity-posts.csv
```

Search/watchlist rows become concrete post URLs through the Playwriter-backed
search capture command. It writes post URLs incrementally and prints progress
lines to stderr while it runs:

```sh
uv run linkedin-tools opportunity capture-search-posts \
  --post-queue /tmp/linkedin-opportunity-posts.csv \
  --out /tmp/linkedin-opportunity-search-posts.csv \
  --max-results-per-search 50
```

Run preflight before collection. This validates the configured source batch,
syncs sources and post candidates into SQLite, checks the configured Chrome
profile path, and writes a browser preflight artifact without collecting
comments:

```sh
uv run linkedin-tools opportunity preflight \
  --state-dir "$state_root/opportunity-intel" \
  --json
```

Known-post URL extraction uses the local LinkedIn browser profile and persists
the run, artifacts, comments, people, rankings, errors, and status transitions
to SQLite:

```sh
uv run linkedin-tools comments extract-url \
  --post-url <linkedin-post-url> \
  --source-id <source-id> \
  --query-id <query-id> \
  --state-dir "$state_root/opportunity-intel" \
  --out-dir "$state_root/opportunity-intel/artifacts"
```

Live extraction prints progress lines to stderr for each expansion pass and
records why the post stopped expanding. Queue extraction also writes its
checkpoint after each processed URL.

Useful browser safety limits are configurable on `extract-url`:

```sh
--max-scrolls 6 \
--max-comment-control-clicks 12 \
--max-reply-control-clicks 8 \
--navigation-timeout-ms 30000 \
--action-timeout-ms 5000 \
--max-runtime-seconds 90 \
--max-no-progress-passes 2
```

The scroll and click limits are ceilings. Extraction stops earlier when recent
passes produce no new comment nodes, no usable expansion controls, and no
meaningful scroll-height or scroll-position change.

Post queues can be narrowed after a measured extraction pass. The prefilter
reads the URL queue manifest, keeps only posts whose measured `comments_found`
meets the threshold, and writes a metrics CSV with every keep/reject reason:

```sh
uv run linkedin-tools opportunity prefilter-post-queue \
  --post-queue /tmp/linkedin-opportunity-posts.csv \
  --manifest /tmp/linkedin-opportunity-live/extract_url_queue_manifest.jsonl \
  --min-comments 10 \
  --out /tmp/linkedin-opportunity-posts.filtered.csv \
  --metrics-out /tmp/linkedin-opportunity-posts.prefilter-metrics.csv
```

Saved HTML extraction remains available and can also persist to SQLite:

```sh
uv run linkedin-tools comments extract \
  --post-url <linkedin-post-url> \
  --html /path/to/post.html \
  --source-id <source-id> \
  --query-id <query-id> \
  --state-dir "$state_root/opportunity-intel" \
  --out-dir /tmp/linkedin-comments
```

The scoring model is:

- `0-4` problem fit
- `0-4` buying signal
- `0-3` buyer fit
- `0-2` actionability
- `0-2` immediacy

Classification is `strong` for 11-15, `possible` for 7-10, `weak` for 4-6,
and `irrelevant` for 0-3. Recruiter, agency, vendor, and job-seeker signals
are rejected regardless of score.

Source experiments:

```sh
uv run linkedin-tools opportunity run-experiment \
  --comments-csv /path/to/comments.csv \
  --out-dir /tmp/linkedin-opportunity-intel \
  --run-id source-test
```

## Local Review UI

```sh
uv run linkedin-tools ui \
  --host 127.0.0.1 \
  --port 8787 \
  --opportunity-state-dir "$state_root/opportunity-intel"
```

The UI exposes review surfaces for opportunities, networking state,
recruiter/agency/advisor state, browser artifacts, and guarded action paths.
Opportunity review labels persist to SQLite: `strong`, `possible`, `weak`,
`reject`, `needs research`, and `ready for outreach`. Reject reasons are
`recruiter`, `agency`, `vendor`, `job seeker`, `not buyer`, `not relevant`,
and `duplicate`.

## Safety Rules

- No real LinkedIn sends without `--allow-send`.
- No real pending-invitation withdrawals without `--allow-withdraw`.
- Networking and recruiter/agency/advisor outreach suppress exact-profile
  overlap across their default state dirs before Connect or message actions.
- Opportunity intelligence is recommend-only.
- Browser flows should start with dry-runs.

## Project Layout

```text
apps/
  cli.py
  network_automation/
  recruiter_agency_outreach/
  opportunity_intel/
  comment_extractor/
  review_ui/
packages/
  linkedin_common/
  linkedin_browser/
  linkedin_storage/
  linkedin_ui/
tests/
docs/
```
