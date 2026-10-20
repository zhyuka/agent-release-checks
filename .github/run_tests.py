"""Run the unit tests the way `python -m unittest discover -s tests -v` does, and print the
first failures as GitHub Actions error annotations, so the reason shows on the run page and
through the public checks API without opening the job log."""
import os
import sys
import unittest

MAX_NOTES = 10  # GitHub keeps 10 error annotations per step


def escape(text, prop=False):
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


class AnnotatingResult(unittest.TextTestResult):
    notes = 0

    def note(self, kind, test, err):
        if AnnotatingResult.notes >= MAX_NOTES:
            return
        AnnotatingResult.notes += 1
        lines = self._exc_info_to_string(err, test).rstrip().splitlines()
        body = "\n".join(lines[-15:])
        self.stream.flush()
        sys.stdout.write("::error title=%s::%s\n" % (escape("%s %s" % (kind, test), prop=True), escape(body)))
        sys.stdout.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.note("FAIL", test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self.note("ERROR", test, err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self.note("FAIL" if failed else "ERROR", subtest, err)


if __name__ == "__main__":
    sys.path[0] = os.getcwd()  # as `python -m unittest` has it: the repo root, not .github/
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2, resultclass=AnnotatingResult).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
