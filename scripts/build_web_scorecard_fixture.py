#!/usr/bin/env python3
"""Build the compact, packaged scorecard view from frozen v1–v6 artifacts."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from sentinel.web.app import (
    PACKAGED_ARTIFACT_DIR,
    SCORECARD_BUNDLE_PATH,
    V3_SCORECARD_PATH,
    V4_SCORECARD_PATH,
    V5_SCORECARD_PATH,
    V6_SCORECARD_PATH,
    _canonical_sha256,
    build_scorecard_view_from_sources,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SCORECARD_BUNDLE_PATH)
    parser.add_argument(
        "--artifact-dir", type=Path, default=PACKAGED_ARTIFACT_DIR
    )
    args = parser.parse_args()

    view = build_scorecard_view_from_sources()
    document = {
        "schema_version": 1,
        "view": view,
        "view_sha256": _canonical_sha256(view),
    }
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _write_gzip(args.output, payload)
    sources = {
        "v3": V3_SCORECARD_PATH,
        "v4": V4_SCORECARD_PATH,
        "v5": V5_SCORECARD_PATH,
        "v6": V6_SCORECARD_PATH,
    }
    for version, source in sources.items():
        destination = args.artifact_dir / f"scorecard-{version}.json.gz"
        _write_gzip(destination, source.read_bytes())
    print(
        "WEB_SCORECARD "
        f"misses={len(view['v6_misses'])} "
        f"view_sha256={document['view_sha256']} output={args.output}"
    )


def _write_gzip(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as archive:
            archive.write(payload)


if __name__ == "__main__":
    main()
