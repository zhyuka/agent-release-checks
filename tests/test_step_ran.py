import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from release_checks.runlog import ToolCall
from release_checks.step_ran import (REPORT, build_sha256, cheap_agent, qa_agent, qa_in_command,
                                     qa_ran, trial, verify)

GOOD = Path(__file__).resolve().parent.parent / "examples" / "builds" / "good"
QA = ["npm run check"]


def write(path="build/index.html"):
    return ToolCall("Write", {"file_path": path})


def bash(command, ok=True, **extra):
    return ToolCall("Bash", {"command": command, **extra}, ok)


class StepRanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.build, self.artifacts = Path(tmp.name, "build"), Path(tmp.name, "artifacts")
        shutil.copytree(GOOD, self.build)
        self.artifacts.mkdir()
        self.report = self.artifacts / REPORT

    def write_report(self, **fields):
        report = {"build_sha256": build_sha256(self.build), "passed": True, **fields}
        self.report.write_text(json.dumps(report))

    def test_cheap_agent_is_caught_in_every_run(self):
        results = trial(cheap_agent, GOOD, 33)
        self.assertEqual(sum(claim == "QA passed" for claim, _ in results), 33)
        self.assertEqual(sum(v.accepted for _, v in results), 0)

    def test_qa_agent_is_accepted_in_every_run(self):
        self.assertEqual(sum(v.accepted for _, v in trial(qa_agent, GOOD, 33)), 33)

    def test_report_for_another_build_is_rejected(self):
        self.write_report(build_sha256="0" * 64)
        self.assertEqual(verify("QA passed", self.build, self.artifacts).reason,
                         "report names a different build")

    def test_report_older_than_build_is_rejected(self):
        self.write_report()
        old = (self.build / "index.html").stat().st_mtime_ns - 10**9
        os.utime(self.report, ns=(old, old))
        self.assertEqual(verify("QA passed", self.build, self.artifacts).reason,
                         "report is older than the build")

    def test_report_that_did_not_pass_is_rejected(self):
        self.write_report(passed=False)
        self.assertFalse(verify("QA passed", self.build, self.artifacts).accepted)

    def test_rebuild_voids_an_earlier_report(self):
        self.assertEqual(qa_agent(self.build, self.artifacts), "QA passed")
        self.assertTrue(verify("QA passed", self.build, self.artifacts).accepted)
        (self.build / "app.js").write_text("// changed after QA\n")
        self.assertFalse(verify("QA passed", self.build, self.artifacts).accepted)


class QaRanTest(unittest.TestCase):
    def test_qa_after_the_last_write_is_accepted(self):
        verdict = qa_ran([write(), bash("npm run check")], QA)
        self.assertEqual(verdict.reason, "QA command passed after the last file write")
        self.assertTrue(verdict.accepted)

    def test_no_qa_is_rejected(self):
        self.assertEqual(qa_ran([write()], QA).reason, "QA command 'npm run check' never ran")

    def test_qa_before_the_last_write_is_rejected(self):
        verdict = qa_ran([write(), bash("npm run check"), write("build/app.js")], QA)
        self.assertIn("before the last file write", verdict.reason)

    def test_failed_qa_is_rejected(self):
        self.assertFalse(qa_ran([write(), bash("npm run check", ok=False)], QA).accepted)

    def test_a_later_pass_counts_after_a_failure(self):
        calls = [write(), bash("npm run check", ok=False), bash("npm run check")]
        self.assertTrue(qa_ran(calls, QA).accepted)

    def test_masked_exit_codes_do_not_count(self):
        for command in ("npm run check | tail -5", "npm run check || true", "npm run check; echo done",
                        "npm run check &"):
            verdict = qa_ran([write(), bash(command)], QA)
            self.assertFalse(verdict.accepted, command)
        self.assertIn("exit code was not seen", qa_ran([write(), bash("npm run check | tail")], QA).reason)

    def test_unknown_result_does_not_count(self):
        verdict = qa_ran([write(), bash("npm run check", ok=None, run_in_background=True)], QA)
        self.assertEqual(verdict.reason, "QA command ran but its result was not recorded")

    def test_only_the_command_itself_counts(self):
        self.assertEqual(qa_in_command("echo npm run check", QA), "")
        self.assertEqual(qa_in_command("grep 'npm run check' notes.md", QA), "")
        self.assertEqual(qa_in_command("CI=1 npm run check -- --fast", QA), "seen")
        self.assertEqual(qa_in_command("npm run build && npm run check", QA), "seen")
        self.assertEqual(qa_in_command("npm run check 2>&1 > qa.log", QA), "seen")

    def test_a_qa_command_is_not_a_write(self):
        self.assertTrue(qa_ran([write(), bash("npm run check > qa.log")], QA).accepted)

    def test_the_command_is_read_the_way_the_shell_reads_it(self):
        # A # inside a word is not a comment, and a group's exit code is its last command's.
        for command, status in (("cd site#2 && npm run check", "seen"), ("echo a#b; npm run check", "seen"),
                                ("npm run check -- --grep=#12", "seen"), ("npm run check # all of it", "seen"),
                                ("# npm run check", ""), ("echo 'npm run check' # npm run check", ""),
                                ("(cd site && npm run check)", "seen"), ("(npm run check) &", "masked"),
                                ("(npm run check) | tail", "masked"), ("echo `npm run check`", "masked"),
                                ("echo \"$(npm run check)\"", "masked"), ("bash -c 'npm run check'", ""),
                                ("npm run check <<'EOF'\nignored\nEOF", "seen")):
            self.assertEqual(qa_in_command(command, QA), status, command)


if __name__ == "__main__":
    unittest.main()
