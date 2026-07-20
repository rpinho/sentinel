#!/usr/bin/env bash
set -euo pipefail

sentinel_db="${1:-.cache/webpack-acceptance.duckdb}"
archive_cache="${2:-.cache/gharchive}"

uv run sentinel ingest gharchive \
  --repo webpack/webpack \
  --start 2025-04-07T12:00:00Z \
  --end 2025-04-07T23:00:00Z \
  --db "$sentinel_db" \
  --cache-dir "$archive_cache"

uv run sentinel ingest npm \
  --package webpack \
  --corroborate-repo webpack/webpack \
  --start 2025-04-07 \
  --end 2025-04-09 \
  --db "$sentinel_db"

uv run sentinel detect \
  --entity webpack/webpack \
  --start 2025-04-07T12:00:00Z \
  --end 2025-04-07T23:00:00Z \
  --db "$sentinel_db" \
  --no-agent \
  --json

uv run python scripts/verify_webpack_acceptance.py --db "$sentinel_db"
