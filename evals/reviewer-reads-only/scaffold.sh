#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/../_fixtures/workspace.sh"
git_review_workspace '{"reviewer_agents": ["main"]}'
