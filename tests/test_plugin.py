"""The plugin files have the fields Claude Code's plugin and marketplace references require.

claude plugin validate is the full check. This one runs without Claude Code, in CI.
"""
import json
import re
import shlex
import unittest
from pathlib import Path

from release_checks import hooks as hooks_module
from release_checks import plugin

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RESERVED = re.compile(r"(claude|anthropics?|cc-plugin)-|(claude|anthropics?|claude-code|claude-mods)$")


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class PluginFilesTest(unittest.TestCase):
    def test_plugin_manifest(self):
        plugin = load(".claude-plugin/plugin.json")
        self.assertRegex(plugin["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertIsNone(RESERVED.match(plugin["name"]))
        self.assertTrue(plugin["author"]["name"])
        self.assertEqual(plugin["version"], re.search(r'version = "(.+)"',
                                                      (ROOT / "pyproject.toml").read_text()).group(1))

    def test_marketplace_lists_this_plugin_from_the_repo_root(self):
        market, plugin = load(".claude-plugin/marketplace.json"), load(".claude-plugin/plugin.json")
        self.assertTrue(NAME.fullmatch(market["name"]))
        self.assertTrue(market["owner"]["name"])
        [entry] = market["plugins"]
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual(entry["version"], plugin["version"])
        self.assertTrue(entry["source"].startswith("./") and ".." not in entry["source"])

    def test_hook_commands_run_the_launcher_and_differ_only_in_the_hook_name(self):
        hooks = load("hooks/hooks.json")["hooks"]
        names = {"SessionStart": "session_status", "PreToolUse": "reviewer_readonly",
                 "Stop": "qa_before_done", "SubagentStop": "qa_before_done"}
        self.assertEqual(set(hooks), set(names))
        shapes = set()
        for event, groups in hooks.items():
            [group] = groups
            [handler] = group["hooks"]
            self.assertEqual(handler["type"], "command")
            self.assertNotIn("args", handler)  # exec form can't pick the interpreter per platform
            self.assertNotIn("${user_config.", handler["command"])  # rejected in shell form
            sh_line, ps_line = handler["command"].split("\n")
            words = shlex.split(sh_line.replace("${CLAUDE_PLUGIN_ROOT}", ROOT.as_posix()))
            self.assertEqual(words, ["exec", "sh", (ROOT / "hooks" / "run.sh").as_posix(), names[event]])
            self.assertIn(f'"$env:CLAUDE_PLUGIN_ROOT/hooks/run.py" {names[event]};', ps_line)
            self.assertTrue(ps_line.rstrip().endswith("exit 0"))
            shapes.add(handler["command"].replace(names[event], "HOOK"))
        self.assertEqual(len(shapes), 1, "the four hook commands should differ only in the hook name")
        self.assertEqual(set(names.values()) - {"session_status"}, set(plugin.SCRIPTS))
        self.assertTrue(all(path.is_file() for path in plugin.SCRIPTS.values()))

    def test_unset_plugin_options_mean_the_defaults(self):
        options = load(".claude-plugin/plugin.json")["userConfig"]
        self.assertEqual(set(options), {*hooks_module.LIST_OPTIONS, "reviewer_shell", "session_status"})
        for key in hooks_module.LIST_OPTIONS:
            self.assertEqual(options[key]["default"], ", ".join(hooks_module.DEFAULTS[key]), key)
        self.assertEqual(options["reviewer_shell"]["default"], hooks_module.DEFAULTS["reviewer_shell"])
        self.assertEqual(options["session_status"]["default"], plugin.STATUS_MODES[0])
        for option in options.values():
            self.assertEqual(option["type"], "string")
            self.assertNotIn("options", option)  # a fixed list needs Claude Code 2.1.271 to load at all

    def test_pre_tool_use_matches_every_tool_the_hook_handles(self):
        [group] = load("hooks/hooks.json")["hooks"]["PreToolUse"]
        self.assertEqual(set(group["matcher"].split("|")),
                         {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "Agent"})

    def test_each_skill_has_a_description(self):
        skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual([path.parent.name for path in skills], ["release-check", "setup"])
        for path in skills:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), path)
            self.assertRegex(text.split("---")[1], r"\ndescription: \S", path)

    def test_shell_scripts_keep_lf_line_endings(self):
        self.assertIn("*.sh text eol=lf", (ROOT / ".gitattributes").read_text().splitlines())
        self.assertNotIn(b"\r", (ROOT / "hooks" / "run.sh").read_bytes())


if __name__ == "__main__":
    unittest.main()
