import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from release_checks.step_ran import REPORT, build_sha256, cheap_agent, qa_agent, trial, verify

GOOD = Path(__file__).resolve().parent.parent / "examples" / "builds" / "good"


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


if __name__ == "__main__":
    unittest.main()
