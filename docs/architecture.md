# Python LinkedIn Tools Architecture

This repo now has an integrated Python `linkedin-tools` monorepo beside the
existing Go and Playwriter implementation. The Go/JavaScript code remains
parity evidence until Hanif approves cutover.

Source-of-truth planning docs:

- `docs/python-linkedin-tools-monorepo-prd.md`
- `docs/python-linkedin-tools-multi-thread-execution.md`
- `docs/python-linkedin-tools-pre-port-salvage.md`

The current implementation branch is organized around:

- `apps/`: user-facing app namespaces and CLI surfaces.
- `packages/`: shared browser, storage, reporting, UI, and experiment packages.
- `tests/`: unit and integration tests for the Python port.
- `docs/handoffs/`: required subthread handoff notes.

Subthreads must keep to their owned paths unless the orchestrator explicitly
changes the assignment.
