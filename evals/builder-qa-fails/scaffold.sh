#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/../_fixtures/workspace.sh"
builder_workspace '{"builder_agents": ["main"], "qa_commands": ["python3 qa.py"]}' fails
