"""Vacuous-pass guard for a rule-based scorer of static web builds.

Most rules say "the build did not do X". A build with no code never does X,
so it passes them. guarded_score() refuses to score a build with nothing to see.
"""
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

HIDDEN = {"head", "script", "style", "template"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


class Page(HTMLParser):
    """What the rules need from one HTML file."""

    def __init__(self, html):
        super().__init__()
        self.tags, self.text, self.scripts, self.buttons, self.headings = [], [], [], [], []
        self._hidden, self._button = [], None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag in HIDDEN:
            self._hidden.append(tag)
        elif tag == "button":
            self._button = (attrs, [])
        elif tag in HEADINGS:
            self.headings.append(int(tag[1]))

    def handle_endtag(self, tag):
        if self._hidden and self._hidden[-1] == tag:
            self._hidden.pop()
        elif tag == "button" and self._button:
            attrs, words = self._button
            self.buttons.append((attrs, " ".join(words)))
            self._button = None

    def handle_data(self, data):
        if self._hidden:
            if self._hidden[-1] == "script" and data.strip():
                self.scripts.append(data)
        elif data.strip():
            self.text.append(data.strip())
            if self._button:
                self._button[1].append(data.strip())


@dataclass
class Build:
    pages: dict
    css: dict
    js: dict


def load_build(folder):
    folder = Path(folder)
    build = Build({}, {}, {})
    for path in sorted(folder.rglob("*")):
        kind = path.suffix.lstrip(".")
        if not path.is_file() or kind not in ("html", "css", "js"):
            continue
        name, text = path.relative_to(folder).as_posix(), path.read_text(encoding="utf-8")
        if kind == "html":
            build.pages[name] = Page(text)
        elif kind == "css":
            build.css[name] = text
        else:
            build.js[name] = text
    return build


# Each rule returns (items inspected, violations).

def hex_only_in_tokens(b):
    blocks, hits = 0, []
    for name, css in b.css.items():
        for body in re.findall(r"\{([^{}]*)\}", css):
            blocks += 1
            for decl in body.split(";"):
                if not decl.strip().startswith("--"):
                    hits += [f"{name}: {h}" for h in HEX.findall(decl)]
    return blocks, hits


def no_important(b):
    return len(b.css), [f"{n}: !important" for n, css in b.css.items()
                        for _ in re.finditer(r"!\s*important", css)]


def img_alt(b):
    imgs = [(n, a) for n, p in b.pages.items() for t, a in p.tags if t == "img"]
    return len(imgs), [f"{n}: <img src={a.get('src')}> has no alt" for n, a in imgs if "alt" not in a]


def no_inline_style(b):
    tags = [(n, t, a) for n, p in b.pages.items() for t, a in p.tags]
    return len(tags), [f"{n}: <{t} style>" for n, t, a in tags if "style" in a]


def no_console_log(b):
    sources = list(b.js.items()) + [(n, s) for n, p in b.pages.items() for s in p.scripts]
    return len(sources), [f"{n}: console.log" for n, s in sources
                          for _ in re.finditer(r"console\.log\(", s)]


def buttons_named(b):
    buttons = [(n, a, text) for n, p in b.pages.items() for a, text in p.buttons]
    unnamed = [n for n, a, text in buttons
               if not (text or a.get("aria-label") or a.get("aria-labelledby") or a.get("title"))]
    return len(buttons), [f"{n}: <button> has no accessible name" for n in unnamed]


def has_main(b):
    if not b.pages:
        return 0, ["no page has a <main> landmark"]
    return len(b.pages), [f"{n}: no <main>" for n, p in b.pages.items()
                          if not any(t == "main" for t, _ in p.tags)]


def heading_order(b):
    count, hits = 0, []
    for name, page in b.pages.items():
        prev = 0
        for level in page.headings:
            count += 1
            if level > prev + 1:
                hits.append(f"{name}: jumps to h{level}")
            prev = level
    return count, hits


RULES = [
    ("hex colors only in tokens", hex_only_in_tokens),
    ("no !important", no_important),
    ("every <img> has alt", img_alt),
    ("no inline style attributes", no_inline_style),
    ("no console.log", no_console_log),
    ("buttons have accessible names", buttons_named),
    ("a <main> landmark exists", has_main),
    ("headings do not skip levels", heading_order),
]


@dataclass
class RuleResult:
    rule: str
    inspected: int
    violations: list

    @property
    def passed(self):
        return not self.violations


@dataclass
class Score:
    value: Optional[float]
    results: list
    reason: str = ""

    def __str__(self):
        return f"UNSCORABLE ({self.reason})" if self.value is None else f"{self.value:.2f}"


def score_build(build):
    results = [RuleResult(name, *rule(build)) for name, rule in RULES]
    return Score(sum(r.passed for r in results) / len(results), results)


def naive_score(folder):
    return score_build(load_build(folder))


def visible_text(build):
    return " ".join(chunk for page in build.pages.values() for chunk in page.text)


def guarded_score(folder):
    build = load_build(folder)
    score = score_build(build)
    if not build.pages:
        return Score(None, score.results, "no HTML files")
    if not visible_text(build):
        return Score(None, score.results, "no visible text in any HTML file")
    return score


def compare(builds_dir):
    """(name, naive, guarded) for every build folder, highest naive score first."""
    rows = [(f.name, naive_score(f), guarded_score(f))
            for f in sorted(Path(builds_dir).iterdir()) if f.is_dir()]
    return sorted(rows, key=lambda row: -row[1].value)
