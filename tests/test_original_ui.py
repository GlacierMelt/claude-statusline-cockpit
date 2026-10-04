"""Preserve the real installed UI: roles, tenths, placeholders and all 12 cells."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from test_history import BASE, ROOT, event
from cache_history import bridge, History

SGR = re.compile(r"\x1b\[[0-9;]*m")
PALETTE = json.loads((ROOT / "tests/fixtures/original-ui-palette.json").read_text())


def visible_styles(text):
    style, out, start = "", [], 0
    for sgr in SGR.finditer(text):
        out.extend((ch, style) for ch in text[start:sgr.start()] if ch != "\n")
        style = sgr.group()
        start = sgr.end()
    out.extend((ch, style) for ch in text[start:] if ch != "\n")
    return out


class OriginalUICase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit-original-ui-")
        self.path = Path(self.tmp.name)
        self.feed = self.path / "feed.jsonl"
        self.feed.write_text("")
        self.env = {**os.environ, "HOME": str(self.path / "home"),
                    "CLAUDE_CONFIG_DIR": str(self.path / "config"),
                    "CACHE_TRANSCRIPT_ROOT": str(self.path / "none"),
                    "CACHE_EVENT_FILE": str(self.feed),
                    "CACHE_HISTORY_DB": str(self.path / "history.sqlite3"),
                    "CACHE_HISTORY_PYTHON": sys.executable,
                    "CACHE_HISTORY_NOW": str((BASE+600000)/1000),
                    "COLORTERM": "truecolor", "TERM_PROGRAM": "", "TERM": "xterm-256color",
                    "COLUMNS": "120", "PYTHONDONTWRITEBYTECODE": "1"}
        for name in ("CACHE_SCOPE_PROJECT", "CACHE_SCOPE_SESSION", "CACHE_SCOPE_CONVERSATION"):
            self.env.pop(name, None)
        self.payload = {"model": {"display_name": "UI Model (1M context)"},
                        "effort": {"level": "high"},
                        "context_window": {"total_input_tokens": 85000,
                                           "context_window_size": 200000, "used_percentage": 42.5},
                        "workspace": {"current_dir": str(self.path)},
                        "cost": {"total_cost_usd": 1.25}}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, **env):
        result = subprocess.run(["/bin/bash", str(ROOT/"statusline-command.sh")],
                                input=json.dumps(self.payload), text=True, capture_output=True,
                                env={**self.env, **env}, check=True)
        self.assertEqual(result.stderr, "")
        rows = result.stdout.splitlines()
        plain = SGR.sub("", result.stdout).splitlines()
        self.assertIn("UI Model", plain[0])
        if int(env.get("COLUMNS", self.env["COLUMNS"])) >= 80:
            self.assertIn("tok 85k/200k (42.5%)", plain[0])
            self.assertIn("$1.25", plain[0])
        # Narrow first-row shedding remains the original design, not a cache fix.
        self.assertEqual(plain[-1], str(self.path))
        return "\n".join(rows[1:-1])

    def write(self, *rows):
        self.feed.write_text("".join(json.dumps(row)+"\n" for row in rows))

    def test_hit_label_uses_original_project_not_installed_review_override(self):
        self.assertEqual(PALETTE["truecolor"]["HIT_LABEL_C"], "\x1b[38;2;44;104;123m")
        self.assertEqual(PALETTE["indexed"]["HIT_LABEL_C"], "\x1b[38;5;248m")
        self.assertNotEqual(PALETTE["truecolor"]["HIT_LABEL_C"], "\x1b[38;2;35;61;77m")
        self.assertNotEqual(PALETTE["indexed"]["HIT_LABEL_C"], "\x1b[38;5;237m")

    def test_original_digit_and_punctuation_roles_on_wide_and_narrow_rows(self):
        self.write(event("known", read=95, uncached=5))
        for width in (10, 12, 16, 20, 26, 80, 140):
            with self.subTest(width=width):
                cache = self.render(COLUMNS=str(width), LC_ALL="C")
                tokens = visible_styles(cache)
                self.assertEqual("".join(ch for ch,_ in tokens[:6]), "▲ hit ")
                self.assertEqual(tokens[0][1], PALETTE["truecolor"]["TRI_C"])
                self.assertTrue(all(style == PALETTE["truecolor"]["HIT_LABEL_C"]
                                    for ch,style in tokens[2:5]))
                self.assertEqual(tokens[6:11], [
                    ("9", PALETTE["truecolor"]["digits"][0]),
                    ("5", PALETTE["truecolor"]["digits"][1]),
                    (".", PALETTE["truecolor"]["dot"]),
                    ("0", PALETTE["truecolor"]["digits"][2]),
                    ("%", PALETTE["truecolor"]["percent"])])
                self.assertEqual(len(re.findall("[▁▂▃▄▅▆▇█]", cache)), 12)
                self.assertNotIn("~", SGR.sub("", cache))
                self.assertNotIn("?", SGR.sub("", cache))

    def test_256_color_label_and_number_keep_original_bold_and_roles(self):
        self.write(event("known", read=95, uncached=5))
        for width in (10, 80):
            cache = self.render(COLUMNS=str(width), COLORTERM="")
            tokens = visible_styles(cache)
            self.assertEqual(tokens[0][1], PALETTE["indexed"]["TRI_C"])
            self.assertTrue(all(style == PALETTE["indexed"]["HIT_LABEL_C"]
                                for ch,style in tokens[2:5]))
            self.assertTrue(all(style == PALETTE["indexed"]["HIT_NUM_C"]
                                for ch,style in tokens[6:11]))
            self.assertNotIn("38;2", cache)
            self.assertEqual(len(re.findall("[▁▂▃▄▅▆▇█]", cache)), 12)

    def test_one_decimal_format_including_true_one_hundred_not_99_9(self):
        for read, write, uncached, pct in ((100,0,0,"100.0%"),(0,0,100,"0.0%"),
                                           (1,2,0,"33.3%"),(90,0,10,"90.0%")):
            with self.subTest(pct=pct):
                # Identities accumulate in the ledger, so select this session.
                self.write(event(pct, read=read, write=write, uncached=uncached, session_id=pct))
                plain = SGR.sub("", self.render(CACHE_SCOPE_SESSION=pct))
                self.assertTrue(plain.startswith("▲ hit "+pct+"  "), plain)
        self.write(event("100-roles", read=100, uncached=0, session_id="100-roles"))
        tokens = visible_styles(self.render(CACHE_SCOPE_SESSION="100-roles"))
        self.assertEqual(tokens[6:12], [
            ("1", PALETTE["truecolor"]["digits"][0]),
            ("0", PALETTE["truecolor"]["digits"][1]),
            ("0", PALETTE["truecolor"]["digits"][2]),
            (".", PALETTE["truecolor"]["dot"]),
            ("0", PALETTE["truecolor"]["digits"][2]),
            ("%", PALETTE["truecolor"]["percent"])])

    def test_no_evidence_original_placeholder_not_invented_in_ledger(self):
        for env, name in (({},"truecolor"),({"COLORTERM":""},"indexed")):
            cache = self.render(**env)
            self.assertEqual(SGR.sub("", cache), "▲ hit 0.0%  "+"▁"*12)
            self.assertEqual(cache.count(PALETTE[name]["blank"]+"▁"), 12)
        raw = bridge(self.payload, self.env)
        self.assertEqual(raw[-2:], ["?%", "?"*12])
        h=History(self.path/"history.sqlite3")
        try:
            with h.transaction():
                self.assertEqual(h.view()["quality"], "unknown")
                self.assertEqual(h.view()["read"], 0)
                self.assertEqual(h.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        finally:
            h.close()

    def test_rightmost_same_bucket_updates_without_moving_any_other_cell(self):
        first = event("first", read=80, uncached=20)
        second = event("second", BASE+1, read=920, uncached=0)
        third = event("third", BASE+2, read=0, uncached=2000)
        self.write(first)
        before = SGR.sub("", self.render()).split("  ",1)[1]
        raw_before = bridge(self.payload,self.env)[-2:]
        self.write(first,second)
        middle = SGR.sub("", self.render()).split("  ",1)[1]
        raw_middle = bridge(self.payload,self.env)[-2:]
        self.write(first,second,third)
        after = SGR.sub("", self.render()).split("  ",1)[1]
        raw_after = bridge(self.payload,self.env)[-2:]
        self.assertEqual((before[-1],middle[-1],after[-1]), ("▁","█","▁"))
        self.assertEqual(before[:-1],middle[:-1])
        self.assertEqual(before[:-1],after[:-1])
        self.assertEqual(raw_before[1][:-1],raw_middle[1][:-1])
        self.assertEqual(raw_before[1][:-1],raw_after[1][:-1])
        self.assertEqual((raw_before[0],raw_middle[0],raw_after[0]), ("80%","98%","33.1%"))
        self.assertEqual(len(before),12)
        stable = self.render()
        self.assertEqual(stable,self.render(CACHE_HISTORY_NOW=str((BASE+86_400_000)/1000)))

    def test_partial_diagnostic_does_not_add_new_ui_tilde_or_question_bars(self):
        self.write(event("known", read=100, uncached=0),
                   event("real-zero", BASE+300000, read=0, write=0, uncached=0))
        cache = self.render()
        self.assertEqual(SGR.sub("", cache), "▲ hit 100.0%  "+"▁"*10+"█▁")
        self.assertEqual(bridge(self.payload,self.env)[-2:], ["~100%", "-"*10+"7?"])
        h=History(self.path/"history.sqlite3")
        try:
            with h.transaction():
                self.assertEqual(h.view()["quality"], "partial")
                self.assertEqual(h.view()["active_bucket_count"], 2)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
