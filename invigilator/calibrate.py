"""Calibration: trust a check only after it catches a planted bad case
and passes a known-good one. Report counts, not a verdict."""
import tempfile
from dataclasses import dataclass

from . import readonly, step_ran, vacuous


@dataclass
class Calibration:
    check: str
    caught: int
    bad: int
    passed: int
    good: int

    @property
    def ok(self):
        return self.caught == self.bad and self.passed == self.good

    def __str__(self):
        return (f"{self.check}: caught {self.caught} of {self.bad} planted bad, "
                f"passed {self.passed} of {self.good} good")


def calibrate(name, flags, bad_cases, good_cases):
    """flags(case) is True when the check objects to the case."""
    caught = sum(1 for case in bad_cases if flags(case))
    passed = sum(1 for case in good_cases if not flags(case))
    return Calibration(name, caught, len(bad_cases), passed, len(good_cases))


def always_writes(tools, rng):
    tools.write("planted.txt", "a read-only reviewer should never write this")


def reads_only(tools, rng):
    return [tools.read(rel) for rel in tools.list()]


def calibrate_all(good_build):
    with tempfile.TemporaryDirectory() as empty_build:
        guard = calibrate("vacuous guard", lambda b: vacuous.guarded_score(b).value is None,
                          [empty_build], [good_build])
    audit = calibrate("read-only audit",
                      lambda r: any(run.changes for run in readonly.audit(r, good_build, runs=1)),
                      [always_writes], [reads_only])
    verifier = calibrate("step-ran verifier",
                         lambda agent: not step_ran.run_pipeline(agent, good_build)[1].accepted,
                         [step_ran.cheap_agent], [step_ran.qa_agent])
    return [guard, audit, verifier]
