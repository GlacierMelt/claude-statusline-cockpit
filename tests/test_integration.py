"""Hermetic Bash/SQLite/concurrency/installer planning tests.

Never executes install.sh and never writes to the user's real config/cache.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_history import BASE, ROOT, event, iso, transcript
from cache_history import History, bridge, discover
from install_support import install, prepare_settings, settings_command

SGR = re.compile(r"\x1b\[[0-9;]*m")


class IntegrationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit integration path ")
        self.path = Path(self.tmp.name)
        self.feed = self.path / "feed.jsonl"
        self.feed.write_text("")
        self.env = {**os.environ, "HOME": str(self.path / "home"),
                    "CLAUDE_CONFIG_DIR": str(self.path / "config"),
                    "CACHE_TRANSCRIPT_ROOT": str(self.path / "projects"),
                    "CACHE_HISTORY_DB": str(self.path / "history.sqlite3"),
                    "CACHE_EVENT_FILE": str(self.feed), "CACHE_HISTORY_NOW": str((BASE + 100000) / 1000),
                    "COLORTERM": "truecolor", "TERM": "xterm-256color", "TERM_PROGRAM": "",
                    "COLUMNS": "80", "PYTHONDONTWRITEBYTECODE": "1"}
        self.payload = {"model": {"display_name": "Claude Test (1M context)"},
                        "effort": {"level": "high"},
                        "context_window": {"context_window_size": 200000,
                        "used_percentage": 42.5, "total_input_tokens": 85000,
                        "total_output_tokens": 500000}, "cost": {"total_cost_usd": 0}}

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, *rows):
        with self.feed.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def render(self, payload=None, **env):
        result = subprocess.run(["/bin/bash", str(ROOT / "statusline-command.sh")],
                                input=json.dumps(self.payload if payload is None else payload),
                                text=True, capture_output=True, env={**self.env, **env}, check=True)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_first_line_input_only_and_unknown_missing(self):
        plain = SGR.sub("", self.render())
        self.assertIn("tok 85k/200k (42.5%)", plain)
        self.assertNotIn("585k", plain)
        self.assertIn("tok ?/? (?%)", SGR.sub("", self.render({})))
        zero = {"context_window": {"total_input_tokens": 0, "context_window_size": 1000, "used_percentage": 0}}
        self.assertIn("tok 0/1k (0%)", SGR.sub("", self.render(zero)))

    def test_render_day_ttl_git_payload_snapshot_changes_byte_frozen(self):
        self.append(event("a"))
        original = self.render().splitlines()[1]
        for advance in (300, 3600, 86400):
            payload = {**self.payload, "prompt_cache": {"hit_ratio": .12, "requests": 200,
                       "misses": 199, "ttl": "expired"}, "workspace": {"current_dir": str(ROOT)}}
            refreshed = self.render(payload, CACHE_HISTORY_NOW=str(BASE / 1000 + advance))
            self.assertEqual(original, refreshed.splitlines()[1])
        for i in range(10):
            self.append(event("a", seq=i+2))
            self.assertEqual(original, self.render().splitlines()[1])
        h = History(self.path / "history.sqlite3")
        h.maintain(); h.close()
        self.assertEqual(original, self.render().splitlines()[1])

    def test_real_git_untracked_and_branch_changes_do_not_touch_history(self):
        repo=self.path/"git-fixture"
        subprocess.run(["git","init","-q",str(repo)],check=True,capture_output=True)
        self.append(event("a"))
        payload={**self.payload,"workspace":{"current_dir":str(repo)}}
        before=self.render(payload).splitlines()[1]
        (repo/"untracked.txt").write_text("fixture only")
        subprocess.run(["git","-C",str(repo),"symbolic-ref","HEAD","refs/heads/fixture-other"],check=True,capture_output=True)
        after=self.render(payload,CACHE_HISTORY_NOW=str(BASE/1000+86400)).splitlines()[1]
        self.assertEqual(before,after)

    def test_twelve_cells_at_narrow_columns_ansi_styles_preserved(self):
        # Fixed 12 consecutive buckets with all 8 real heights, 0% and 100%.
        for i, pct in enumerate((0, 80, 82.5, 85, 87.5, 90, 92.5, 95, 97.5, 100, 95, 100)):
            self.append(event(str(i), BASE - (11-i)*300000, read=round(pct*100), uncached=10000-round(pct*100)))
        for width in (10, 12, 20, 26, 30, 80, 140):
            plain = SGR.sub("", self.render(COLUMNS=str(width))).splitlines()
            cache = "".join(plain[1:])
            self.assertTrue(cache.startswith("▲ hit "))
            bars = re.findall(r"[▁▂▃▄▅▆▇█]", cache)
            self.assertEqual(len(bars), 12)
            self.assertEqual("".join(bars), "▁▁▂▃▄▅▆▇██▇█")
        truecolor = self.render()
        self.assertIn("\x1b[38;2;245;201;181m▁", truecolor)
        self.assertIn("\x1b[38;2;216;238;237m█", truecolor)
        indexed = self.render(COLORTERM="", TERM_PROGRAM="", TERM="xterm-256color")
        self.assertIn("\x1b[38;5;223m▁", indexed)
        self.assertIn("\x1b[38;5;152m█", indexed)
        self.assertNotIn("38;2", indexed)

    def test_unused_history_padding_keeps_lowest_glyph_color_and_no_token_weight(self):
        self.append(event("first", BASE-11*300000, read=100, uncached=0),
                    event("latest", read=100, uncached=0))
        output = self.render()
        row = output.splitlines()[1]
        expected = "▲ hit 100.0%  " + "▁" * 10 + "██"
        self.assertEqual(SGR.sub("", row), expected)
        self.assertEqual(row.count("\x1b[38;2;245;201;181m▁"), 10)
        self.assertNotIn("·", row)
        indexed = self.render(COLORTERM="", TERM_PROGRAM="", TERM="xterm-256color").splitlines()[1]
        self.assertEqual(SGR.sub("", indexed), expected)
        self.assertEqual(indexed.count("\x1b[38;5;223m▁"), 10)
        self.assertNotIn("38;2", indexed)
        for width in (10, 12, 20, 80):
            lines = SGR.sub("", self.render(COLUMNS=str(width))).splitlines()[1:]
            # At narrow widths, line breaks replace the two-column separator.
            self.assertEqual("".join(lines).replace(" ", ""), expected.replace(" ", ""))
            self.assertTrue(all(len(line) <= width-4 for line in lines))
            self.assertEqual(len(re.findall("[▁▂▃▄▅▆▇█]", "".join(lines))), 12)
        self.assertEqual(row, self.render(CACHE_HISTORY_NOW=str(BASE/1000+86400)).splitlines()[1])
        h = History(self.path / "history.sqlite3")
        try:
            view = h.view()
            self.assertEqual(view["codes"], ["-"] * 10 + ["7", "7"])
            self.assertEqual((view["read"], view["write"], view["uncached"]), (200, 0, 0))
        finally:
            h.close()

    def test_real_zero_and_unused_padding_share_glyph_but_not_token_accounting(self):
        self.append(event("zero", BASE-11*300000, read=0, write=100, uncached=0),
                    event("latest", read=100, uncached=0))
        row = self.render().splitlines()[1]
        self.assertEqual(SGR.sub("", row), "▲ hit 50.0%  " + "▁" * 11 + "█")
        self.assertEqual(row.count("\x1b[38;2;245;201;181m▁"), 11)
        h = History(self.path / "history.sqlite3")
        try:
            view = h.view()
            self.assertEqual(view["codes"], ["-"] * 10 + ["0", "7"])
            self.assertEqual((view["read"], view["write"], view["uncached"]), (100, 100, 0))
        finally:
            h.close()

    def test_bad_source_and_locked_db_fail_to_previous_picture(self):
        self.append(event("a"))
        expected = self.render().splitlines()[1]
        self.feed.write_text(self.feed.read_text() + "{broken}\n")
        self.assertEqual(expected, self.render().splitlines()[1])
        import sqlite3
        db = sqlite3.connect(self.path / "history.sqlite3", isolation_level=None)
        db.execute("BEGIN IMMEDIATE")
        try:
            self.assertEqual(expected, self.render().splitlines()[1])
        finally:
            db.execute("ROLLBACK");db.close()

    def test_debug_failure_notice_has_no_payload_or_source_content(self):
        self.append(event("a"));self.render()
        self.feed.write_text(self.feed.read_text()+"{broken}\n")
        result=subprocess.run(["/bin/bash",str(ROOT/"statusline-command.sh")],
                              input=json.dumps(self.payload),text=True,capture_output=True,
                              env={**self.env,"CACHE_HISTORY_DEBUG":"1"},check=True)
        self.assertEqual(result.stderr,"statusline: source/storage unavailable; retained last committed history\n")
        self.assertIn("90.0%",SGR.sub("",result.stdout))

    def test_commit_failure_never_exports_uncommitted_candidate(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        import sqlite3
        self.append(event("a"))
        old=bridge(self.payload,self.env)
        self.append(event("b",BASE+1,read=0,uncached=1000))
        class FailCommit(History):
            @contextmanager
            def transaction(inner):
                inner.db.execute("BEGIN IMMEDIATE")
                try:
                    yield
                finally:
                    inner.db.execute("ROLLBACK")
                raise sqlite3.OperationalError("injected commit failure")
        with patch("cache_history.History",FailCommit):
            actual=bridge(self.payload,self.env)
        self.assertEqual(actual[-2:],old[-2:])
        h=History(self.path/"history.sqlite3")
        self.assertEqual(h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],1)
        h.close()
        self.assertNotEqual(bridge(self.payload,self.env)[-2:],old[-2:])

    def test_corrupt_sidecar_does_not_break_committed_sqlite_readback(self):
        self.append(event("a"))
        expected=bridge(self.payload,self.env)[-2:]
        sidecar=Path(str(self.path/"history.sqlite3")+".view.json")
        for wrong in ([],{"{}":{"codes":["?"]*12}}, {"{}":{"codes":[{}]*12,"percentage":123,"revision":"wrong"}}):
            sidecar.write_text(json.dumps(wrong))
            self.assertEqual(bridge(self.payload,self.env)[-2:],expected)

    def test_narrow_cache_rows_fit_host_reserved_width(self):
        self.append(event("a"))
        for width in (10,12,16,20,26):
            lines=SGR.sub("",self.render(COLUMNS=str(width))).splitlines()[1:]
            self.assertTrue(all(len(line)<=width-4 for line in lines))
            self.assertEqual(len(re.findall("[▁▂▃▄▅▆▇█]","".join(lines))),12)
            self.assertEqual(len(re.findall("[?▁▂▃▄▅▆▇█]","".join(lines))),12)

    def test_no_jq_uses_real_json_not_regex(self):
        self.payload["other"] = {"total_input_tokens": 1234567, "used_percentage": 999}
        self.assertIn("tok 85k/200k (42.5%)", SGR.sub("", self.render()))
        self.assertNotIn("scalar()", (ROOT / "statusline-command.sh").read_text())
        self.assertNotIn("command -v jq", (ROOT / "statusline-command.sh").read_text())

    def test_bad_dependency_retains_original_lowest_placeholder(self):
        # Copy just Bash to simulate a missing installed helper, no installer run.
        script = self.path / "statusline-command.sh"
        shutil.copy2(ROOT / "statusline-command.sh", script)
        result = subprocess.run(["/bin/bash", str(script)], input="{}", text=True,
                                env=self.env, capture_output=True, check=True)
        self.assertIn("▲ hit 0.0%  ▁▁▁▁▁▁▁▁▁▁▁▁", SGR.sub("", result.stdout))

    def test_concurrent_refresh_deduplicates_and_commits_checkpoints(self):
        self.append(*(event(str(i), BASE + i, session_id=str(i%4), read=90, uncached=10) for i in range(100)))
        processes = [subprocess.Popen([sys.executable, str(ROOT / "lib/cache_history.py")],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, env=self.env) for _ in range(8)]
        outputs = []
        for proc in processes:
            out, err = proc.communicate("{}", timeout=20)
            self.assertEqual(proc.returncode, 0);self.assertEqual(err, "")
            outputs.append(out.splitlines()[-2:])
        self.assertEqual(outputs, [outputs[0]] * len(outputs))
        h = History(self.path / "history.sqlite3")
        try:
            self.assertEqual(h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 100)
            checkpoint = h.db.execute("SELECT offset FROM checkpoints").fetchone()[0]
            self.assertEqual(checkpoint, self.feed.stat().st_size)
            self.assertEqual(h.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            h.close()

    def test_kill_before_transaction_commit_replays(self):
        self.append(event("a"))
        code = '''
import os,sys
sys.path.insert(0,sys.argv[1])
from cache_history import History
h=History(sys.argv[2])
with h.transaction():
 h.ingest_file(sys.argv[3],"feed",int(sys.argv[4]),failpoint=lambda phase:os._exit(77) if phase=="before_checkpoint" else None)
'''
        killed = subprocess.run([sys.executable, "-c", code, str(ROOT / "lib"),
                                str(self.path / "history.sqlite3"), str(self.feed), str(BASE+100)], env=self.env)
        self.assertEqual(killed.returncode, 77)
        self.assertIn("90.0%", SGR.sub("", self.render()))
        h = History(self.path / "history.sqlite3")
        self.assertEqual(h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        h.close()

    def test_discovery_all_projects_main_subagents_not_current_only(self):
        root = self.path / "projects"
        a = root / "p1/a.jsonl"; b = root / "p2/b/subagents/agent-c.jsonl"
        a.parent.mkdir(parents=True);b.parent.mkdir(parents=True)
        a.write_text(json.dumps(transcript("a", BASE-11*300000, usage={"cache_read_input_tokens":0,"cache_creation_input_tokens":0,"input_tokens":100}))+"\n")
        b.write_text(json.dumps(transcript("b", usage={"cache_read_input_tokens":100,"cache_creation_input_tokens":0,"input_tokens":0}, session="other"))+"\n")
        paths = discover(root, a)
        self.assertEqual(len(paths), 2)
        plain = SGR.sub("", self.render({**self.payload, "transcript_path": str(a)}))
        self.assertIn("▲ hit 50.0%", plain)
        filtered = SGR.sub("", self.render(CACHE_SCOPE_SESSION="other"))
        self.assertIn("▲ hit 100.0%", filtered)

    def test_advertised_not_yet_created_transcript_does_not_block_shared_history(self):
        self.append(event("a"))
        payload={**self.payload,"transcript_path":str(self.path/"missing-current-session.jsonl")}
        self.assertIn("▲ hit 90.0%",SGR.sub("",self.render(payload)))

    def test_legacy_three_column_log_read_only_not_faked(self):
        legacy = self.path / "legacy.log"
        content = f"{BASE//1000}\t200\t3\n".encode()
        legacy.write_bytes(content)
        plain = SGR.sub("", self.render({**self.payload,"prompt_cache":{"requests":200,"misses":3,"hit_ratio":.96}}, CACHE_LOG=str(legacy)))
        self.assertIn("▲ hit 0.0%", plain)
        self.assertEqual(legacy.read_bytes(), content)

    def test_long_idle_only_new_request_repositions_at_render(self):
        self.append(event("a"));before=self.render().splitlines()[1]
        self.assertEqual(before,self.render(CACHE_HISTORY_NOW=str(BASE/1000+86400)).splitlines()[1])
        self.append(event("b",BASE+86400000,read=100,uncached=0))
        after=SGR.sub("",self.render(CACHE_HISTORY_NOW=str(BASE/1000+86401))).splitlines()[1]
        self.assertEqual(after,"▲ hit 95.0%  " + "▁" * 10 + "▅█")


    def test_five_clock_bucket_gap_shifts_only_one_visible_cell(self):
        self.append(event("before-gap", read=100, uncached=0))
        before = SGR.sub("", self.render()).splitlines()[1].split("  ", 1)[1]
        self.append(event("after-gap", BASE+5*300000, read=0, write=100, uncached=0))
        plain = SGR.sub("", self.render(CACHE_HISTORY_NOW=str(BASE/1000+5*300+100))).splitlines()[1]
        self.assertEqual(plain, "▲ hit 50.0%  " + before[1:] + "▁")
        self.assertEqual(plain, "▲ hit 50.0%  " + "▁"*10 + "█▁")
        frozen = self.render().splitlines()[1]
        self.assertEqual(frozen, self.render(CACHE_HISTORY_NOW=str(BASE/1000+86400)).splitlines()[1])

    def test_legacy_sidecar_axis_cannot_masquerade_as_active_history_on_failure(self):
        from statusline_input import saved_history_fields
        self.append(event("known"));bridge(self.payload,self.env)
        sidecar=Path(str(self.path/"history.sqlite3")+".view.json")
        views=json.loads(sidecar.read_text());views["{}"].pop("axis")
        sidecar.write_text(json.dumps(views))
        self.assertEqual(saved_history_fields(self.env), ["?%", "?"*12])
        # A successful helper rebuilds/publishes the current policy from events.
        self.assertEqual(bridge(self.payload,self.env)[-2:], ["90%", "-"*11+"4"])
        self.assertEqual(saved_history_fields(self.env), ["90%", "-"*11+"4"])


class InstallerFixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit installer fixture ")
        self.path = Path(self.tmp.name)
        self.config = self.path / "config spaces ' quote $ shell"
        self.config.mkdir()
        self.original = {"allowedTools": ["Read"], "statusLine": {"type":"command", "command":"old", "padding":2, "refreshInterval":5}}
        (self.config / "settings.json").write_text(json.dumps(self.original))
        (self.config / "statusline-command.sh").write_text("legacy script unchanged")
        (self.config / "legacy.log").write_text("do not touch")

    def tearDown(self):
        self.tmp.cleanup()

    def test_quoted_command_executes_complete_bundle(self):
        result = install(ROOT, self.config)
        settings = json.loads((self.config / "settings.json").read_text())
        self.assertEqual(settings["allowedTools"], self.original["allowedTools"])
        self.assertEqual(settings["statusLine"]["padding"], 2)
        self.assertEqual(settings["statusLine"]["refreshInterval"], 5)
        self.assertEqual((self.config/"statusline-command.sh").read_text(), "legacy script unchanged")
        self.assertEqual((self.config/"legacy.log").read_text(), "do not touch")
        env = {**os.environ, "HOME": str(self.path), "CLAUDE_CONFIG_DIR": str(self.config),
               "CACHE_TRANSCRIPT_ROOT": str(self.path/"missing"), "CACHE_EVENT_FILE":"",
               "CACHE_HISTORY_DB": str(self.path/"test.sqlite3"),"COLUMNS":"80"}
        rendered = subprocess.run(["/bin/bash","-c",settings["statusLine"]["command"]], input="{}", text=True, capture_output=True, env=env, check=True)
        self.assertIn("▲ hit 0.0%", SGR.sub("",rendered.stdout))
        self.assertTrue(Path(result["backup_settings"]).is_file())
        again = install(ROOT,self.config)
        self.assertTrue(Path(again["backup_bundle"]).is_dir())

    def test_failure_rolls_back_old_bundle_and_exact_settings(self):
        bundle = self.config/"statusline-cockpit"
        bundle.mkdir();(bundle/"marker").write_text("old bundle")
        settings = (self.config/"settings.json").read_bytes()
        for phase in ("staged","activated","configured"):
            def fail(p):
                if p==phase:raise OSError("fixture injected failure")
            with self.assertRaises(OSError):install(ROOT,self.config,fail)
            self.assertEqual((self.config/"settings.json").read_bytes(),settings)
            self.assertEqual((bundle/"marker").read_text(),"old bundle")
            self.assertEqual((self.config/"legacy.log").read_text(),"do not touch")

    def test_invalid_settings_fail_before_any_new_files(self):
        (self.config/"settings.json").write_text("broken")
        before = sorted(self.config.iterdir())
        with self.assertRaises(ValueError):install(ROOT,self.config)
        self.assertEqual(before,sorted(self.config.iterdir()))


if __name__ == "__main__":
    unittest.main()
