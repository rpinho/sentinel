#!/usr/bin/env python3
"""CLI driver for freezing and running Sentinel's public holdout."""

from __future__ import annotations

import argparse
from pathlib import Path

from sentinel.holdout import freeze_holdout, run_holdout
from sentinel.types import parse_timestamp

REPOSITORIES = ("facebook/react", "vercel/next.js", "microsoft/vscode")
START = "2025-05-12T00:00:00Z"
END = "2025-05-19T00:00:00Z"
ACK_LOOKAHEAD_HOURS = 24
SELECTION_POLICY = (
    "First full Monday five weeks after the webpack demo; three predeclared "
    "high-traffic JavaScript repositories, selected before archive inspection."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze", help="Freeze code, config, and feedback.")
    freeze.add_argument(
        "--feedback-db", type=Path, default=Path(".cache/claude-validation.duckdb")
    )
    freeze.add_argument("--output-dir", type=Path, default=Path("holdout"))

    run = subparsers.add_parser("run", help="Run and publish the frozen holdout.")
    run.add_argument("--manifest", type=Path, default=Path("holdout/manifest.json"))

    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.command == "freeze":
        manifest = freeze_holdout(
            repo_root=repo_root,
            feedback_db=(repo_root / args.feedback_db).resolve(),
            output_dir=(repo_root / args.output_dir).resolve(),
            start=parse_timestamp(START),
            end=parse_timestamp(END),
            repositories=REPOSITORIES,
            ack_lookahead_hours=ACK_LOOKAHEAD_HOURS,
            selection_policy=SELECTION_POLICY,
        )
        print(f"FROZEN commit={manifest['frozen_git_commit']}")
        print(f"CONFIG sha256={manifest['detector_config_sha256']}")
        print(f"FEEDBACK sha256={manifest['feedback_snapshot_sha256']}")
        print(f"WINDOW {manifest['detection_start']}..{manifest['detection_end']}")
        print(f"REPOSITORIES {','.join(manifest['repositories'])}")
        return
    run_holdout(repo_root=repo_root, manifest_path=(repo_root / args.manifest).resolve())


if __name__ == "__main__":
    main()
