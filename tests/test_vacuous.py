import tempfile
import unittest
from pathlib import Path

from invigilator.vacuous import guarded_score, load_build, naive_score, score_build

BUILDS = Path(__file__).resolve().parent.parent / "examples" / "builds"


def failed(score):
    return {r.rule for r in score.results if not r.passed}


class VacuousTest(unittest.TestCase):
    def test_empty_folder_scores_high_naively_and_is_refused_by_guard(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(naive_score(empty).value, 7 / 8)
            guarded = guarded_score(empty)
            self.assertIsNone(guarded.value)
            self.assertEqual(guarded.reason, "no HTML files")

    def test_empty_build_outranks_real_builds_under_naive_scorer(self):
        scores = {name: naive_score(BUILDS / name).value for name in ("empty", "good", "mediocre", "broken")}
        self.assertEqual(scores, {"empty": 0.875, "good": 1.0, "mediocre": 0.625, "broken": 0.25})
        self.assertGreater(scores["empty"], scores["mediocre"])

    def test_guard_scores_real_builds_like_naive(self):
        for name in ("good", "mediocre", "broken"):
            self.assertEqual(guarded_score(BUILDS / name).value, naive_score(BUILDS / name).value)

    def test_guard_refuses_html_without_visible_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "index.html").write_text(
                "<html><head><title>Hi</title></head><body><main><script>go()</script></main></body></html>")
            self.assertIsNotNone(naive_score(tmp).value)
            self.assertIn("visible text", guarded_score(tmp).reason)

    def test_rules_catch_the_planted_problems(self):
        self.assertEqual(failed(naive_score(BUILDS / "good")), set())
        self.assertEqual(failed(naive_score(BUILDS / "mediocre")),
                         {"hex colors only in tokens", "no !important", "no inline style attributes"})
        self.assertEqual(failed(naive_score(BUILDS / "broken")),
                         {"hex colors only in tokens", "every <img> has alt", "no console.log",
                          "buttons have accessible names", "a <main> landmark exists",
                          "headings do not skip levels"})

    def test_hex_allowed_only_in_custom_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            css = Path(tmp, "a.css")
            css.write_text(":root { --ink: #222; } #face p { color: var(--ink); }")
            self.assertNotIn("hex colors only in tokens", failed(score_build(load_build(tmp))))
            css.write_text("p { color: #222; }")
            self.assertIn("hex colors only in tokens", failed(score_build(load_build(tmp))))


if __name__ == "__main__":
    unittest.main()
