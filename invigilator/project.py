"""A project's own checks: `invigilator check` with no folder, and `invigilator prove`.

A check is a command that exits 0 when what it inspects passes, and non-zero when it
doesn't. The project lists its checks in .claude/invigilator.json:

    {
      "checks": [
        {
          "name": "no-print",
          "run": "{python} checks/no_print.py {target}",
          "target": "src",
          "bad": "checks/cases/no-print/bad",
          "good": "checks/cases/no-print/good"
        }
      ]
    }

run is a command line, split into words as a POSIX shell splits them, or a list of words.
It runs from the project root without a shell, so a pipe or a redirect in it is just a word.
{target} becomes the path the check inspects, and {python} the Python that runs invigilator.
target defaults to ".". bad and good are each a path or a list of paths, to files or
folders: the planted cases the check has to fail and the known-good cases it has to pass.
timeout is in seconds and defaults to 900.

A check counts only with a proof: a record in .claude/invigilator-proofs.json that it
failed each bad case and passed each good one. `invigilator prove` runs the cases and
writes it. The record holds a fingerprint of the check, which covers its run, target and
case lists and every file in the project that run names, and one of each case's files, so a
change to any of them leaves the check unproven until it is proven again. `check --prove`
runs the cases again instead of trusting the record. CI should use it, since an agent can
write a record by hand.

A project can add checks, but it can't take one away or change one, as with the hook
settings in hooks.py:

- The user's own invigilator.json can list checks for a project, next to its qa_commands.
  Those always run, and a project check with the same name is left out.
- With --base REF, every check the project file had at REF has to stay. Leaving one out,
  or dropping or changing one of its cases, fails. Its command may change, but the new
  one has to be proven on the same cases. CI passes the branch a change would merge into.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__, hooks

PROOFS = hooks.PROOFS
FIELDS = ("name", "run", "target", "bad", "good", "timeout")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT = 900
OUTPUT_LINES = 20  # of a failing check's output, from the end
BLOCKED = ("If a check can't pass for a reason outside your change, such as a service this "
           "machine can't reach, stop and say you are blocked: name the check, give its output "
           "and say why. Don't edit the check, its cases, the files it runs, "
           f"{PROOFS.as_posix()} or {hooks.CONFIG.as_posix()} to get it to pass.")


class ConfigError(Exception):
    """The checks can't be read, or a git ref can't: exit code 2."""


@dataclass
class Check:
    name: str
    run: list  # the words, before {target} and {python} are filled in
    target: str
    bad: list
    good: list
    timeout: float
    source: str  # where it came from, for the report

    def cases(self):
        return [(path, "fail") for path in self.bad] + [(path, "pass") for path in self.good]


@dataclass
class Result:
    check: str
    status: str  # pass, fail, unproven, removed or error. Only pass passes.
    detail: str
    output: str = ""


# Reading the checks -----------------------------------------------------------

def parse(entries, where, source):
    """[Check] from a JSON list. Raises ConfigError naming the first problem."""
    if not isinstance(entries, list):
        raise ConfigError(f"{where}: \"checks\" must be a list")
    checks, seen = [], set()
    for entry in entries:
        check = _parse_one(entry, where, source)
        if check.name in seen:
            raise ConfigError(f"{where}: two checks are named {check.name!r}")
        seen.add(check.name)
        checks.append(check)
    return checks


def _parse_one(entry, where, source):
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: each check must be a JSON object")
    name = entry.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ConfigError(f"{where}: each check needs a \"name\" made of letters, digits, dots, "
                          "dashes and underscores")
    where = f"{where}, check {name!r}"
    unknown = sorted(set(entry) - set(FIELDS))
    if unknown:
        raise ConfigError(f"{where}: unknown field {unknown[0]!r}")
    run = entry.get("run")
    if isinstance(run, str):
        try:
            words = shlex.split(run)
        except ValueError as err:
            raise ConfigError(f"{where}: \"run\" can't be split into words ({err})") from None
    elif isinstance(run, list) and all(isinstance(word, str) for word in run):
        words = list(run)
    else:
        raise ConfigError(f"{where}: \"run\" must be a command line or a list of words")
    if not words:
        raise ConfigError(f"{where}: \"run\" is empty")
    if not any("{target}" in word for word in words):
        raise ConfigError(f"{where}: \"run\" has to name {{target}}, so prove can point the "
                          "check at its cases")
    target = entry.get("target", ".")
    if not isinstance(target, str) or not target:
        raise ConfigError(f"{where}: \"target\" must be a path")
    timeout = entry.get("timeout", TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError(f"{where}: \"timeout\" must be a number of seconds")
    return Check(name, words, target, _paths(entry.get("bad"), where, "bad", "fail"),
                 _paths(entry.get("good"), where, "good", "pass"), timeout, source)


def _paths(value, where, key, must):
    paths = [value] if isinstance(value, str) else value
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        raise ConfigError(f"{where}: \"{key}\" must name at least one file or folder the check "
                          f"has to {must}")
    return list(paths)


def find_root(start):
    """The nearest folder at or above start that has .claude/invigilator.json, else start."""
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / hooks.CONFIG).is_file():
            return folder
    return start


def project_checks(root):
    data, problem = hooks.project_file(root)
    if problem:
        raise ConfigError(problem)
    return parse(data.get("checks", []), hooks.CONFIG.as_posix(), "project")


def user_checks(root, environ):
    """(the checks the user's own file lists for this project, a note to show or "")."""
    entry, path, problem, broken = hooks.user_entry(root, environ)
    if broken:
        raise ConfigError(f"{problem}, so the checks it would require can't be known")
    return parse((entry or {}).get("checks", []), str(path), "yours"), problem


@dataclass
class Plan:
    """The checks that run, and what the floor rules found before any of them ran."""
    checks: list
    findings: list  # Result for each check the project took away or changed
    notes: list  # sentences to print before the table


def plan(root, environ=None, base=None):
    """The checks to run in root. The user's checks first, then the project's; with base, the
    checks the project file had at that git ref have to stay. Raises ConfigError."""
    environ = os.environ if environ is None else environ
    mine, note = user_checks(root, environ)
    notes = [note] if note else []
    names = {check.name: check for check in mine}
    checks = list(mine)
    for check in project_checks(root):
        if check.name not in names:
            names[check.name] = check
            checks.append(check)
        elif _definition(names[check.name], root) != _definition(check, root):
            notes.append(f"The project's check {check.name!r} differs from the one in your own "
                         "invigilator.json, so yours runs")
    findings = []
    for old in base_checks(root, base) if base else []:
        now = names.get(old.name)
        if now is None:
            findings.append(Result(old.name, "removed",
                                   f"{hooks.CONFIG.as_posix()} at {base} has this check and the "
                                   "project's doesn't now. A change can add checks, not remove them"))
            continue
        if now.source == "yours":
            continue  # the user's own definition runs; the base can't weaken or replace it
        problem = _kept_cases(old, now, root, base)
        if problem:  # a changed command is fine: it is proven on the cases kept here
            findings.append(Result(old.name, "removed", problem))
            checks.remove(now)
    return Plan(checks, findings, notes)


def base_checks(root, ref):
    """The checks the project file had at a git ref. [] when it had none."""
    if not _git_ok(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
        raise ConfigError(f"git can't find the commit {ref}. In CI, check out the full history, "
                          "so the base branch is there")
    spec = f"{ref}:./{hooks.CONFIG.as_posix()}"  # ./ makes the path start at the project root
    if not _git_ok(root, "cat-file", "-e", spec):
        return []
    text = _git(root, "show", spec)
    where = f"{hooks.CONFIG.as_posix()} at {ref}"
    try:
        data = json.loads(text)
    except ValueError as err:
        raise ConfigError(f"{where} could not be read ({err})") from None
    return parse(data.get("checks", []) if isinstance(data, dict) else None, where, f"at {ref}")


def _kept_cases(old, now, root, ref):
    """Why now drops or changes one of old's cases, or ""."""
    for path, must in old.cases():
        if path not in (now.bad if must == "fail" else now.good):
            kind = "bad" if must == "fail" else "good"
            return (f"it no longer lists the {kind} case {path}, which it had at {ref}. A change can "
                    "add cases, not drop them")
        if _blobs_at(root, ref, path) != _blobs_now(root, path):
            return f"its case {path} differs from the one at {ref}. A change can add cases, not edit them"
    return ""


def _run_git(root, args):
    try:
        return subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=False)
    except OSError as err:
        raise ConfigError(f"git could not run ({err}), and --base needs it") from None


def _git_ok(root, *args):
    return _run_git(root, args).returncode == 0


def _git(root, *args):
    """git's output as text. Raises ConfigError when git fails."""
    done = _run_git(root, args)
    if done.returncode:
        message = done.stderr.decode("utf-8", "replace").strip()
        last = message.splitlines()[-1].rstrip(".") if message else f"exit {done.returncode}"
        raise ConfigError(f"git {args[0]} failed ({last})")
    return done.stdout.decode("utf-8", "replace")


def _blobs_at(root, ref, path):
    """{path from the root: git blob id} for the files under path at ref. git lists paths
    from the folder it runs in, which is the project root."""
    out = _git(root, "ls-tree", "-r", "-z", ref, "--", Path(path).as_posix())
    blobs = {}
    for record in filter(None, out.split("\0")):
        meta, name = record.split("\t", 1)
        blobs[name] = meta.split()[2]
    return blobs


def _blobs_now(root, path):
    """The same for the working tree, hashed the way git add would."""
    files = [Path(os.path.relpath(p, root)).as_posix() for p in _files(Path(root) / path)]
    if not files:
        return {}
    done = subprocess.run(["git", "hash-object", "--stdin-paths"], cwd=str(root), check=False,
                          input="\n".join(files).encode("utf-8"), capture_output=True)
    if done.returncode:
        raise ConfigError(f"git hash-object failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return dict(zip(files, done.stdout.decode().split()))


# Fingerprints and proof records ----------------------------------------------

def _files(path):
    """The files at path, a file or a folder, sorted. Links are followed; .git is skipped."""
    path = Path(path)
    if path.is_file():
        return [path]
    found = []
    for folder, subfolders, names in os.walk(path):
        subfolders[:] = sorted(d for d in subfolders if d != ".git")
        found.extend(Path(folder) / name for name in sorted(names))
    return found


def _digest(paths, root, extra=b""):
    """sha256 over the files' paths from root and their bytes, with CRLF read as LF so a
    Windows checkout fingerprints the same."""
    sha = hashlib.sha256(extra)
    for path in paths:
        sha.update(Path(os.path.relpath(path, root)).as_posix().encode("utf-8") + b"\0")
        sha.update(Path(path).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return sha.hexdigest()


def case_digest(root, path):
    """The fingerprint of one case, or "" when it doesn't exist."""
    root = Path(root).resolve()
    files = _files(root / path)
    return _digest(files, root) if files else ""


def named_files(check, root):
    """The files in the project that the check's words name, such as checks/no_print.py."""
    root = Path(root).resolve()
    found = []
    for word in check.run:
        if "{" in word:
            continue
        path = (root / word).resolve()
        if path.is_file() and (root in path.parents) and path not in found:
            found.append(path)
    return found


def _definition(check, root):
    spec = json.dumps({"run": check.run, "target": check.target, "bad": check.bad,
                       "good": check.good, "timeout": check.timeout}, sort_keys=True)
    return _digest(named_files(check, root), Path(root).resolve(), spec.encode("utf-8"))


def read_proofs(root):
    path = Path(root) / PROOFS
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as err:
        raise ConfigError(f"{PROOFS.as_posix()} could not be read ({err})") from None
    proofs = data.get("proofs") if isinstance(data, dict) else None
    if not isinstance(proofs, dict):
        raise ConfigError(f"{PROOFS.as_posix()} must hold {{\"proofs\": {{...}}}}")
    return proofs


def record(check, root, runs):
    """The proof record for check, from the runs of its cases: [(path, must, Run)]."""
    return {"check": _definition(check, root),
            "cases": [{"path": path, "must": must, "sha256": case_digest(root, path), "exit": run.code}
                      for path, must, run in runs],
            "proven_with": f"invigilator {__version__}"}


def stale(check, root, proof):
    """Why the stored proof doesn't cover the check as it is now, or ""."""
    if not isinstance(proof, dict):
        return f"no proof. Run invigilator prove {check.name}"
    if proof.get("check") != _definition(check, root):
        return (f"the check changed since it was proven. Run invigilator prove {check.name}")
    stored = {(c.get("path"), c.get("must")): c for c in proof.get("cases") or [] if isinstance(c, dict)}
    for path, must in check.cases():
        case = stored.get((path, must))
        if case is None:
            return f"the case {path} has no proof. Run invigilator prove {check.name}"
        if case.get("sha256") != case_digest(root, path):
            return f"the case {path} changed since it was proven. Run invigilator prove {check.name}"
    return ""


def write_proofs(root, updates, drop=()):
    """Write the records in updates over the stored ones, and remove those named in drop.
    Records for other checks are kept. Written to a temp file and moved into place."""
    path = Path(root) / PROOFS
    proofs = {**read_proofs(root), **updates}
    for name in drop:
        proofs.pop(name, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"proofs": dict(sorted(proofs.items()))}, indent=2) + "\n"
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


# Running ---------------------------------------------------------------------

@dataclass
class Run:
    code: object  # the exit code, or None when the command didn't start or timed out
    output: str
    problem: str = ""


def run_check(check, root, target):
    """Run check with {target} set to target, from root."""
    words = [w.replace("{target}", target).replace("{python}", sys.executable) for w in check.run]
    program = words[0]
    if os.sep in program or (os.altsep and os.altsep in program):
        found = str((Path(root) / program)) if not os.path.isabs(program) else program
        found = found if os.path.isfile(found) else None
    else:
        found = shutil.which(program)  # finds npm.cmd for npm on Windows
    if not found:
        return Run(None, "", f"{program} was not found")
    try:
        done = subprocess.run([found, *words[1:]], cwd=str(root), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=check.timeout)
    except subprocess.TimeoutExpired as err:
        return Run(None, _text(err.stdout), f"it timed out after {check.timeout:g} s")
    except OSError as err:
        return Run(None, "", f"it could not start ({err})")
    return Run(done.returncode, _text(done.stdout))


def _text(data):
    if not data:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _tail(text, lines=OUTPUT_LINES):
    return "\n".join(text.rstrip().splitlines()[-lines:])


def prove_one(check, root):
    """(ok, runs, problems). Each bad case has to make the check exit non-zero and each good
    case exit 0. A case where the check didn't start or timed out proves nothing."""
    runs, problems = [], []
    for path, must in check.cases():
        if not (Path(root) / path).exists():
            problems.append(f"the case {path} doesn't exist")
            continue
        run = run_check(check, root, path)
        runs.append((path, must, run))
        if run.code is None:
            problems.append(f"on {path} {run.problem}")
        elif must == "fail" and run.code == 0:
            problems.append(f"it passed the bad case {path}")
        elif must == "pass" and run.code != 0:
            problems.append(f"it failed the good case {path} (exit {run.code})")
    return not problems, runs, problems


def counts(runs):
    """(bad caught, bad, good passed, good)."""
    bad = [r for _, must, r in runs if must == "fail"]
    good = [r for _, must, r in runs if must == "pass"]
    return (sum(r.code not in (None, 0) for r in bad), len(bad),
            sum(r.code == 0 for r in good), len(good))


def check_project(root, environ=None, base=None, reprove=False):
    """(results, notes). Raises ConfigError."""
    root = Path(root).resolve()
    work = plan(root, environ, base)
    proofs = read_proofs(root)
    results = list(work.findings)
    for check in work.checks:
        if reprove:
            ok, runs, problems = prove_one(check, root)
            stored = stale(check, root, proofs.get(check.name))
            if not ok:
                results.append(Result(check.name, "unproven", "the proof didn't hold: " + "; ".join(problems)))
                continue
            if stored:
                results.append(Result(check.name, "unproven", f"its cases hold, but its record is stale: {stored}"))
                continue
        else:
            why = stale(check, root, proofs.get(check.name))
            if why:
                results.append(Result(check.name, "unproven", why))
                continue
        run = run_check(check, root, check.target)
        if run.code is None:
            results.append(Result(check.name, "error", run.problem, _tail(run.output)))
        elif run.code:
            results.append(Result(check.name, "fail", f"exit {run.code} on {check.target}", _tail(run.output)))
        else:
            results.append(Result(check.name, "pass", f"exit 0 on {check.target}"))
    return results, work.notes


def prove_project(root, names=(), environ=None, base=None):
    """(rows, notes, ok): one row per check proven, [name, bad caught, good passed, result].
    Writes the records of the checks that held and drops those of the ones that didn't."""
    root = Path(root).resolve()
    work = plan(root, environ, base)
    known = {check.name for check in work.checks} | {r.check for r in work.findings}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ConfigError(f"there is no check named {unknown[0]!r}")
    rows, updates, drop, ok = [], {}, [], True
    for finding in work.findings:
        if not names or finding.check in names:
            rows.append([finding.check, "-", "-", f"not proven: {finding.detail}"])
            ok = False
    for check in work.checks:
        if names and check.name not in names:
            continue
        held, runs, problems = prove_one(check, root)
        caught, bad, passed, good = counts(runs)
        rows.append([check.name, f"{caught} of {bad}", f"{passed} of {good}",
                     "proven" if held else "not proven: " + "; ".join(problems)])
        if held:
            updates[check.name] = record(check, root, runs)
        else:
            drop.append(check.name)
            ok = False
    if updates or drop:
        write_proofs(root, updates, drop)
    return rows, work.notes, ok
