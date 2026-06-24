# Python LinkedIn Tools Cutover Checklist

Do not archive the current Go/JavaScript implementation until all items pass.

- [ ] Every current CLI command has a Python equivalent.
- [ ] Every current browser script has a Python Playwright equivalent or an
      approved consolidated replacement.
- [x] Old state importers preserve data and do not mutate old state.
- [x] Compatibility commands work during migration.
- [x] Local UI exposes all required PRD views.
- [x] Send and withdraw safety tests pass.
- [ ] Browser dry-runs pass.
- [x] Opportunity-intel remains recommend-only.
- [ ] Hanif approves cutover.
