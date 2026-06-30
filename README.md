# linkedin-tools

Python monorepo for Hanif's LinkedIn networking, recruiter/agency/advisor outreach,
opportunity intelligence, comment extraction, and local review UI tools.

## Current Status

- Active CLI: `uv run linkedin-tools`
- Python package: `linkedin-tools`
- Runtime: Python, `uv`, SQLite, FastAPI/Jinja, Playwriter, and Python
  Playwright fallback paths
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
reconciliation, candidate reservoirs, acceptance tracking, accepted follow-ups,
and pending-invitation cleanup.

```sh
uv run linkedin-tools network --state-dir "$state_root/network-automation" status --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" plan --json
uv run linkedin-tools network --state-dir "$state_root/network-automation" report
uv run linkedin-tools network --state-dir "$state_root/network-automation" saved-searches --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance check --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" acceptance draft-followups --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup audit --session auto
uv run linkedin-tools network --state-dir "$state_root/network-automation" pending-cleanup capture --session auto
```

Browser-backed network commands default to the Playwriter backend. To reuse a
specific Playwriter session, set `LINKEDIN_TOOLS_PLAYWRITER_SESSION=<id>`. To
create a new Playwriter session in a specific browser profile, set
`LINKEDIN_TOOLS_PLAYWRITER_BROWSER_KEY=<key>` before running the command. If a
command reports `Playwriter <method> is not ported yet`, rerun that command with
the Python Playwright fallback:

```sh
LINKEDIN_TOOLS_BROWSER_BACKEND=playwright \
  uv run linkedin-tools network --state-dir "$state_root/network-automation" <command>
```

Use the session commands for scheduled browser automations that should keep one
managed Chrome instance open across the whole run:

```sh
uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  browser-session start --force --url https://www.linkedin.com/sales/search/people

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  run-session --session auto --target 30 --max-real-sends 30 --force --allow-send --finish

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  acceptance run-daily-session --session auto --min-age-days 1 --max-age-days 45

uv run linkedin-tools network \
  --state-dir "$state_root/network-automation" \
  browser-session stop
```

When `browser-session start` is running, later browser-backed network commands
using `LINKEDIN_TOOLS_BROWSER_BACKEND=playwright` auto-attach to the saved CDP
URL in `browser-session.json`. The window stays open between commands until
`browser-session stop`. Browser actions reuse an existing tab in the attached
context by default and do not bring Chrome to the foreground; a new tab is
created only when the context has no open pages.

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
  pending-cleanup run-session --session auto --withdraw-limit 1 --allow-withdraw
```

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

Search/watchlist rows become concrete post URLs through the browser-backed
search capture command. It uses the normal Chrome/Playwriter CDP path, writes
post URLs incrementally, and prints progress lines to stderr while it runs:

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
  linkedin_salesnav/
  linkedin_storage/
  linkedin_reports/
  linkedin_ui/
  linkedin_experiments/
tests/
docs/
```
