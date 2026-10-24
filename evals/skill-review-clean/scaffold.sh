#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "$0")/../_fixtures/skills.py" review-clean
