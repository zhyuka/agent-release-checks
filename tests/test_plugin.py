"""The plugin files have the fields Claude Code's plugin and marketplace references require.

claude plugin validate is the full check. This one runs without Claude Code, in CI.
"""
import json
import re
import shlex
import unittest
from pathlib import Path

import invigilator
from invigilator import hooks as hooks_module
from invigilator import plugin

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
        self.assertEqual(plugin["version"], invigilator.__version__)

    def test_agent_plugins_manifest(self):
        # The Agent Plugins 1.0 manifest that VS Code, Copilot, Cursor and Codex read. Its field
        # list is closed, and a client rejects a plugin whose manifest breaks the schema.
        portable, claude = load("plugin.json"), load(".claude-plugin/plugin.json")
        self.assertEqual(portable["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        self.assertLessEqual(set(portable), {"$schema", "name", "version", "description", "homepage",
                                             "repository", "license", "author", "keywords", "extensions"})
        self.assertLessEqual(set(portable["author"]), {"name", "email", "url"})
        self.assertRegex(portable["name"], r"^[a-z0-9]([a-z0-9.-]{0,62}[a-z0-9])?$")
        self.assertNotRegex(portable["name"], r"--|\.\.")
        for key in ("name", "version", "homepage", "repository", "license", "author"):
            self.assertEqual(portable[key], claude[key], key)
        self.assertFalse((ROOT / "mcp.json").exists(), "the plugin starts no MCP server")

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
            self.assertEqual(ps_line, "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "
                                      f'"$env:CLAUDE_PLUGIN_ROOT/hooks/run.ps1" {names[event]}')
            shapes.add(handler["command"].replace(names[event], "HOOK"))
        self.assertEqual(len(shapes), 1, "the four hook commands should differ only in the hook name")
        self.assertEqual(set(names.values()) - {"session_status"}, set(plugin.SCRIPTS))
        self.assertTrue(all(path.is_file() for path in plugin.SCRIPTS.values()))

    def test_each_hook_command_line_runs_one_program_it_names(self):
        # The plugin directory's validator reads a hook command as shell. It blocks one whose
        # program comes from a variable or an expression, since that could be any package launcher.
        for event, groups in load("hooks/hooks.json")["hooks"].items():
            for line in groups[0]["hooks"][0]["command"].split("\n"):
                with self.subTest(event=event, line=line[:30]):
                    words = list(shlex.shlex(line, posix=True, punctuation_chars=";|&"))
                    self.assertRegex(words[0], r"^[A-Za-z][A-Za-z0-9._-]*$")
                    self.assertEqual([w for w in words if set(w) <= set(";|&")], [], "one program per line")
        # A PowerShell script that names $input reads all of stdin before it starts, so no Python
        # it runs would get the hook input.
        self.assertNotRegex((ROOT / "hooks" / "run.ps1").read_text(encoding="utf-8"), r"(?i)\$input\b")

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
        self.assertEqual([path.parent.name for path in skills], ["check", "miss-to-check", "review", "setup"])
        for path in skills:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"), path)
            self.assertRegex(text.split("---")[1], r"\nname: " + re.escape(path.parent.name) + r"\n", path)
            self.assertRegex(text.split("---")[1], r"\ndescription: \S", path)

    def test_the_reviewer_agent_is_read_only_by_the_default_settings(self):
        # The review skill starts invigilator:reviewer. The reviewer hook keeps it from writing,
        # and where hooks don't run, its tool list has no file-writing tool.
        text = (ROOT / "agents" / "reviewer.md").read_text(encoding="utf-8")
        front = text.split("---")[1]
        self.assertRegex(front, r"\nname: reviewer\n")
        agent = f"{load('.claude-plugin/plugin.json')['name']}:reviewer"
        self.assertTrue(hooks_module.matches(agent, hooks_module.DEFAULTS["reviewer_agents"]))
        tools = re.search(r"\ntools: (.+)\n", front).group(1).split(", ")
        self.assertEqual([t for t in tools if t in ("Edit", "Write", "MultiEdit", "NotebookEdit")], [])
        self.assertIn(f"`{agent}`", (ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8"))

    def test_ci_templates_install_this_release_and_prove_the_checks(self):
        # The checker comes from a fixed release outside the project, so a change can't edit it.
        for name in ("github-actions.yml", "azure-pipelines.yml"):
            text = (ROOT / "ci" / name).read_text(encoding="utf-8")
            self.assertIn(f'git+https://github.com/zhyuka/invigilator@v{invigilator.__version__}"', text, name)
            self.assertIn("invigilator check --prove --base", text, name)
        self.assertIn("ci/pre-push text eol=lf", (ROOT / ".gitattributes").read_text().splitlines())
        self.assertNotIn(b"\r", (ROOT / "ci" / "pre-push").read_bytes())

    def test_shell_scripts_keep_lf_line_endings(self):
        self.assertIn("*.sh text eol=lf", (ROOT / ".gitattributes").read_text().splitlines())
        self.assertNotIn(b"\r", (ROOT / "hooks" / "run.sh").read_bytes())


if __name__ == "__main__":
    unittest.main()
