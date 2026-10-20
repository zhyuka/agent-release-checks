# Run one invigilator hook on Windows without Git Bash:
#     powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File run.ps1 <hook>
#
# hooks.json runs this from the second line of each hook command, which PowerShell
# reaches after the first line, exec sh, fails. It tries each Python in turn, as
# hooks/run.sh does: INVIGILATOR_PYTHON, then py -3, python and python3.
#
# The hook input on stdin goes, unread, to each Python in turn. Never name
# PowerShell's automatic pipeline-input variable in this file: a script that names
# it reads all of stdin before it starts, and every Python then gets an empty input.
# hooks/run.py exits 0 when it ran the hook and 3 when that Python is older than
# 3.9. Any other exit means that interpreter could not run it, such as the
# Microsoft Store's python3 alias, so the next one gets a try. With no Python that
# works, the hook lets the call through and warns once per session.
param([string]$Hook)

$run = Join-Path $PSScriptRoot 'run.py'
$candidates = @()
# INVIGILATOR_PYTHON counts only outside the project, so a repository can't choose the
# program that runs its own checks.
$inside = $false
if ($env:INVIGILATOR_PYTHON) {
    $found = Get-Command $env:INVIGILATOR_PYTHON -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $project = $env:CLAUDE_PROJECT_DIR
        if (-not $project) { $project = (Get-Location).ProviderPath }
        $root = [System.IO.Path]::GetFullPath($project).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
        $inside = [System.IO.Path]::GetFullPath($found.Source).StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
        if (-not $inside) { $candidates += ,@($env:INVIGILATOR_PYTHON) }
    }
}
$candidates += ,@('py', '-3')
$candidates += ,@('python')
$candidates += ,@('python3')

foreach ($candidate in $candidates) {
    $program = $candidate[0]
    $rest = @($candidate | Select-Object -Skip 1)
    if (Get-Command $program -CommandType Application -ErrorAction SilentlyContinue) {
        & $program @rest $run $Hook
        if ($LASTEXITCODE -eq 0) { exit 0 }
    }
}

# No Python ran the hook. Say so once per session, and let the call through.
$bytes = New-Object System.IO.MemoryStream
[Console]::OpenStandardInput().CopyTo($bytes)
$session = ''
try {
    $parsed = [System.Text.Encoding]::UTF8.GetString($bytes.ToArray()) | ConvertFrom-Json
    $session = $parsed.session_id -replace '[^A-Za-z0-9_-]', ''
} catch { }
$folder = $env:TEMP
if (-not $folder) { $folder = '/tmp' }
$marker = Join-Path $folder "invigilator-no-python-$session"
if (-not (Test-Path -LiteralPath $marker)) {
    New-Item -ItemType File -Path $marker -Force | Out-Null
    if ($inside) {
        '{"systemMessage": "invigilator is off: it needs Python 3.9 or later, and none ran (tried py -3, python and python3). INVIGILATOR_PYTHON is inside the project, so it was skipped. Install Python, or set INVIGILATOR_PYTHON to one outside the project."}'
    } else {
        '{"systemMessage": "invigilator is off: it needs Python 3.9 or later, and none ran (tried py -3, python and python3). Install Python, or set INVIGILATOR_PYTHON to its path."}'
    }
}
exit 0
