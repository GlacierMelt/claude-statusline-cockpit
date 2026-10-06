"""Runtime regression: installation PATH is not the host's runtime PATH.

No real config/transcript/database writes, no network/model requests.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_history import BASE, BUCKET_MS, ROOT, event
from install_support import BUNDLE_FILES, install

SGR = re.compile(r"\x1b\[[0-9;]*m")


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit runtime path ")
        self.path = Path(self.tmp.name)
        self.feed = self.path / "events.jsonl"
        self.feed.write_text("".join(json.dumps(row) + "\n" for row in (
            event("baseline", BASE - 11 * BUCKET_MS, read=95, uncached=5),
            event("runtime", read=95, uncached=5),
        )))
        self.payload = {
            "model": {"display_name": "Runtime Test (1M context)"},
            "effort": {"level": "high"},
            "context_window": {"total_input_tokens": 85000,
                               "context_window_size": 200000,
                               "used_percentage": 42.5},
            "workspace": {"current_dir": str(self.path)},
            "cost": {"total_cost_usd": 1.25},
        }
        self.env = {**os.environ, "HOME": str(self.path / "home"),
                    "CLAUDE_CONFIG_DIR": str(self.path / "config"),
                    "CACHE_EVENT_FILE": str(self.feed),
                    "CACHE_TRANSCRIPT_ROOT": str(self.path / "missing"),
                    "CACHE_HISTORY_DB": str(self.path / "history.sqlite3"),
                    "CACHE_HISTORY_NOW": str((BASE + 100000) / 1000),
                    "CACHE_HISTORY_PYTHON": "", "CACHE_HISTORY_DEBUG": "",
                    "COLORTERM": "truecolor", "TERM": "xterm-256color",
                    "TERM_PROGRAM": "", "COLUMNS": "120",
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, script, **env):
        result = subprocess.run(["/bin/bash", str(script)],
                                input=json.dumps(self.payload), text=True,
                                capture_output=True, env={**self.env, **env})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return SGR.sub("", result.stdout).splitlines()

    def assert_metadata(self, rows):
        self.assertIn("Runtime Test", rows[0])
        self.assertIn("tok 85k/200k (42.5%)", rows[0])
        self.assertIn("$1.25", rows[0])
        self.assertEqual(rows[-1], str(self.path))

    def assert_history(self, rows):
        self.assertTrue(rows[1].startswith("▲ hit 95.0%  "), rows)
        bar, badge = rows[1].split("  ", 1)[1].split("   💭 ", 1)
        self.assertEqual(len(bar), 12)
        self.assertEqual(badge, "0")

    def copy_bundle(self):
        bundle = self.path / "manual bundle"
        bundle.mkdir()
        for relative in BUNDLE_FILES:
            dest = bundle / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, dest)
        return bundle

    def test_system_python_39_source_keeps_all_three_rows_and_updates(self):
        if not Path("/usr/bin/python3").is_file():
            self.skipTest("system Python not installed")
        rows = self.render(ROOT / "statusline-command.sh", PATH="/usr/bin:/bin:/usr/sbin:/sbin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_conda_python_39_source_keeps_all_three_rows_and_updates(self):
        conda = Path.home() / "opt/anaconda3/bin/python3"
        if not conda.is_file():
            self.skipTest("Conda Python not installed")
        rows = self.render(ROOT / "statusline-command.sh",
                           CACHE_HISTORY_PYTHON=str(conda),
                           PATH=str(conda.parent) + ":/usr/bin:/bin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_installed_pin_ignores_broken_path_python(self):
        config = self.path / "installed config"
        result = install(ROOT, config)
        bundle = Path(result["bundle"])
        self.assertEqual((bundle / ".python-path").read_text().strip(),
                         str(Path(sys.executable).absolute()))
        poison = self.path / "wrong python first"
        poison.mkdir()
        script = poison / "python3"
        script.write_text("#!/bin/sh\nprintf 'wrong runtime invoked\\n' >&2\nexit 97\n")
        script.chmod(0o755)
        rows = self.render(bundle / "statusline-command.sh", PATH=str(poison) + ":/usr/bin:/bin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_pinned_runtime_path_with_spaces_quotes_and_dollar(self):
        alias = self.path / "runtime spaces ' quote $ python"
        alias.symlink_to(sys.executable)
        result = install(ROOT, self.path / "config", python_executable=alias)
        self.assertEqual(result["python_executable"], str(alias))
        rows = self.render(Path(result["bundle"]) / "statusline-command.sh", PATH="/usr/bin:/bin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_missing_pin_falls_back_to_system_runtime_without_losing_metadata(self):
        if not Path("/usr/bin/python3").is_file():
            self.skipTest("system Python not installed")
        result = install(ROOT, self.path / "config")
        bundle = Path(result["bundle"])
        (bundle / ".python-path").write_text(str(self.path / "removed-runtime") + "\n")
        rows = self.render(bundle / "statusline-command.sh", PATH="/usr/bin:/bin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_cache_history_python_override_precedes_pin(self):
        result = install(ROOT, self.path / "config")
        bundle = Path(result["bundle"])
        (bundle / ".python-path").write_text(str(self.path / "nonexistent-runtime") + "\n")
        rows = self.render(bundle / "statusline-command.sh",
                           CACHE_HISTORY_PYTHON=sys.executable, PATH="/usr/bin:/bin")
        self.assert_metadata(rows)
        self.assert_history(rows)

    def test_adapter_import_crash_preserves_payload_model_tokens_and_path(self):
        bundle = self.copy_bundle()
        (bundle / "lib/cache_sources.py").write_text("raise RuntimeError('fixture import crash')\n")
        rows = self.render(bundle / "statusline-command.sh")
        self.assert_metadata(rows)
        self.assertEqual(rows[1], "▲ hit 0.0%  ▁▁▁▁▁▁▁▁▁▁▁▁")

    def test_missing_ledger_preserves_payload_model_tokens_and_path(self):
        bundle = self.copy_bundle()
        (bundle / "lib/cache_history.py").rename(bundle / "lib/cache_history.py.saved")
        rows = self.render(bundle / "statusline-command.sh")
        self.assert_metadata(rows)
        self.assertEqual(rows[1], "▲ hit 0.0%  ▁▁▁▁▁▁▁▁▁▁▁▁")

    def test_import_crash_retains_committed_history_and_payload_rows(self):
        bundle = self.copy_bundle()
        before = self.render(bundle / "statusline-command.sh")
        self.assert_history(before)
        (bundle / "lib/cache_sources.py").write_text("raise RuntimeError('fixture import crash')\n")
        after = self.render(bundle / "statusline-command.sh")
        self.assertEqual(after, before)
        self.assert_metadata(after)

    def test_fallback_corrupt_projection_still_preserves_payload_fields(self):
        bundle = self.copy_bundle()
        self.render(bundle / "statusline-command.sh")
        (bundle / "lib/cache_sources.py").write_text("raise RuntimeError('fixture import crash')\n")
        sidecar = Path(self.env["CACHE_HISTORY_DB"] + ".view.json")
        sidecar.write_text(json.dumps({"{}": {"percentage": 95, "codes": [None] * 12}}))
        rows = self.render(bundle / "statusline-command.sh")
        self.assert_metadata(rows)
        self.assertEqual(rows[1], "▲ hit 0.0%  ▁▁▁▁▁▁▁▁▁▁▁▁")

    def test_fallback_uses_same_explicit_scope_not_shared_projection(self):
        bundle = self.copy_bundle()
        self.render(bundle / "statusline-command.sh")
        (bundle / "lib/cache_sources.py").write_text("raise RuntimeError('fixture import crash')\n")
        rows = self.render(bundle / "statusline-command.sh", CACHE_SCOPE_SESSION="unrecorded-session")
        self.assert_metadata(rows)
        self.assertEqual(rows[1], "▲ hit 0.0%  ▁▁▁▁▁▁▁▁▁▁▁▁")

    def test_runtime_probe_fails_before_any_target_write(self):
        config = self.path / "target"
        config.mkdir()
        settings = config / "settings.json"
        settings.write_text('{"statusLine":{"command":"unchanged"}}')
        before = settings.read_bytes()
        wrong = self.path / "bad-runtime"
        wrong.write_text("#!/bin/sh\nexit 97\n")
        wrong.chmod(0o755)
        with self.assertRaises(ValueError):
            install(ROOT, config, python_executable=wrong)
        self.assertEqual(settings.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in config.iterdir()), ["settings.json"])

    def test_import_probe_rejects_broken_bundle_before_any_target_write(self):
        broken = self.copy_bundle()
        (broken / "lib/cache_sources.py").write_text("raise RuntimeError('fixture import crash')\n")
        config = self.path / "target not yet created"
        with self.assertRaises(ValueError):
            install(broken, config)
        self.assertFalse(config.exists())


if __name__ == "__main__":
    unittest.main()
