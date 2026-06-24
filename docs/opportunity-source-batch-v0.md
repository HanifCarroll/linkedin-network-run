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
uv run linkedin-tools opportunity post-queue --out /tmp/linkedin-opportunity-v0/post-queue.csv
uv run linkedin-tools opportunity provider-export-csv --out /tmp/linkedin-opportunity-v0/provider-comments.csv
```

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

Example company-page conversion:

```sh
uv run linkedin-tools opportunity company-post-capture \
  --source-id product_retool \
  --html /tmp/linkedin-opportunity-v0/company-pages/product_retool.html \
  --out /tmp/linkedin-opportunity-v0/company-pages/product_retool-post-queue.csv
```

The output uses the same post queue columns as `post-queue.csv`, with concrete
`post_url` values ready for visible-comment capture.

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
