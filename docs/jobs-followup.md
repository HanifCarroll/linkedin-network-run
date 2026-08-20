# Jobs follow-up handoff

`jobs followup-next` is a read-only packet after the HubSpot Day 1 task and completed Instantly handoff. It never calls HubSpot, writes CRM, schedules locally, or sends.

If Instantly recorded one work email/lead, the packet contains only one associated HubSpot monitoring task; Instantly owns cadence and `stop_on_reply`, so no duplicate email tasks are allowed. If Instantly recorded `noEmail`, it contains HubSpot tasks for days 5–7, 8–10, and 12–14. Each requires a fresh reply/connection check: DM only when connected, otherwise InMail. Any reply stops the sequence.

Perform lookup-before-create using the exact marker in `hs_task_body`, reuse the existing company, contact, and deal IDs, and associate every task to all three. Each task emits `hs_task_subject`, `hs_task_status=NOT_STARTED`, `hs_task_type` (`TODO` for email monitoring, `LINKED_IN_MESSAGE` for LinkedIn), `hubspot_owner_id=96636780`, and an ISO `hs_timestamp`. The Day 1 anchor is the later completed HubSpot/Instantly receipt; due offsets are day 5 for monitoring or days 5, 8, and 12 for LinkedIn.

Record the resulting task IDs with:

```sh
linkedin-tools --json jobs followup-record --prospect-id ID --payload - < receipt.json
```

The receipt payload must contain ordered `tasks`, each with `stage`, `taskId`, `associationsComplete: true`, and matching `associations` (`companyId`, `contactId`, `dealId`). Replays are no-ops; conflicting receipts block. Automatic `followup-next` skips completed receipts; `--id` may reopen one for inspection.
