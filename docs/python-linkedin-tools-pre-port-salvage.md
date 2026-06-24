# Python LinkedIn Tools Pre-Port Salvage Notes

## Status

Draft for Hanif review. This note preserves valuable decisions from the
previous Go/JavaScript worktree before cleanup. It is source material for the
Python port, not approval to keep the old implementation.

## Purpose

Before cleaning the worktree, preserve the reusable contracts, safety rules,
data seeds, and product decisions that should survive into `linkedin-tools`.
The Go/JavaScript code can be discarded or reverted after these requirements
are captured in the PRD and the relevant Python workstreams.

## Opportunity Intelligence

Preserve the source registry and query-pack concepts.

Current prior work contains:

- `configs/opportunity-sources.json` with 119 source hypotheses across LinkedIn
  searches, known post URLs, creator/operator audiences, company pages,
  complementary audiences, finance/tax manual-work sources, external seeds,
  higher-control comment-signal imports, and Sales Navigator active-ICP feeds.
- `configs/opportunity-comment-signal-queries.json` with six initial
  actual-comment query buckets:
  `first_party_automation_backlog`,
  `internal_tools_dashboard_pain`,
  `finance_tax_spreadsheet_workflows`,
  `product_engineering_build_need`,
  `automation_tool_migration_pain`, and
  `known_high_signal_post_engagement`.
- High initial caps: 500 searches/run, 500 posts/source, 2,000 comments/post,
  and 100,000 comments/run.

The Python port should keep the source registry and query pack as versioned
data, not hardcoded logic.

## Actual-Comment Evidence Contract

A row counts only when it contains actual LinkedIn comment text from the named
person.

Required proof fields:

- LinkedIn post URL.
- LinkedIn person profile URL for the commenter.
- Actual comment text.
- Commenter name.
- Source/query attribution.

Rejected as proof:

- Web-search snippets.
- Post text without comment text.
- Likes or reactions.
- Profile-only matches.
- Generic engagement.
- Inferred intent.

Keyword-comment provider rows must match the configured
`comment_text_patterns`; matching only the source post topic is not enough.

## Source Experiment Loop

Preserve this workflow shape:

1. Build a prioritized collection queue.
2. Prepare a batch workspace.
3. Collect or import actual comment rows.
4. Merge provider/manual/browser CSV chunks.
5. Validate required fields, query IDs, URL shapes, duplicate rows, and
   comment-pattern matches.
6. Convert valid rows into capture records.
7. Score comments.
8. Generate report, gate, review queue, calibration template, source decision,
   action plan, and run history.

The batch gate should start with these thresholds:

- Minimum valid comments: 100.
- Minimum warm/hot per 100: 3.
- Minimum warm/hot total: 20.
- Minimum direct-buyer rate: 8%.
- Maximum noise rate: 65%.
- Required evidence fields enabled.

Calibration should start with:

- At least 20 matched human labels overall.
- At least 5 labels before a source/query can pass individually.
- Precision at least 0.70.
- Recall at least 0.60.

The source decision logic should distinguish:

- Promote passing calibrated configuration.
- Label more before promotion.
- Tighten false positives.
- Recover false negatives.
- Collect more when warm signals exist below proof volume.
- Replace sources that reach volume with no signal.
- Use a higher-control actual-comment feed when browser/search inputs expose no
  trusted comment nodes.

## Provider And Manual Feed Interface

Preserve provider adapters as optional import interfaces, not as required
dependencies.

Provider candidates from the prior work:

- Octolens LinkedIn keyword mentions.
- Trigify or Clay-managed Trigify actual comment export.
- OutX watchlists and comment interactions.
- Apify post-comments API for exact post URLs.
- PhantomBuster post-commenters export for exact post URLs.
- BeReach comments finder or manual visible-comment capture.

The Python port should support the common CSV/import contract first. Native
provider adapters can be added behind that contract.

Canonical CSV columns:

```csv
query_id,source_id,source_kind,source_url,search_query,post_url,post_author_name,post_text,comment_id,comment_url,commenter_name,commenter_profile_url,commenter_headline,commenter_company,relationship,comment_text,commented_at
```

