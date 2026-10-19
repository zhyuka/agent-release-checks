import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from invigilator.calibrate import Calibration, calibrate, calibrate_all
from invigilator.cli import main
from invigilator.vacuous import naive_score

GOOD = Path(__file__).resolve().parent.parent / "examples" / "builds" / "good"


class CalibrateTest(unittest.TestCase):
    def test_all_three_checks_pass_calibration(self):
        results = calibrate_all(GOOD)
        self.assertEqual([c.check for c in results if c.ok], [c.check for c in results])
        self.assertEqual(len(results), 3)

    def test_check_that_never_objects_fails(self):
        result = calibrate("silent", lambda case: False, ["bad"], ["good"])
        self.assertFalse(result.ok)
        self.assertEqual(str(result), "silent: caught 0 of 1 planted bad, passed 1 of 1 good")

    def test_check_that_always_objects_fails(self):
        result = calibrate("noisy", lambda case: True, ["bad"], ["good"])
        self.assertFalse(result.ok)
        self.assertEqual((result.caught, result.passed), (1, 0))

    def test_naive_scorer_cannot_serve_as_the_guard(self):
        with tempfile.TemporaryDirectory() as empty:
            result = calibrate("naive", lambda b: naive_score(b).value is None, [empty], [GOOD])
        self.assertEqual((result.caught, result.bad), (0, 1))

    def test_demo_exit_code_follows_calibration(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["demo"]), 0)
            failing = [Calibration("broken check", 0, 1, 1, 1)]
            with mock.patch("invigilator.calibrate.calibrate_all", return_value=failing):
                self.assertEqual(main(["demo"]), 1)


if __name__ == "__main__":
    unittest.main()
