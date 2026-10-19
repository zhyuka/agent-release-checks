"""Split a shell command line into simple commands.

Good enough to tell which programs a command line runs, where it sends
output, and what decides its exit code. It reads the command line, not
what the programs do: a script can still write files without showing it here.

split() is the reader both checks use: it follows quoting, comments,
heredocs, command and process substitution, and compound commands.
program() strips the VAR=value words and wrappers in front of a program.
drop_heredocs() gives the command line without its heredoc bodies.
"""
import os
import re
from dataclasses import dataclass, field

ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
WRAPPERS = {"builtin", "command", "env", "exec", "nice", "nohup", "stdbuf", "sudo", "time",
            "timeout", "xargs"}
DURATION = re.compile(r"\d+(\.\d+)?[smhd]?")


@dataclass
class Simple:
    """One simple command: its words, its redirections, and the separator after it.

    split() also fills stdin with the heredoc bodies and here-strings the
    command reads, as (text, expanded) pairs, and kind with "for", "case" or
    "function" for a header that runs no program."""
    words: list
    redirects: list = field(default_factory=list)
    then: str = ""
    stdin: list = field(default_factory=list)
    kind: str = ""


def drop_heredocs(command):
    """The command line without its heredoc bodies, which are input text, not commands.

    A rough cut by lines, for sizing a command before reading it with split()."""
    lines, ends = [], []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        lines.append(line)
        ends = [m.group(2) for m in HEREDOC.finditer(line)]
    return "\n".join(lines)


DEVICES = re.compile(r"/dev/(null|zero|stdout|stderr|stdin|tty|fd/\d+)")


def writes_file(operator, target):
    """True when a redirection sends output to a file.

    Only the null device, the standard streams, the terminal and file
    descriptors count as not a file; the path is normalized first, so
    /dev/../tmp/x is a file."""
    if ">" not in operator or target.startswith((">(", "<(")):
        return False  # a process substitution's own commands are read on their own
    if operator in (">&", "<&") and (target.isdigit() or target == "-"):
        return False
    return not DEVICES.fullmatch(os.path.normpath(target)) if target.startswith("/dev/") else True


def program(words):
    """The program and its arguments, without leading VAR=value words and wrappers like sudo."""
    i = 0
    while i < len(words):
        if ASSIGNMENT.match(words[i]):
            i += 1
        elif os.path.basename(words[i]) in WRAPPERS:
            wrapper = os.path.basename(words[i])
            i += 1
            while i < len(words) and (words[i].startswith("-") or ASSIGNMENT.match(words[i])):
                i += 1
            if wrapper == "timeout" and i < len(words) and DURATION.fullmatch(words[i]):
                i += 1
        else:
            break
    return words[i:]


# split(): the fuller reader the read-only check uses.

SKIPPED_WORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "}", "fi",
                 "done", "esac"}
REDIRECTS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", "<&", ">>!", ">>", ">|", ">!", ">&", "<", ">")
PLAIN = re.compile(r"[^\s;&|<>()'\"\\$`]+")
PLAIN_IN_DOUBLE = re.compile(r"[^\"\\$`]+")
GLOB_BEFORE_PAREN = set("*?@!+]")
ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b",
        "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


