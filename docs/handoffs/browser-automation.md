# Browser Automation Handoff

## Goal

Build the Python Playwright browser/session layer and shared Sales Navigator browser primitives for app workstreams.

## Owned Paths Changed

- `packages/linkedin_browser/`
- `packages/linkedin_salesnav/`
- `tests/fixtures/browser/`
- `tests/test_browser_layer.py`
- `tests/test_salesnav_primitives.py`
- `docs/handoffs/browser-automation.md`

## Commands Implemented

No user-facing CLI commands were added in this thread. The implemented public package APIs are callable by app threads:

- Chrome profile config defaults to the existing Chrome profile named `LinkedIn`.
- Persistent Chrome launch helper for Python Playwright.
- Reusable page/session helper that prefers existing LinkedIn/Sales Navigator pages and can close surplus pages when explicitly requested.
- JSON and screenshot artifact writer.
- Browser state classifier for rate limit, login, checkpoint, security, restriction, blocked, and network-error evidence.
- Guarded click helper for real sends, messages, and withdrawals.
- Sales Navigator capture and audit artifact parsers.
- Sales Navigator guarded connection, message, and withdrawal action wrappers.

## Data Models Introduced

- `ChromeProfileConfig`
- `ArtifactRef`
- `BrowserStateEvidence`
- `BrowserStateClassification`
- `RealActionApproval`
- `GuardedActionResult`
- `CandidateIdentity`
- `SalesNavCaptureArtifact`
- `SalesNavCaptureRow`
- `AuditArtifact`
- `MessageActionCandidate`
- `MessageActionSafetyResult`

## Browser Assumptions

- Default Chrome user data directory is `~/Library/Application Support/Google/Chrome`.
- Default Chrome profile name is `LinkedIn`.
- Profile path can be changed with `LINKEDIN_TOOLS_CHROME_USER_DATA_DIR`.
- Profile name can be changed with `LINKEDIN_TOOLS_CHROME_PROFILE_NAME`.
- Browser channel can be changed with `LINKEDIN_TOOLS_BROWSER_CHANNEL`; use
  `bundled` for Playwright's Chromium instead of installed Google Chrome.
- Headless mode can be changed with `LINKEDIN_TOOLS_BROWSER_HEADLESS`.
- Installed Google Chrome launches inherit a minimal Chrome-safe environment
  from `chrome_launch_env()` so local dev-shell variables do not trip Chrome's
  hardened runtime.
- Browser sessions should reuse an existing LinkedIn/Sales Navigator page when possible to avoid tab growth.
- Closing extra pages is explicit, not automatic, so app threads do not unexpectedly close unrelated browser state.

## Safety Boundaries

- Browser operations default to dry-run.
- `RealAction.SEND_CONNECTION`, `RealAction.SEND_MESSAGE`, and `RealAction.WITHDRAW_INVITATION` require matching `RealActionApproval` when `dry_run=False`.
- Message/InMail actions require an identity label matching the candidate before the click helper will proceed.
- Candidate-mismatched message actions return `message-action-candidate-mismatch`.
- Profile More-menu message paths must record `opened_page_url`.
- The browser state classifier uses structured evidence and URL/status checks, not generic page text scanning.

## Tests Added

- Browser profile config defaults and overrides.
- Page reuse and explicit surplus-page closing.
- Guarded click dry-run and approval enforcement.
- Browser blocked-state classification.
- JSON and screenshot artifact writing.
- Sales Navigator capture/audit fixture parsing.
- Sales profile URL/URN conversion.
- Message action identity mismatch.
- Profile More-menu opened URL requirement.
- Sales Navigator guarded action approval enforcement.

## Verification Run

Passed in this thread:

- `uv run pytest tests/test_browser_layer.py tests/test_salesnav_primitives.py`
- `uv run ruff check packages/linkedin_browser packages/linkedin_salesnav tests/test_browser_layer.py tests/test_salesnav_primitives.py`
- `uv run mypy packages/linkedin_browser packages/linkedin_salesnav tests/test_browser_layer.py tests/test_salesnav_primitives.py`
- `uv run pytest`
- `rg -n "slice\\(|substring\\(|substr\\(|visibleText|innerText|document\\.title|legacy|fallback|infer|keyword|score|\\[class\\*=|h1|h2|h3|article|raw_text" packages/linkedin_browser packages/linkedin_salesnav tests/test_browser_layer.py tests/test_salesnav_primitives.py docs/handoffs/browser-automation.md`

## Known Gaps

- Live Playwright flows are not exercised here; this thread only added dry-run and fixture-backed coverage.
- Capture and audit parsers preserve current artifact contracts, but full DOM extraction ports should be completed by the app threads or a follow-up browser thread against live dry-runs.
- Connection send, pending withdrawal, and message composition still need app-specific controller integration.

## Integration Dependencies

- Network automation should call `guarded_connection_request` and `guarded_withdraw_invitation` instead of clicking directly.
- Recruiter/agency and accepted-follow-up send paths should call `guarded_message_click`.
- App threads should write ambiguous browser outcomes through `ArtifactWriter`.
- Review UI can read browser artifact paths and classifier output directly.

## Decisions Needing Orchestrator Approval

- Whether `RealActionApproval` should carry a human-readable approval nonce or command flag provenance before final CLI integration.
- Whether page surplus closing should remain opt-in for all app threads.
- Whether the message identity rule should require a candidate match in the button label itself or allow a matched row/profile identity label as implemented here.
