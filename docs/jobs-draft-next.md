# `jobs draft-next`

Companion-facing, read-only handoff:

```sh
linkedin-tools --json jobs draft-next [--id JOB_ID] [--state-dir ABSOLUTE_PATH]
```

The command returns one deterministic packet for one person and one primary role. The role must
be kept, triaged, approved, not sent, have a usable first hiring-team LinkedIn profile, and have no stored
draft in its recipient group. Application-followup/contract roles also require `jobs applied`.
Unapplied contract roles are skipped and counted in `blockedApplications`.

The packet contains the route, stored person/job/company evidence, and concise writing instructions.
The companion writes two distinct pieces of copy: a Day 1 LinkedIn connection note of at most 200
characters, plus the fuller follow-up message and optional InMail subject. The two texts must differ.
The packet exposes the approved `connectionNoteTemplate` so companions do not invent another shape.
It does not call an LLM, write a draft, approve, send, use a browser, or access HubSpot. The
companion writes both texts, then invokes `jobs draft`; that existing command stores them and
returns review to `needs_review`.