class _Reader:
    """Reads one piece of shell text. levels collects each list of commands it
    finds: the text's own commands, then one list per substitution."""

    def __init__(self, text, levels):
        self.s, self.i, self.levels = text, 0, levels

    def at(self, k=0):
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def starts(self, prefix):
        return self.s.startswith(prefix, self.i)

    def run(self):
        level = []
        self.levels.append(level)
        level.extend(self.commands())

    # Expansions -------------------------------------------------------------

    def substitution(self):
        """After "$(", "<(" or ">(": the commands up to the matching ")"."""
        level = []
        self.levels.append(level)
        level.extend(self.commands(stop=True))

    def arithmetic(self):
        """After "$((" or "((": skip to the matching "))", following $( inside."""
        depth = 2
        while self.i < len(self.s):
            if self.starts("$(") and not self.starts("$(("):
                self.i += 2
                self.substitution()
                continue
            c = self.at()
            self.i += 1
            depth += {"(": 1, ")": -1}.get(c, 0)
            if depth == 0:
                return
        raise ValueError("arithmetic is not closed")

    def backtick(self):
        """After a backtick: the text up to the closing one, read as commands."""
        out = []
        while self.i < len(self.s):
            c = self.at()
            if c == "\\" and self.at(1) in "`$\\":
                out.append(self.at(1))
                self.i += 2
                continue
            self.i += 1
            if c == "`":
                _Reader("".join(out), self.levels).run()
                return "`" + "".join(out) + "`"
            out.append(c)
        raise ValueError("backtick is not closed")

    def braces(self):
        """After "${": skip to the matching "}", following substitutions inside."""
        depth = 1
        while self.i < len(self.s):
            c = self.at()
            if c == "\\":
                self.i += 2
            elif c in "'\"":
                self.i += 1
                self.quoted(c)
            elif c == "$" and self.dollar(None) is not None:
                pass
            elif c == "`":
                self.i += 1
                self.backtick()
            else:
                self.i += 1
                depth += {"{": 1, "}": -1}.get(c, 0)
                if depth == 0:
                    return
        raise ValueError("${ is not closed")

    def dollar(self, quote):
        """The $ form at the cursor as text, or None when $ starts nothing special."""
        start = self.i
        if self.starts("$(("):
            self.i += 3
            self.arithmetic()
        elif self.starts("$("):
            self.i += 2
            self.substitution()
        elif self.starts("${"):
            self.i += 2
            self.braces()
        elif quote is None and self.starts("$'"):
            self.i += 2
            return self.ansi()
        elif quote is None and self.starts('$"'):
            self.i += 2
            return self.quoted('"')
        else:
            return None
        return self.s[start:self.i]

    def ansi(self):
        """After $': the decoded text up to the closing quote."""
        out = []
        while self.i < len(self.s):
            c = self.at()
            if c == "\\" and self.i + 1 < len(self.s):
                out.append(ANSI.get(self.at(1), "\\" + self.at(1)))
                self.i += 2
                continue
            self.i += 1
            if c == "'":
                return "".join(out)
            out.append(c)
        raise ValueError("$' quote is not closed")

    def quoted(self, quote):
        """After an opening quote: the text up to the closing one."""
        if quote == "'":
            end = self.s.find("'", self.i)
            if end < 0:
                raise ValueError("single quote is not closed")
            text, self.i = self.s[self.i:end], end + 1
            return text
        out = []
        while self.i < len(self.s):
            run = PLAIN_IN_DOUBLE.match(self.s, self.i)
            if run:
                out.append(run.group())
                self.i = run.end()
                continue
            c = self.at()
            if c == "\\":
                nxt = self.at(1)
                out.append(nxt if nxt in '"\\$`' else "" if nxt == "\n" else "\\" + nxt)
                self.i += 2
            elif c == "$":
                text = self.dollar('"')
                out.append("$" if text is None else text)
                self.i += 1 if text is None else 0
            elif c == "`":
                self.i += 1
                out.append(self.backtick())
            else:
                self.i += 1
                return "".join(out)
        raise ValueError("double quote is not closed")

    def balanced(self):
        """A parenthesized run inside a word, such as a glob qualifier or an array value."""
        start, depth = self.i, 0
        while self.i < len(self.s):
            c = self.at()
            if c in "'\"":
                self.i += 1
                self.quoted(c)
                continue
            if c == "$" and self.dollar(None) is not None:
                continue
            self.i += 1
            depth += {"(": 1, ")": -1}.get(c, 0)
            if depth == 0:
                return self.s[start:self.i]
        raise ValueError("parenthesis is not closed")

    def word(self):
        """(text, quoted) of the word at the cursor, with quotes removed."""
        out, quoted = [], False
        while self.i < len(self.s):
            run = PLAIN.match(self.s, self.i)
            if run:
                out.append(run.group())
                self.i = run.end()
                continue
            c = self.at()
            if c == "\\":
                if self.at(1) != "\n":
                    out.append(self.at(1))
                    quoted = True
                self.i += 2
            elif c in "'\"":
                self.i += 1
                out.append(self.quoted(c))
                quoted = True
            elif c == "$":
                quoted = quoted or self.starts("$'") or self.starts('$"')
                text = self.dollar(None)
                out.append("$" if text is None else text)
                self.i += 1 if text is None else 0
            elif c == "`":
                self.i += 1
                out.append(self.backtick())
            elif c == "(" and out and (out[-1][-1:] in GLOB_BEFORE_PAREN or out[-1].endswith("=")
                                       or any(g in "".join(out) for g in "*?")):
                out.append(self.balanced())
            else:
                break
        return "".join(out), quoted

    def expansions(self, text):
        """Run the substitutions in text the shell expands, such as an unquoted heredoc body."""
        sub = _Reader(text, self.levels)
        while sub.i < len(sub.s):
            c = sub.at()
            try:
                if c == "\\":
                    sub.i += 2
                elif c == "`":
                    sub.i += 1
                    sub.backtick()
                elif c != "$" or sub.dollar(None) is None:
                    sub.i += 1
            except ValueError:
                return  # the shell refuses an unclosed substitution here, so nothing in it runs

    # Commands ---------------------------------------------------------------

    def blank(self):
        while self.i < len(self.s):
            if self.at() in " \t":
                self.i += 1
            elif self.starts("\\\n"):
                self.i += 2
            else:
                return

    def heredocs(self, pending):
        """At the start of a line: read each pending heredoc body into its command."""
        for delimiter, strip, expand, simple in pending:
            lines = []
            while self.i < len(self.s):
                end = self.s.find("\n", self.i)
                line = self.s[self.i:] if end < 0 else self.s[self.i:end]
                self.i = len(self.s) if end < 0 else end + 1
                if (line.lstrip("\t") if strip else line) == delimiter:
                    break
                lines.append(line)
            body = "\n".join(lines)
            simple.stdin.append((body, expand))
            if expand:
                self.expansions(body)
        pending.clear()

    def condition(self, current):
        """After "[[": the words up to "]]", where < > && || compare instead of redirecting."""
        while True:
            self.blank()
            if self.i >= len(self.s):
                raise ValueError("[[ is not closed")
            if self.starts("]]"):
                self.i += 2
                current.words.append("]]")
                return
            if self.at() in "<>&|()!\n":
                start = self.i
                while self.i < len(self.s) and self.at() in "<>&|()!=\n":
                    self.i += 1
                current.words.append(self.s[start:self.i].strip())
            else:
                current.words.append(self.word()[0])

    def commands(self, stop=False):
        """Simple commands up to the end of the text, or with stop up to an unmatched ")"."""
        out, current, pending, case = [], Simple([]), [], []
        depth, skip_name = 0, False

        def finish(then):
            nonlocal current
            current.then = then
            if current.words or current.redirects or current.stdin:
                out.append(current)
            elif out and then not in ("", ";"):
                out[-1].then = then  # after a group such as (a; b) &, the separator applies to b
            current = Simple([])

        while True:
            self.blank()
            if self.i >= len(self.s):
                if stop:
                    raise ValueError("substitution is not closed")
                finish("")
                return out
            c = self.at()
            if c == "\n":
                self.i += 1
                finish(";")
                self.heredocs(pending)
                continue
            if c == "#":
                end = self.s.find("\n", self.i)
                self.i = len(self.s) if end < 0 else end
                continue
            if case and case[-1] == "pattern":
                text, quoted = ("", False) if c in "()|;" else self.word()
                if c == ")":
                    case[-1] = "body"
                if text == "esac" and not quoted:
                    case.pop()
                self.i += 1 if c in "()|;" else 0
                continue
            if c in "<>" and self.at(1) == "(":
                self.i += 2
                self.substitution()
                current.words.append(c + "(...)")
                continue
            redirect = next((r for r in REDIRECTS if self.starts(r)), None)
            if redirect:
                self.i += len(redirect)
                self.blank()
                if self.at() and self.at() in "<>" and self.at(1) == "(":
                    target, quoted = self.at() + "(...)", False
                    self.i += 2
                    self.substitution()
                else:
                    target, quoted = self.word()
                if redirect in ("<<", "<<-"):
                    pending.append((target, redirect == "<<-", not quoted, current))
                elif redirect == "<<<":
                    current.stdin.append((target, True))
                else:
                    current.redirects.append((redirect, target))
                continue
            separator = next((t for t in (";;&", ";;", ";&", "&&", "||", "|&", ";", "&", "|")
                              if self.starts(t)), None)
            if separator:
                self.i += len(separator)
                finish(";" if separator.startswith(";") else separator)
                if separator.startswith(";") and separator != ";" and case:
                    case[-1] = "pattern"
                continue
            if c == "(":
                if self.at(1) == "(" and (not current.words or current.kind == "for"):
                    self.i += 2
                    self.arithmetic()
                    continue
                self.i += 1
                self.blank()
                if len(current.words) == 1 and not current.kind and self.at() == ")":
                    self.i += 1
                    current.kind = "function"
                    finish(";")
                    continue
                finish(";")
                depth += 1
                continue
            if c == ")":
                self.i += 1
                if depth:
                    depth -= 1
                elif stop:
                    finish("")
                    return out
                finish(";")
                continue
            start = self.i
            text, quoted = self.word()
            if self.i == start:
                self.i += 1  # a character no rule above takes; never loop on it
                continue
            if self.at() and self.at() in "<>" and text.isdigit() and not quoted:
                continue  # the file descriptor of the redirection that follows
            if skip_name:
                skip_name = False
                continue
            if not current.words and not quoted and not current.kind:
                if text == "esac" and case:
                    case.pop()
                if text in SKIPPED_WORDS:
                    continue
                if text == "function":
                    skip_name = True
                    continue
                if text in ("for", "select"):
                    current.kind = "for"
                elif text == "case":
                    current.kind = "case"
                elif text == "[[":
                    current.words.append(text)
                    self.condition(current)
                    continue
            if current.kind == "case" and text == "in" and not quoted:
                finish(";")
                case.append("pattern")
                continue
            current.words.append(text)


def split(command):
    """Every list of simple commands a command line runs: its own commands
    first, then one list for each command or process substitution in it.

    A heredoc body or here-string is attached to the command that reads it as
    stdin; the substitutions in an unquoted heredoc run. Raises ValueError
    when a quote or a substitution is not closed."""
    levels = []
    _Reader(command, levels).run()
    return levels

