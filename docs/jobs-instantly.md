# `jobs instantly-next` / `jobs instantly-record`

These commands are a read-only handoff to an authorized agent. They never call Instantly, send email, store credentials, or schedule follow-ups.

```sh
linkedin-tools --json jobs instantly-next [--id JOB_ID] [--state-dir ABSOLUTE_PATH]
linkedin-tools --json jobs instantly-record --prospect-id ID --payload -|ABSOLUTE_PATH
```

`instantly-next` selects an approved, kept Jobs prospect with a complete local HubSpot receipt and emits the official Instantly API v2 packet. The packet uses SuperSearch work-email enrichment, then campaign enrollment, and asks the agent to verify the selected campaign's existing cadence and `stop_on_reply` setting. Local follow-up scheduling is intentionally absent.

The receipt payload must contain exactly one of:

```json
{"email":"person@example.com","campaignEnrollmentId":"...","stopReplyStatus":"active"}
{"noEmail":true}
{"error":"..."}
```

An ambiguous or multiple-email result is rejected. A work-email receipt is complete only after enrollment and stop/reply status are recorded; no-email is a terminal, non-enrolled outcome. Receipts are durable in SQLite and are safe to retry only with the same outcome.
