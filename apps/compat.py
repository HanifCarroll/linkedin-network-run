"""Temporary compatibility command entrypoints during migration."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def _compat_placeholder(command_name: str, argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        f"{command_name} compatibility shim is scaffolded; "
        "migration workstream owns behavior parity."
    )
    return 0


def linkedin_network_run(argv: Sequence[str] | None = None) -> int:
    return _compat_placeholder("linkedin-network-run", argv or sys.argv[1:])


def recruiter_agency_outreach(argv: Sequence[str] | None = None) -> int:
    return _compat_placeholder("recruiter-agency-outreach", argv or sys.argv[1:])


def linkedin_opportunity_intel(argv: Sequence[str] | None = None) -> int:
    return _compat_placeholder("linkedin-opportunity-intel", argv or sys.argv[1:])
