# `jobs instantly-next` / `jobs instantly-record`

These commands are a read-only handoff to an authorized agent. They never call Instantly, send email, store credentials, or schedule follow-ups.

```sh
linkedin-tools --json jobs instantly-next --campaign-id CAMPAIGN_ID [--id JOB_ID] [--state-dir ABSOLUTE_PATH]
linkedin-tools --json jobs instantly-record --prospect-id ID --payload -|ABSOLUTE_PATH
```

`instantly-next` selects an approved, kept Jobs prospect with a complete local HubSpot receipt and an explicit campaign ID. The packet first calls `GET /api/v2/campaigns/{id}` and blocks unless `stop_on_reply` is true. It then uses documented SuperSearch fields (`name`, `company_name.include`, `limit`, `work_email_enrichment`); that response is an asynchronous enrichment/list receipt, not an email result. The agent then calls `POST /api/v2/leads` with the recorded work email and campaign, recording the returned lead ID. Local follow-up scheduling is intentionally absent.

The receipt payload supports an async enrichment receipt, then a final enrollment receipt, or a terminal no-email/error outcome:

```json
{"enrichmentId":"..."}
{"email":"person@example.com","leadId":"...","campaignStopOnReply":true}
{"noEmail":true}
{"error":"..."}
```

An ambiguous or multiple-email result is rejected. A work-email receipt is complete only after a lead ID and `campaignStopOnReply:true` are recorded; no-email is a terminal, non-enrolled outcome. Receipts store explicit `campaignId`, `leadId`, `enrichmentId`, and `campaignStopOnReply`. Receipts are durable in SQLite and are safe to retry only with the same outcome.
