"""CLI namespace for the local review UI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_ui import LocalAccessToken

from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linkedin-tools-ui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--access-token")
    parser.add_argument("--opportunity-state-dir", type=Path, default=None)
    parser.add_argument("--network-state-dir", type=Path, default=None)
    parser.add_argument("--recruiter-state-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="info")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = args.access_token or LocalAccessToken.generate().token
    app = create_app(
        access_token=token,
        opportunity_store=OpportunityStore(args.opportunity_state_dir),
        network_state_dir=args.network_state_dir,
        recruiter_state_dir=args.recruiter_state_dir,
    )
    print(f"Review UI: http://{args.host}:{args.port}/?access_token={token}")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
