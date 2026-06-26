# Opportunity Source Batch v0

This is the first runnable source batch for the recommend-only LinkedIn
opportunity experiment. It turns the report's source ideas into a concrete
queue that can be used to find posts, collect visible comments, and test
whether the comments contain direct-buyer signals for product engineering,
internal tools, and AI workflow work.

## Source Buckets

The registry at `apps/opportunity_intel/data/source_registry.v1.json` includes
38 enabled sources:

- 1 known-post seed source with high-signal dashboard and spreadsheet posts.
- 10 creator/operator audience watchlists.
- 12 complementary product/company page monitors with audience searches.
- 4 competitor-adjacent audience searches.
- 6 pain-language LinkedIn searches.
- 3 secondary discovery searches.
- 2 CSV import sources for provider or manual actual-comment rows.

The batch is intentionally recommend-only. It creates search and capture inputs;
it does not send messages, connect, withdraw, or change LinkedIn state.

## Generate The Queue

```sh
uv run linkedin-tools opportunity validate-contracts
uv run linkedin-tools opportunity collection-coverage --json
uv run linkedin-tools opportunity preflight \
  --state-dir "$HOME/Library/Application Support/linkedin-tools/opportunity-intel" \
  --json
uv run linkedin-tools opportunity post-queue --out /tmp/linkedin-opportunity-v0/post-queue.csv
uv run linkedin-tools opportunity provider-export-csv --out /tmp/linkedin-opportunity-v0/provider-comments.csv
```

`preflight` does not collect comments. It validates the registry/query pack,
syncs the configured source batch into SQLite, checks the configured Chrome
profile path, and writes a browser preflight artifact under the opportunity
state directory.

`post-queue.csv` has three row shapes:

- Known-post rows: `post_url` is filled. These are ready for comment capture
  once the visible post HTML is available.
- Company-page monitor rows: `source_kind` is `company_page`, `reason` is
  `company_page_posts`, `post_url` is empty, and `source_url` points at the
  company's LinkedIn posts tab. Capture that page's visible HTML, then convert
  it into post URLs with `company-post-capture`.
- Watchlist/search rows: `post_url` is empty, while `source_url` and
  `search_query` are filled. Use these rows to find candidate LinkedIn posts,
  then collect comments from selected post URLs.

Convert search/watchlist rows into concrete post URLs with the browser-backed
search capture command. It writes captured post URLs, metrics, and a checkpoint
incrementally, and prints `progress ...` lines to stderr while it works:

```sh
uv run linkedin-tools opportunity capture-search-posts \
  --post-queue /tmp/linkedin-opportunity-v0/post-queue.csv \
  --out /tmp/linkedin-opportunity-v0/search-post-queue.csv \
  --metrics-jsonl /tmp/linkedin-opportunity-v0/search-post-capture.metrics.jsonl \
  --checkpoint /tmp/linkedin-opportunity-v0/search-post-capture.checkpoint.json \
  --max-results-per-search 50
```

Example company-page conversion:

```sh
uv run linkedin-tools opportunity company-post-capture \
  --source-id product_retool \
  --html /tmp/linkedin-opportunity-v0/company-pages/product_retool.html \
  --out /tmp/linkedin-opportunity-v0/company-pages/product_retool-post-queue.csv
```

The output uses the same post queue columns as `post-queue.csv`, with concrete
`post_url` values ready for visible-comment capture.

## Browser Comment Extraction

Use the `comments` namespace for LinkedIn post comment extraction. It owns the
post URL to comment rows process, including browser navigation, comment/reply
expansion, scrolling, artifacts, errors, and SQLite persistence.

```sh
uv run linkedin-tools comments extract-url \
  --post-url <linkedin-post-url> \
  --source-id <source-id> \
  --query-id <query-id> \
  --state-dir "$HOME/Library/Application Support/linkedin-tools/opportunity-intel" \
  --out-dir "$HOME/Library/Application Support/linkedin-tools/opportunity-intel/artifacts"
```

For a post queue, use the live URL queue runner so each processed post writes
its own run artifacts, appends a manifest row, refreshes the provider CSV from
SQLite, and writes a checkpoint before moving to the next URL:

```sh
uv run linkedin-tools comments extract-url-queue \
  --post-queue /tmp/linkedin-opportunity-v0/post-queue.csv \
  --state-dir /tmp/linkedin-opportunity-v0/state \
  --out-dir /tmp/linkedin-opportunity-v0/live-capture \
  --provider-csv /tmp/linkedin-opportunity-v0/provider-comments.csv
```

`opportunity run-batch` is wired to the same URL queue runner.

After a measured URL queue pass, narrow the next queue to posts that actually
produced enough comments. The prefilter reads `extract_url_queue_manifest.jsonl`,
writes a same-shape post queue for the selected posts, and writes an audit CSV
with the measured `comments_found` and keep/reject reason for every candidate:

