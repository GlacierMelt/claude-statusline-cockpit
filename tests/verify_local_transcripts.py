#!/usr/bin/env python3
"""Opt-in read-only source validation. NEVER stores message bodies or prints IDs.

Requires explicit --root and a NEW --db under a caller-selected test directory.
Not part of the hermetic unit suite, and never uses the installed runtime DB.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from cache_history import BUCKET_MS, History, discover
from cache_sources import STOP_REASONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.db.exists():
        parser.error("validation requires a NEW isolated database; existing state is not overwritten")
    started = time.perf_counter()
    observed = time.time_ns() // 1_000_000
    paths = discover(args.root)
    expected = {}
    versions = {}
    rejected = {}
    repeats = 0
    # Independent arithmetic/keying oracle: no adapter or renderer calls.
    for path, (_, project, scope) in paths:
        with path.open() as source:
            for line in source:
                row = json.loads(line)
                if row.get("type") != "assistant":
                    continue
                message = row.get("message") or {}
                if row.get("isApiErrorMessage") is True or message.get("model") == "<synthetic>":
                    continue
                if message.get("role") != "assistant" or message.get("stop_reason") not in STOP_REASONS:
                    continue
                version = row.get("version")
                if version is not None and (not isinstance(version,str) or not version or len(version)>1024 or any(ord(c)<32 or ord(c)==127 for c in version)):
                    continue
                usage = message.get("usage") or {}
                counts = tuple(usage.get(k) for k in ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens"))
                if not all(type(n) is int and n >= 0 for n in counts):
                    continue
                dt = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                epoch_ms = int(dt.timestamp() * 1000)
                if epoch_ms > observed:
                    continue
                key = (project, row.get("sessionId") or row.get("session_id"), scope, message.get("id"))
                if not key[1] or not key[3]:
                    continue
                version = version or "unreported"
                versions[version] = versions.get(version, 0) + 1
                if key in expected:
                    repeats += 1
                    assert expected[key][1] == counts, "source has conflicting final input usage"
                    continue
                expected[key] = (epoch_ms, counts)
    assert expected, "no completed supported request records found"
    active_ids = sorted({value[0] // BUCKET_MS for value in expected.values()})[-12:]
    pad_count = 12-len(active_ids)
    positions = {bucket: i+pad_count for i,bucket in enumerate(active_ids)}
    per_bucket = [[0, 0, 0] for _ in range(12)]
    for ms, counts in expected.values():
        b = ms // BUCKET_MS
        if b in positions:
            index = positions[b]
            per_bucket[index] = [a+b for a,b in zip(per_bucket[index], counts)]
    weights = [sum(bucket[i] for bucket in per_bucket) for i in range(3)]
    h = History(args.db)
    try:
        with h.transaction():
            for path, (adapter, project, scope) in paths:
                h.ingest_file(path, adapter, observed, project, scope)
            view = h.view()
        actual_count = h.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert actual_count == len(expected), (actual_count, len(expected))
        assert [view["read"], view["write"], view["uncached"]] == weights
        assert [[b["read"], b["write"], b["uncached"]] for b in view["buckets"]] == per_bucket
        pct=(Decimal(weights[0])*100/Decimal(sum(weights))).quantize(Decimal(".1"),rounding=ROUND_HALF_UP)
        expected_pct=str(pct).removesuffix(".0")+"%"
        if any(b["quality"]=="unknown" or (b["quality"]!="padding" and not sum([b["read"], b["write"], b["uncached"]])) for b in view["buckets"]):expected_pct="~"+expected_pct
        assert view["percentage"]==expected_pct
        expected_codes=[]
        from fractions import Fraction
        for i,counts in enumerate(per_bucket):
            if i < pad_count:
                expected_codes.append("-")
            elif not sum(counts):
                expected_codes.append("?")
            else:
                ratio=Fraction(counts[0],sum(counts))*100
                step=(ratio-80)//Fraction(5,2)
                expected_codes.append(str(min(7,max(0,step))))
        if "?" in expected_codes and not expected_pct.startswith("~"):
            expected_pct = "~"+expected_pct
        assert view["active_bucket_count"] == len(active_ids)
        assert [b["bucket"] for b in view["buckets"]] == [None]*pad_count+active_ids
        assert view["codes"]==expected_codes
        for _ in range(3):
            with h.transaction():
                for path, (adapter, project, scope) in paths:
                    h.ingest_file(path, adapter, observed+86_400_000, project, scope)
                assert view == h.view(), "idle replay changed the saved view"
        h.maintain()
        with h.transaction():
            assert view == h.view(), "maintenance changed the saved view"
        rejected = dict(h.db.execute("SELECT code,COUNT(*) FROM issues GROUP BY code").fetchall())
        assert h.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        h.close()
    report = {"validated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(),
              "source": "read-only local contract-validated Claude Code assistant JSONL; no conversation text captured",
              "transcript_files": len(paths), "versions": versions,
              "unique_completed_requests": len(expected), "repeated_completed_records_deduplicated": repeats,
              "issue_counts": rejected, "shared_scope": "all projects, sessions and main/subagent conversations under selected root",
              "axis": view["axis"], "active_bucket_ids": active_ids, "window_anchor_ms": view["anchor_ms"], "window_weights_R_W_U": weights,
              "window_percentage": view["percentage"], "window_codes": "".join(view["codes"]),
              "independent_token_oracle": "passed", "independent_percentage_and_height_oracle": "passed", "idle_day_replay_and_maintenance_freeze": "passed",
              "sqlite_integrity": "ok", "elapsed_seconds": round(time.perf_counter()-started, 3)}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
