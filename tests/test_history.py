import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from cache_history import (BUCKET_MS, History, bridge, context_fields, percentage,
                           ramp_step)
from cache_sources import TOKEN_KEYS, parse_feed, parse_transcript

BASE = 1_700_100_000_000 // BUCKET_MS * BUCKET_MS


def iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def event(eid, ms=BASE, read=90, write=0, uncached=10, **kw):
    row = {"schema": "cockpit-events/v1", "source": "fixture", "source_version": "1",
           "project_id": "p", "session_id": "s", "conversation_scope": "main",
           "event_id": eid, "event_time": iso(ms), "source_sequence": kw.pop("seq", 1),
           "usage": dict(zip(TOKEN_KEYS, (read, write, uncached)))}
    row.update(kw)
    return row


def transcript(eid, ms=BASE, version="2.1.288", stop="end_turn", **kw):
    return {"type": "assistant", "version": version, "sessionId": kw.pop("session", "s"),
            "timestamp": iso(ms), "message": {"id": eid, "role": "assistant",
            "stop_reason": stop, "usage": kw.pop("usage", dict(zip(TOKEN_KEYS, (90, 0, 10))))}, **kw}


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit-test-")
        self.path = Path(self.tmp.name)
        self.h = History(self.path / "history.sqlite3")

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def book(self, *rows, now=BASE + 365 * 86_400_000):
        with self.h.transaction():
            for i, row in enumerate(rows):
                self.h.put(parse_feed(row, i + 1, now))
            return self.h.view()

    def test_weighted_oracle_not_request_or_percent_average(self):
        v = self.book(event("a", read=90, uncached=10), event("b", read=0, write=900, uncached=100))
        self.assertEqual((v["read"], v["write"], v["uncached"]), (90, 900, 110))
        self.assertEqual(v["percentage"], "8.2%")
        self.assertEqual(v["codes"][-1], "0")
        self.assertNotEqual(v["percentage"], "45%")

    def test_sparse_active_history_keeps_old_request_tokens_and_skips_idle_slots(self):
        latest = event("latest", BASE+19641, read=61077, write=116, uncached=4)
        view = self.book(
            event("older-coverage", BASE-12*BUCKET_MS, read=100, uncached=0),
            event("write-a", BASE-8*BUCKET_MS+299272, read=0, write=61041, uncached=4),
            event("zero-input", BASE-35200, read=0, write=0, uncached=0),
            event("write-b", BASE-22986, read=0, write=61077, uncached=4),
            latest,
        )
        self.assertEqual(view["percentage"], "33.4%")
        self.assertEqual((view["read"], view["write"], view["uncached"]), (61177, 122234, 12))
        self.assertEqual(view["codes"], ["-"] * 8 + ["7", "0", "0", "7"])
        self.assertEqual(percentage(61077, 61077+116+4), "99.8%")
        self.assertEqual(view["anchor_ms"], BASE+19641)
        self.assertEqual(view, self.book(latest, now=BASE+86400000))

    def test_randomized_independent_token_oracle(self):
        rng = random.Random(732)
        rows = [event(str(i), BASE + rng.randrange(12) * BUCKET_MS,
                      read=rng.randrange(5000), write=rng.randrange(100), uncached=rng.randrange(500))
                for i in range(500)]
        v = self.book(*rows)
        weights = [0, 0, 0]
        buckets = {}
        for row in rows:
            from cache_sources import timestamp_ms
            b = timestamp_ms(row["event_time"]) // BUCKET_MS
            buckets.setdefault(b, [0, 0, 0])
            for k, key in enumerate(TOKEN_KEYS):
                weights[k] += row["usage"][key]
                buckets[b][k] += row["usage"][key]
        self.assertEqual([v["read"], v["write"], v["uncached"]], weights)
        from decimal import Decimal, ROUND_HALF_UP
        pct=(Decimal(weights[0])*100/Decimal(sum(weights))).quantize(Decimal(".1"),rounding=ROUND_HALF_UP)
        oracle=str(pct).removesuffix(".0")+"%"
        self.assertEqual(v["percentage"],oracle)
        for bucket in v["buckets"]:
            self.assertEqual([bucket["read"], bucket["write"], bucket["uncached"]], buckets[bucket["bucket"]])

    def test_clock_boundaries_and_same_bucket_no_shift(self):
        a = self.book(event("before", BASE - 1))
        b = self.book(event("on", BASE))
        c = self.book(event("after", BASE + 1, read=50, uncached=50))
        self.assertEqual(a["anchor_bucket"] + 1, b["anchor_bucket"])
        self.assertEqual(b["anchor_bucket"], c["anchor_bucket"])
        self.assertEqual(b["codes"][:-1], c["codes"][:-1])
        self.assertEqual(c["buckets"][-1]["read"], 140)

    def test_idle_days_and_duplicate_snapshot_frozen(self):
        row = event("request", BASE)
        v = self.book(row)
        for advance in (300_000, 3_600_000, 86_400_000):
            self.assertEqual(v, self.book(row, now=BASE + advance))
            self.h.maintain()
            with self.h.transaction():
                self.assertEqual(v, self.h.view())

    def test_new_request_after_long_idle_advances_then_only(self):
        a = self.book(event("a"))
        b = self.book(event("b", BASE + 86_400_000, read=100, uncached=0))
        self.assertEqual(b["anchor_bucket"], a["anchor_bucket"] + 288)
        self.assertEqual(b["codes"], a["codes"][1:] + ["7"])
        self.assertEqual(b["percentage"], "95%")

    def test_one_new_bucket_moves_one_slot_after_five_or_many_empty_intervals(self):
        view = self.book(event("first", read=0, uncached=100))
        for i, gap in enumerate((5, 288, 17_280), 1):
            ms = view["anchor_ms"] + gap * BUCKET_MS
            before = view
            view = self.book(event(str(i), ms, read=100, uncached=0))
            self.assertEqual(view["codes"][:-1], before["codes"][1:])
            self.assertEqual(view["active_bucket_count"], i+1)
            self.assertEqual(view["anchor_bucket"], before["anchor_bucket"]+gap)
            self.assertEqual(len(view["buckets"]), 12)
            self.assertEqual(sum(b["quality"] == "padding" for b in view["buckets"]), 11-i)
            self.assertEqual(view, self.book(now=ms+86_400_000))

    def test_user_timeline_0925_0952_1001_advances_one_slot_per_active_bucket(self):
        first = self.book(event("0925", read=61077, write=116, uncached=4))
        second = self.book(event("0952-a", BASE+27*60_000, read=0, write=61845, uncached=4))
        self.assertEqual(second["codes"], first["codes"][1:]+["0"])
        same = self.book(event("0952-b", BASE+27*60_000+30_000, read=61845, write=68, uncached=4))
        self.assertEqual(same["active_bucket_count"], 2)
        self.assertEqual(same["codes"][:-1], second["codes"][:-1])
        third = self.book(event("1001", BASE+36*60_000, read=0, write=61957, uncached=4))
        self.assertEqual(third["codes"], same["codes"][1:]+["0"])
        self.assertEqual(third["codes"][-3], "7")
        self.assertEqual(third["active_bucket_count"], 3)

    def test_full_ring_keeps_latest_twelve_occupied_buckets_not_latest_clock_hour(self):
        rows = [event(str(i), BASE+i*86_400_000, read=100+i, write=i, uncached=1)
                for i in range(14)]
        view = self.book(*rows)
        self.assertEqual([b["bucket"] for b in view["buckets"]],
                         [row_ms//BUCKET_MS for row_ms in (BASE+i*86_400_000 for i in range(2,14))])
        self.assertEqual(view["active_bucket_count"], 12)
        self.assertFalse(any(b["quality"] == "padding" for b in view["buckets"]))
        self.assertEqual((view["read"], view["write"], view["uncached"]),
                         (sum(100+i for i in range(2,14)), sum(range(2,14)), 12))
        next_view = self.book(event("next", BASE+100*86_400_000, read=0, write=200, uncached=0))
        self.assertEqual(next_view["codes"][:-1], view["codes"][1:])
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0], 15)

    def test_new_shared_session_after_idle_advances_one_slot_and_same_bucket_merges(self):
        before = self.book(event("old", session_id="one", read=100, uncached=0))
        after = self.book(event("new", BASE+288*BUCKET_MS, session_id="two", read=0, uncached=100))
        self.assertEqual(after["codes"], before["codes"][1:]+["0"])
        merged = self.book(event("same-clock-bucket", BASE+288*BUCKET_MS+1,
                                 session_id="three", read=100, uncached=0))
        self.assertEqual(merged["active_bucket_count"], 2)
        self.assertEqual(merged["buckets"][-1]["read"], 100)
        self.assertEqual(merged["buckets"][-1]["uncached"], 100)
        with self.h.transaction():
            one = self.h.view({"session_id": "one"})
        self.assertEqual(one["active_bucket_count"], 1)
        self.assertEqual(one["codes"], ["-"]*11+["7"])

    def test_reported_cache_read_after_ttl_or_long_gap_is_still_a_hit(self):
        with self.h.transaction():
            first = transcript("first", usage={"cache_read_input_tokens":100,
                               "cache_creation_input_tokens":0, "input_tokens":0})
            self.h.put(parse_transcript(first, "p", "main", 1, BASE+1))
            before = self.h.view()
            later = transcript("later", BASE+86_400_000, usage={
                "cache_read_input_tokens":100, "cache_creation_input_tokens":0,
                "input_tokens":0, "cache_creation":{"ephemeral_5m_input_tokens":0,
                "ephemeral_1h_input_tokens":0}, "ttl":"expired"})
            self.h.put(parse_transcript(later, "p", "main", 2, BASE+86_400_001))
            after = self.h.view()
        self.assertEqual(after["percentage"], "100%")
        self.assertEqual(after["read"], 200)
        self.assertEqual(after["codes"], before["codes"][1:]+["7"])
        self.assertEqual(after["active_bucket_count"], 2)

    def test_legacy_cached_axis_rebuilds_at_same_revision_without_rebooking(self):
        view = self.book(event("old", read=100, uncached=0),
                         event("new", BASE+288*BUCKET_MS, read=0, uncached=100))
        legacy = dict(view)
        legacy.pop("axis")
        legacy["codes"] = ["-"]*11+["0"]
        legacy["percentage"] = "0%"
        self.h.db.execute("UPDATE views SET data=? WHERE scope_key='{}'", (json.dumps(legacy),))
        with self.h.transaction():
            self.assertEqual(self.h.view(), view)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0], 2)
        self.assertEqual(self.h.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0], view["revision"])
        self.h.close(); self.h=History(self.path/"history.sqlite3")
        self.assertEqual(self.book(now=BASE+365*86_400_000), view)

    def test_coarse_interval_crossing_excluded_active_history_not_fractionally_invented(self):
        self.book(event("base", kind="counter", counter_epoch="e", request_count=1))
        rows = [event(str(i), BASE+i*BUCKET_MS, read=100, uncached=0) for i in range(1,14)]
        self.book(*rows)
        view = self.book(event("coarse", BASE+100*BUCKET_MS, kind="counter", counter_epoch="e",
                               request_count=2, seq=2, read=180, uncached=20))
        self.assertEqual(view["read"], 1100)
        self.assertEqual(view["uncached"], 0)
        self.assertEqual(view["percentage"], "~100%")
        self.assertEqual(view["active_bucket_count"], 12)
        self.assertEqual(view["codes"][-1], "?")

    def test_same_second_multi_session_identity_and_resume(self):
        rows = [event("same", session_id="a"), event("same", session_id="b"),
                event("other", session_id="a")]
        v = self.book(*rows, *rows)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        self.assertEqual(v["read"], 270)
        self.h.close(); self.h = History(self.path / "history.sqlite3")
        self.assertEqual(v, self.book(*rows))

    def test_late_event_no_anchor_rewind(self):
        a = self.book(event("recent", BASE + 10 * BUCKET_MS))
        b = self.book(event("late", BASE + 2 * BUCKET_MS, read=0, uncached=100))
        self.assertEqual(a["anchor_ms"], b["anchor_ms"])
        self.assertEqual(b["buckets"][-2]["uncached"], 100)
        self.assertEqual(b["percentage"], "45%")

    def test_future_rejected_never_clock_released(self):
        first = self.book(event("past"))
        f = self.path / "future.jsonl"
        f.write_text(json.dumps(event("future", BASE + 10 * BUCKET_MS)) + "\n")
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + BUCKET_MS)
            self.assertEqual(first, self.h.view())
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100 * BUCKET_MS)
            self.assertEqual(first, self.h.view())

    def test_future_rotated_replay_remains_quarantined(self):
        first = self.book(event("past"))
        future = event("future", BASE + 10 * BUCKET_MS)
        self.assertEqual(first, self.book(future, now=BASE+100))
        self.assertEqual(first, self.book(future, now=BASE+86400000))
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM quarantined").fetchone()[0], 1)

    def test_submillisecond_before_boundary_does_not_round_into_new_bucket(self):
        from cache_sources import timestamp_ms
        text = iso(BASE-1).replace(".999000+00:00", ".999999+00:00")
        self.assertEqual(timestamp_ms(text), BASE-1)

    def test_observed_time_read_clock_can_advance_during_ingest(self):
        f = self.path / "feed.jsonl"
        f.write_text(json.dumps(event("a", BASE+1))+"\n")
        with self.h.transaction():
            self.h.ingest_file(f, "feed", lambda: BASE+2)
            self.assertEqual(self.h.view()["anchor_ms"], BASE+1)

    def test_stream_final_replacement_not_chunk_sum(self):
        a = event("stream", kind="stream_start", seq=1)
        a["usage"] = {"input_tokens": 10, "cache_read_input_tokens": 90}
        b = event("stream", kind="stream_delta", seq=2)
        b["usage"] = {"cache_creation_input_tokens": 10, "output_tokens": 999}
        stop = event("stream", BASE + 1, kind="stream_stop", seq=3)
        stop["usage"] = {}
        self.assertEqual(self.book(a, b)["percentage"], "?%")
        v = self.book(stop, stop)
        self.assertEqual((v["read"], v["write"], v["uncached"]), (90, 10, 10))
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM pending").fetchone()[0], 0)

    def test_transcript_repeats_and_stop_none(self):
        with self.h.transaction():
            self.h.put(parse_transcript(transcript("msg", stop=None), "p", "main", 1, BASE + 100))
            self.assertEqual(self.h.view()["percentage"], "?%")
            self.h.put(parse_transcript(transcript("msg", ms=BASE + 1), "p", "main", 2, BASE + 100))
            self.h.put(parse_transcript(transcript("msg", ms=BASE + 2), "p", "main", 3, BASE + 100))
            v = self.h.view()
        self.assertEqual(v["read"], 90)
        self.assertEqual(v["anchor_ms"], BASE + 1)

    def test_malformed_version_metadata_and_invalid_usage_fail_closed(self):
        for version in ({}, [], True, 289, "", "2.1.289\n"):
            issue = parse_transcript(transcript("msg", version=version), "p", "main", 1, BASE)
            self.assertEqual(issue.code, "invalid_transcript_version")
        for value in (-1, True, "10", None, 1.5):
            row = event("bad", read=value)
            self.assertEqual(parse_feed(row, 1, BASE).code, "invalid_usage")

    def test_more_than_600_events_are_never_tail_trimmed(self):
        v=self.book(*(event(str(i),BASE+i,read=90,uncached=10) for i in range(701)))
        self.h.maintain()
        with self.h.transaction():
            self.assertEqual(self.h.view(),v)
        self.assertEqual(v["read"],701*90)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],701)

    def test_counter_missing_baseline_and_independent_epochs(self):
        baseline = event("base", read=1000, uncached=100, kind="counter", counter_epoch="e1", request_count=10, seq=1)
        self.assertEqual(self.book(baseline)["percentage"], "?%")
        nextrow = event("next", BASE + 1, read=1090, uncached=110, kind="counter", counter_epoch="e1", request_count=11, seq=2)
        v = self.book(nextrow)
        self.assertEqual((v["read"], v["uncached"]), (90, 10))
        other = event("base", read=10, uncached=10, session_id="other", kind="counter", counter_epoch="e1", request_count=1)
        self.assertEqual(v, self.book(other))
        reset = event("reset", read=0, uncached=0, kind="counter", counter_epoch="e2", request_count=0)
        self.assertEqual(v, self.book(reset))

    def test_counter_decrease_and_out_of_order_not_reset(self):
        self.book(event("base", read=100, uncached=10, kind="counter", counter_epoch="e", request_count=1, seq=10))
        self.book(event("old", BASE - 1, read=50, uncached=10, kind="counter", counter_epoch="e", request_count=1, seq=9))
        self.book(event("reset", BASE + 1, read=1, uncached=1, kind="counter", counter_epoch="e", request_count=0, seq=11))
        v = self.book(event("new", BASE + 2, read=190, uncached=20, kind="counter", counter_epoch="e", request_count=2, seq=12))
        self.assertEqual(v["read"], 90)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM counters").fetchone()[0], 1)

    def test_counter_repeat_does_not_move_baseline_time(self):
        self.book(event("base", read=100, uncached=10, kind="counter", counter_epoch="e", request_count=1))
        self.book(event("repeat", BASE + 10 * BUCKET_MS, read=100, uncached=10, kind="counter", counter_epoch="e", request_count=1, seq=2))
        self.assertEqual(self.h.db.execute("SELECT event_ms FROM counters").fetchone()[0], BASE)

    def test_cross_bucket_counter_coarse_not_exact_bars(self):
        self.book(event("base", kind="counter", counter_epoch="e", request_count=1))
        v = self.book(event("new", BASE + BUCKET_MS, read=180, uncached=20, kind="counter", counter_epoch="e", request_count=2, seq=2))
        self.assertEqual(v["codes"][-2:], ["-", "?"])
        self.assertEqual(v["percentage"], "~90%")
        self.assertEqual(v["buckets"][-1]["read"], 0)

    def test_coarse_counter_across_long_idle_not_differenced_from_zero(self):
        self.book(event("base", kind="counter", counter_epoch="e", request_count=1, read=1_000_000))
        v = self.book(event("new", BASE + 13 * BUCKET_MS, kind="counter", counter_epoch="e", request_count=2, seq=2, read=1_000_090, uncached=20))
        self.assertEqual(v["percentage"], "~90%")
        self.assertEqual(v["read"], 90)
        self.assertEqual(v["codes"], ["-"] * 11 + ["?"])
        self.assertEqual(self.h.db.execute("SELECT read_tokens FROM counters").fetchone()[0], 1_000_090)

    def test_counter_duplicate_replay_not_diagnosed_as_new(self):
        base = event("base",kind="counter",counter_epoch="e",request_count=1,seq=1)
        nextrow = event("new",BASE+1,read=180,uncached=20,kind="counter",counter_epoch="e",request_count=2,seq=2)
        v = self.book(base,nextrow)
        self.assertEqual(v,self.book(base,nextrow))
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0],1)
        # The stale baseline is diagnosed, but repeating the booked final delta
        # itself must never create a second event or diagnostic.
        count=self.h.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        self.assertEqual(v,self.book(nextrow))
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0],count)

    def test_coverage_idle_zero_and_hundred_are_distinct(self):
        self.book(event("old", BASE - 11 * BUCKET_MS, read=0, uncached=100))
        v = self.book(event("new", read=100, uncached=0))
        self.assertEqual(v["codes"], ["-"] * 10 + ["0", "7"])
        self.assertEqual(v["percentage"], "50%")
        self.assertEqual(percentage(0, 100), "0%")
        self.assertEqual(percentage(100, 100), "100%")
        self.assertEqual(percentage(0, 0), "?%")

    def test_eight_step_edges(self):
        for pct, step in ((0, 0), (80, 0), (82.49, 0), (82.5, 1), (85, 2),
                          (87.5, 3), (90, 4), (92.5, 5), (95, 6), (97.5, 7), (100, 7)):
            self.assertEqual(ramp_step(round(pct * 100), 10000), step)

    def test_atomic_checkpoint_and_interruption_replay(self):
        f = self.path / "feed.jsonl"
        f.write_text(json.dumps(event("a")) + "\n")
        for phase in ("after_event", "before_checkpoint"):
            def fail(p):
                if p == phase:
                    raise RuntimeError("interrupted")
            with self.assertRaises(RuntimeError):
                with self.h.transaction():
                    self.h.ingest_file(f, "feed", BASE + 100, failpoint=fail)
            self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0], 0)
            self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_incremental_partial_line_rotation_and_truncate(self):
        f = self.path / "feed.jsonl"
        row = json.dumps(event("b", BASE + 1))
        f.write_text(json.dumps(event("a")) + "\n" + row[:40])
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100)
        with f.open("a") as stream:
            stream.write(row[40:] + "\n")
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        f.write_text(json.dumps(event("c", BASE + 2)) + "\n")
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        rotated = self.path / "rotate"
        rotated.write_text(json.dumps(event("c", BASE + 2)) + "\n")
        os.replace(rotated, f)
        with self.h.transaction():
            self.h.ingest_file(f, "feed", BASE + 100)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)

    def test_malformed_json_never_advances_checkpoint(self):
        f = self.path / "feed.jsonl"
        f.write_text(json.dumps(event("a")) + "\n{bad}\n")
        with self.assertRaises(ValueError):
            with self.h.transaction():
                self.h.ingest_file(f, "feed", BASE + 100)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0], 0)

    def test_scopes_same_prefix_and_cells(self):
        self.book(event("a", BASE - 11 * BUCKET_MS, read=0, uncached=100, project_id="a"),
                  event("b", read=100, uncached=0, project_id="b"))
        with self.h.transaction():
            v = self.h.view(); a = self.h.view({"project_id": "a"})
        self.assertEqual(v["percentage"], "50%")
        self.assertEqual(a["percentage"], "0%")
        self.assertEqual(a["anchor_ms"], BASE - 11 * BUCKET_MS)
        self.assertEqual(a["codes"][-1], "0")

    def test_compaction_preserves_old_filtered_history_and_freeze(self):
        self.book(event("old", BASE - 100 * BUCKET_MS, project_id="old"), event("new", project_id="new"))
        with self.h.transaction():
            before = self.h.view({"project_id": "old"})
            all_before = self.h.view()
        self.h.maintain()
        with self.h.transaction():
            self.assertEqual(before, self.h.view({"project_id": "old"}))
            self.assertEqual(all_before, self.h.view())
        self.assertEqual(self.h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_zero_token_real_request_has_unknown_ratio(self):
        v = self.book(event("zero", read=0, write=0, uncached=0))
        self.assertEqual(v["percentage"], "?%")
        self.assertEqual(v["codes"][-1], "?")
        self.assertEqual(v["anchor_ms"], BASE)


class ContextCase(unittest.TestCase):
    def test_official_percent_and_current_input_only(self):
        p = {"context_window": {"used_percentage": 42.5, "context_window_size": 200000,
             "total_input_tokens": 999999, "total_output_tokens": 900000,
             "current_usage": dict(zip(TOKEN_KEYS, (7000, 2000, 1000)))}}
        self.assertEqual(context_fields(p), ("10000", "200000", "42.5", "43"))

    def test_zero_missing_and_legacy_fallback(self):
        self.assertEqual(context_fields({}), ("", "", "?", ""))
        self.assertEqual(context_fields({"context_window": {"used_percentage": 0}}), ("", "", "0", "0"))
        self.assertEqual(context_fields({"context_window": {"total_input_tokens": 100,
             "total_output_tokens": 9999, "context_window_size": 1000}}), ("100", "1000", "10", "10"))
        self.assertEqual(context_fields({"context_window": {"total_input_tokens": 0,
             "context_window_size": 1000}}), ("0", "1000", "0", "0"))


if __name__ == "__main__":
    unittest.main()
