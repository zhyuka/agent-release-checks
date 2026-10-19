#!/bin/sh
# Run one invigilator hook with the first Python 3.9 or later on this machine.
#
# hooks.json runs this on macOS, Linux and Windows with Git Bash as
#     sh "${CLAUDE_PLUGIN_ROOT}/hooks/run.sh" <hook>
# On Windows without Git Bash, hooks.json runs the same search in PowerShell.
#
# The hook input on stdin goes, unread, to each Python in turn. hooks/run.py
# exits 0 when it ran the hook and 3 when that Python is older than 3.9. Any
# other exit means that interpreter could not run it, such as the Microsoft
# Store's python3 alias, so the next one gets a try. With no Python that works,
# the hook lets the call through and warns once per session.

hook=$1
here=$(dirname "$0")

try_python() {
    command -v "$1" >/dev/null 2>&1 || return 0
    "$@" "$here/run.py" "$hook" && exit 0
}

if [ -n "$INVIGILATOR_PYTHON" ]; then
    try_python "$INVIGILATOR_PYTHON"
fi

case $(uname -s 2>/dev/null) in
    MINGW* | MSYS* | CYGWIN*)
        try_python py -3
        try_python python
        try_python python3
        ;;
    Darwin)
        # Without the Xcode command line tools, /usr/bin/python3 is a stub that
        # opens an install dialog instead of running.
        if [ "$(command -v python3)" != /usr/bin/python3 ] || /usr/bin/xcode-select -p >/dev/null 2>&1; then
            try_python python3
        fi
        try_python python
        ;;
    *)
        try_python python3
        try_python python
        ;;
esac

# No Python ran the hook. Say so once per session, and let the call through.
session=$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)
marker="${TMPDIR:-/tmp}/invigilator-no-python-${session:-unknown}"
if [ ! -e "$marker" ]; then
    : >"$marker" 2>/dev/null
    printf '%s\n' '{"systemMessage": "invigilator is off: it needs Python 3.9 or later, and none ran (tried python3, python and py -3). Install Python, or set INVIGILATOR_PYTHON to its path."}'
fi
exit 0