Common provider aliases should normalize into that shape.

## Comment Extraction Rules

The browser extractor should preserve exact LinkedIn comment text and warnings.

Known explicit LinkedIn comment paths from the prior script:

- Search-expanded comments:
  `[componentkey^="replaceableComment_urn:li:comment:"]`
- Direct post comments:
  `[data-id^="urn:li:comment:"]`

The public-post path can use JSON-LD for post/comment structure when available,
then explicit comment DOM records to fill missing URLs or URNs.

Do not infer comments from generic page text. If LinkedIn changes the DOM, add a
new explicit extraction path and warning.

Browser extraction should reuse an existing page/tab where possible to avoid tab
explosion.

## Buyer-Signal Ranking Rules

Keep the scorer focused on direct buyers who can buy Hanif's services.

High-priority need categories:

- Internal tools.
- AI workflow automation.
- Product engineering.
- Data dashboards and reporting.
- Spreadsheet-heavy operations.
- Prototype productionization.

Positive signals:

- Explicit help or recommendation ask.
- First-person workflow pain.
- First-party automation backlog.
- Timing or budget signal.
- Active evaluation signal.
- Relevant tool ecosystem pain.
- Direct-buyer headline.

Noise and rejection signals:

- Recruiters, staffing, talent, sourcers, and headhunters.
- Job seekers, students, and application comments.
- Vendors, consultants, agencies, freelancers, and self-promotional replies
  unless the comment itself contains a direct buyer ask.
- Post-author comments without a buyer ask.
- Generic commentary without first-party pain.

Levels should remain `hot`, `warm`, `watch`, and `reject`, with evidence quote,
fit reasons, reject reasons, and warnings preserved for review.

## Acceptance Follow-Up Review

The prior work added two useful requirements:

- Accepted-research browser runs may fail after writing a complete artifact. If
  the artifact declares `complete: true` and row counts match expected
  candidates, the draft renderer can continue from that artifact.
- Accepted follow-up drafts benefit from a local review UI with list filters,
  detail pages, draft editing, and limited status changes.

In the Python port, fold this into the shared FastAPI/Jinja/HTMX/Alpine review
UI rather than preserving a separate Go server.

## Recruiter/Agency Identity Safety

Preserve this safety rule:

Public LinkedIn `/in/...` profile URLs found from agency websites are review
context only. They are not enough to promote a website contact into a drafted
LinkedIn lead.

Promotion should require Sales Navigator identity resolution:

- Store `sales_profile_urn` on agency contact candidates.
- Convert the Sales Navigator profile URN into a Sales Navigator lead URL.
- Preserve the public profile URL as evidence/context.
- Surface unresolved contacts as `resolve_agency_contact_salesnav_identity`.

Also preserve:

- `contact_sales_profile_urn` as a CSV import column/alias.
- Canonicalization of public `/in/...` URLs by stripping tracking and
  recent-activity suffixes.
- `missing_linkedin_company_url` as a distinct agency account blocker before
  account-scoped Sales Navigator contact search.

## Browser Messaging Safety

Preserve these send-message safety rules in the Python browser layer:

- Message/InMail action labels must match the candidate name before clicking.
- Candidate-mismatched actions should produce a structured
  `message-action-candidate-mismatch` style result.
- Sales Navigator/profile pages may require opening the profile `More` menu to
  find Message/InMail.
- If Message/InMail opens a new browser page, the automation must follow that
  page and record the opened URL.

For connection requests:

- If the Sales Navigator connection API accepts the request but post-click page
  verification lands on a transient blocked page, treat the accepted network
  response as pending evidence and preserve the reason.

## Cleanup Recommendation

Before starting the Python port, keep:

- `docs/python-linkedin-tools-monorepo-prd.md`
- `docs/python-linkedin-tools-multi-thread-execution.md`
- `docs/python-linkedin-tools-pre-port-salvage.md`
- `AGENTS.md`

Then clear the previous Go/JavaScript implementation work from the active
worktree. The valuable behavior has been captured here for the Python port.
