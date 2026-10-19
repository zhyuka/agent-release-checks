"""Split a shell command line into simple commands.

Good enough to tell which programs a command line runs, where it sends
output, and what decides its exit code. It reads the command line, not
what the programs do: a script can still write files without showing it here.
"""
import os
import re
import shlex
from dataclasses import dataclass, field

OPERATOR = set("();<>|&")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
WRAPPERS = {"builtin", "command", "env", "exec", "nice", "nohup", "stdbuf", "sudo", "time",
            "timeout", "xargs"}
DURATION = re.compile(r"\d+(\.\d+)?[smhd]?")


@dataclass
class Simple:
    """One simple command: its words, its redirections, and the separator after it."""
    words: list
    redirects: list = field(default_factory=list)
    then: str = ""


def _drop_heredocs(command):
    """Heredoc bodies are input text, not commands."""
    lines, ends = [], []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        lines.append(line)
        ends = [m.group(2) for m in HEREDOC.finditer(line)]
    return "\n".join(lines)


def parse(command):
    """Simple commands in order. Raises ValueError if the quoting does not balance."""
    text = _drop_heredocs(command).replace("\\\n", " ").replace("\n", " ; ").replace("`", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    commands, current, i = [], Simple([]), 0
    while i < len(tokens):
        token = tokens[i]
        if set(token) <= OPERATOR and ("<" in token or ">" in token):
            if current.words and current.words[-1].isdigit():
                current.words.pop()
            current.redirects.append((token, tokens[i + 1] if i + 1 < len(tokens) else ""))
            i += 2
            continue
        if set(token) <= OPERATOR:
            current.then = token
            commands.append(current)
            current = Simple([])
        else:
            current.words.append(token)
        i += 1
    commands.append(current)
    return [c for c in commands if c.words or c.redirects]


def writes_file(operator, target):
    """True when a redirection sends output to a file."""
    if ">" not in operator:
        return False
    if operator == ">&" and (target.isdigit() or target == "-"):
        return False
    return not target.startswith("/dev/")


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