```sh
uv run linkedin-tools opportunity prefilter-post-queue \
  --post-queue /tmp/linkedin-opportunity-v0/post-queue.csv \
  --manifest /tmp/linkedin-opportunity-v0/live-capture/extract_url_queue_manifest.jsonl \
  --min-comments 10 \
  --out /tmp/linkedin-opportunity-v0/post-queue.filtered.csv \
  --metrics-out /tmp/linkedin-opportunity-v0/post-queue.prefilter-metrics.csv
```

Configurable safety limits:

```sh
--max-scrolls 6
--max-comment-control-clicks 12
--max-reply-control-clicks 8
--navigation-timeout-ms 30000
--action-timeout-ms 5000
--max-runtime-seconds 90
--max-no-progress-passes 2
```

The scroll and click limits are hard ceilings, not fixed work amounts. Live
extraction stops earlier when recent passes produce no new comment nodes, no
usable expansion controls, and no meaningful scroll-height or scroll-position
change. Each run summary records `stop_reason`, `scrolls_performed`,
`comment_control_clicks`, `reply_control_clicks`, `comments_found`, and
`runtime_seconds`.

The extractor uses your real Google Chrome root and the Chrome profile named
`LinkedIn` by default. It does not attach to the Playwriter CDP endpoint unless
`--cdp-url` is passed explicitly. Use `LINKEDIN_TOOLS_BROWSER_PROFILE_MODE` to
switch roots:

```sh
# Default: your real Google Chrome root.
export LINKEDIN_TOOLS_BROWSER_PROFILE_MODE=real
export LINKEDIN_TOOLS_CHROME_PROFILE_NAME=LinkedIn

# Opt-in: isolated normal Google Chrome root for source experiments.
export LINKEDIN_TOOLS_BROWSER_PROFILE_MODE=automation
export LINKEDIN_TOOLS_CHROME_PROFILE_NAME=LinkedIn
```

Use `LINKEDIN_TOOLS_BROWSER_PROFILE_MODE=custom` plus
`LINKEDIN_TOOLS_CHROME_USER_DATA_DIR` for another explicit root.

The isolated root needs its own LinkedIn login once. Chrome's newer remote
debugging protections require a non-default data dir for automation debugging,
and that non-default data dir uses a different encryption key, so copying a
profile folder does not reliably copy the logged-in session. It remains
recommend-only and does not send messages, connect, withdraw, or click guarded
LinkedIn actions.

For saved HTML fixtures or manual captures:

```sh
uv run linkedin-tools comments extract \
  --post-url <linkedin-post-url> \
  --html /path/to/post.html \
  --source-id <source-id> \
  --query-id <query-id> \
  --state-dir "$HOME/Library/Application Support/linkedin-tools/opportunity-intel" \
  --out-dir /tmp/linkedin-comments
```

## Review UI

Start the local review UI against the opportunity SQLite state:

```sh
uv run linkedin-tools ui \
  --host 127.0.0.1 \
  --port 8787 \
  --opportunity-state-dir "$HOME/Library/Application Support/linkedin-tools/opportunity-intel"
```

The opportunity tabs read live SQLite rows for sources, post queue, extraction
runs, ranked comments, source summaries, calibration rows, and browser
artifacts. Labels persist as `strong`, `possible`, `weak`, `reject`, `needs
research`, or `ready for outreach`. Reject reasons persist as `recruiter`,
`agency`, `vendor`, `job seeker`, `not buyer`, `not relevant`, or `duplicate`.

## First Proof Gate

For the first source proof, collect at least 100 actual comment rows into
`provider-comments.csv`. Use the canonical columns from the exported template.

Prioritize the first 100 rows this way:

- Known high-signal posts first, because they already have concrete post URLs.
- Creator/operator audiences next, especially Valiotti, Bill Yost, Sebastian
  Hewing, Mike Rizzo, Darragh McKay, and Ali Rohde.
- Product/company pages and pain-language searches after that, favoring Retool,
  n8n, Zapier, Power Automate, Airtable, dashboard pain, spreadsheet operations,
  manual operations, and AI workflow productionization.

Run the experiment after the CSV has actual comments:

```sh
uv run linkedin-tools opportunity run-experiment \
  --comments-csv /tmp/linkedin-opportunity-v0/provider-comments.csv \
  --out-dir /tmp/linkedin-opportunity-v0/runs \
  --run-id v0-source-batch
```

Review these artifacts:

- `/tmp/linkedin-opportunity-v0/runs/v0-source-batch/source_gate.json`
- `/tmp/linkedin-opportunity-v0/runs/v0-source-batch/source_report.md`
- `/tmp/linkedin-opportunity-v0/runs/v0-source-batch/review_queue.csv`
- `/tmp/linkedin-opportunity-v0/runs/v0-source-batch/action_plan.md`

## Decision Rule

The initial source batch is promising if it produces at least 8 qualified warm
or hot direct-buyer comments per 100 valid comments and at least 60% of the
reviewable comments are from target buyer roles.

It is strong if it produces at least 15 qualified warm or hot direct-buyer
comments per 100 valid comments.

It should be replaced or sharply narrowed if it produces fewer than 3 qualified
warm or hot direct-buyer comments per 100 valid comments after 200 collected
comments, or if more than 25% of accepted rows are recruiters, agencies,
vendors, job seekers, or other non-buyers.
