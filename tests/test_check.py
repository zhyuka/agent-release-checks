import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from invigilator.calibrate import calibrate
from invigilator.check import check_folder
from invigilator.cli import main
from invigilator.runlog import RunLogError
from invigilator.step_ran import REPORT, build_sha256, qa_agent

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
RUNS, GOOD = EXAMPLES / "runs", EXAMPLES / "builds" / "good"


def statuses(folder):
    return {r.check: r.status for r in check_folder(folder)}


def failed(folder, check):
    return statuses(folder)[check] == "fail"


class CheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name, "run")
        shutil.copytree(GOOD, self.run_dir / "build")

    def write_run(self, **fields):
        (self.run_dir / "run.json").write_text(json.dumps(fields))
        return self.run_dir

    def test_example_runs(self):
        self.assertEqual(statuses(RUNS / "qa-builder"),
                         {"build score": "pass", "read-only": "skip", "step ran": "pass"})
        self.assertEqual(statuses(RUNS / "cheap-builder"),
                         {"build score": "pass", "read-only": "skip", "step ran": "fail"})
        self.assertEqual(statuses(RUNS / "reviewer-wrote"),
                         {"build score": "pass", "read-only": "fail", "step ran": "skip"})
        self.assertEqual(statuses(RUNS / "reviewer-read-only"),
                         {"build score": "pass", "read-only": "pass", "step ran": "skip"})

    def test_each_run_check_catches_its_planted_bad_case_and_passes_its_good_one(self):
        step = calibrate("step ran", lambda f: failed(f, "step ran"),
                         [RUNS / "cheap-builder"], [RUNS / "qa-builder"])
        audit = calibrate("read-only", lambda f: failed(f, "read-only"),
                          [RUNS / "reviewer-wrote"], [RUNS / "reviewer-read-only"])
        self.assertTrue(step.ok, str(step))
        self.assertTrue(audit.ok, str(audit))

    def test_bare_build_folder_runs_only_the_build_check(self):
        self.assertEqual(statuses(GOOD), {"build score": "pass", "read-only": "skip", "step ran": "skip"})
        self.assertEqual(check_folder(EXAMPLES / "builds" / "empty")[0].detail,
                         "UNSCORABLE (no HTML files)")
        self.assertTrue(failed(EXAMPLES / "builds" / "broken", "build score"))

    def test_reviewer_that_changed_the_build_fails_without_any_write_call(self):
        before = build_sha256(self.run_dir / "build")
        (self.run_dir / "build" / "index.html").write_text("<main>edited</main>")
        self.write_run(role="reviewer", build_sha256_before=before, tool_calls=[])
        result = check_folder(self.run_dir)[1]
        self.assertEqual((result.status, result.detail),
                         ("fail", "the build's SHA-256 differs from build_sha256_before"))

    def test_qa_claim_with_no_evidence_fails(self):
        self.write_run(role="builder", claims=["QA passed"])
        self.assertIn("no evidence", check_folder(self.run_dir)[2].detail)

    def test_qa_report_is_checked_against_the_build(self):
        artifacts = self.run_dir / "artifacts"
        artifacts.mkdir()
        self.assertEqual(qa_agent(self.run_dir / "build", artifacts), "QA passed")
        self.write_run(role="builder", claims=["QA passed"])
        self.assertEqual(statuses(self.run_dir)["step ran"], "pass")
        report = json.loads((artifacts / REPORT).read_text())
        (artifacts / REPORT).write_text(json.dumps({**report, "build_sha256": "0" * 64}))
        self.assertEqual(check_folder(self.run_dir)[2].detail, "report names a different build")

    def test_unusable_run_logs_are_errors_not_passes(self):
        for fields, message in (({"role": "tester"}, '"role" must be'),
                                ({"role": "builder", "claim": ["QA passed"]}, "unknown field 'claim'"),
                                ({"role": "builder", "build": "missing"}, "not found"),
                                ({"role": "builder", "tool_calls": [{"input": {}}]}, "tool call 1")):
            self.write_run(**fields)
            with self.assertRaises(RunLogError) as caught:
                check_folder(self.run_dir)
            self.assertIn(message, str(caught.exception))

    def test_cli_exit_codes(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["check", str(RUNS / "qa-builder")]), 0)
            self.assertEqual(main(["check", str(RUNS / "cheap-builder")]), 1)
            self.assertEqual(main(["check", str(self.run_dir / "nope")]), 2)
        self.assertIn("2 of 3 checks ran: 1 passed, 1 failed.", out.getvalue())
        self.assertIn("not a folder", err.getvalue())


if __name__ == "__main__":
    unittest.main()
