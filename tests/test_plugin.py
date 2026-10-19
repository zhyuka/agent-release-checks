"""The plugin files have the fields Claude Code's plugin and marketplace references require.

claude plugin validate is the full check. This one runs without Claude Code, in CI.
"""
import json
import re
import shlex
import unittest
from pathlib import Path

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
        self.assertTrue(entry["source"].startswith("./") and ".." not in entry["source"])

    def test_hook_commands_point_at_scripts_that_exist(self):
        hooks = load("hooks/hooks.json")["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse", "Stop", "SubagentStop"})
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertEqual(handler["type"], "command")
                    words = shlex.split(handler["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT)))
                    self.assertEqual(words[0], "python3")
                    self.assertTrue(Path(words[1]).is_file(), words[1])

    def test_pre_tool_use_matches_every_tool_the_hook_handles(self):
        [group] = load("hooks/hooks.json")["hooks"]["PreToolUse"]
        self.assertEqual(set(group["matcher"].split("|")),
                         {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "Agent"})

    def test_skill_has_a_description(self):
        text = (ROOT / "skills" / "release-check" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertRegex(text.split("---")[1], r"\ndescription: \S")


if __name__ == "__main__":
    unittest.main()
