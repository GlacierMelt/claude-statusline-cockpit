#!/usr/bin/env python3
"""Request-ledger and status-line bridge (Python stdlib + transactional SQLite).

Nothing in view(), maintenance(), or payload context handling advances time.
Only newly committed, identifiable request usage can change a saved view.
One occupied five-minute bucket is one slot; empty clock intervals are skipped.
A view-policy upgrade can rebuild the projection once from the retained ledger.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from cache_sources import (Excluded, Issue, Record, TOKEN_KEYS, integer, parse_feed,
                           parse_transcript, token_usage, transcript_exclusion_reason)
from statusline_input import VIEW_AXIS, context_fields, history_fields, input_fields, valid_view

BUCKET_MS = 300_000
BUCKETS = 12
SCHEMA_VERSION = 1
TRANSCRIPT_FILTER_VERSION = 1
# Bump when the accepted record CONTRACT changes, never per Claude release.
# Each known path replays once under this policy to recover past rejected rows.
TRANSCRIPT_CONTRACT_POLICY = 1
RECOVERABLE_TRANSCRIPT_ISSUES = frozenset((
    "unsupported_transcript_version", "invalid_transcript_version",
    "missing_completion", "unsupported_stop_reason", "invalid_assistant",
    "missing_usage", "invalid_usage", "missing_identity", "invalid_identity",
    "conflicting_session_identity", "invalid_event_time", "missing_event_time",
))
UNKNOWN_VIEW = {"axis": VIEW_AXIS, "active_bucket_count": 0, "anchor_ms": None, "anchor_bucket": None, "percentage": "?%",
                "codes": ["?"] * BUCKETS, "buckets": [], "read": 0, "write": 0,
                "uncached": 0, "revision": 0, "quality": "unknown"}

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('revision',0);
CREATE TABLE IF NOT EXISTS events (
 source TEXT NOT NULL, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
 conversation_scope TEXT NOT NULL, event_id TEXT NOT NULL,
 schema_version TEXT NOT NULL, source_version TEXT NOT NULL,
 event_ms INTEGER NOT NULL, observed_ms INTEGER NOT NULL, sequence INTEGER NOT NULL,
 counter_epoch TEXT NOT NULL, read_tokens INTEGER NOT NULL,
 write_tokens INTEGER NOT NULL, uncached_tokens INTEGER NOT NULL,
 interval_start_ms INTEGER, quality TEXT NOT NULL,
 PRIMARY KEY(source,project_id,session_id,conversation_scope,event_id));
CREATE INDEX IF NOT EXISTS events_time ON events(event_ms);
CREATE TABLE IF NOT EXISTS checkpoints (
 path TEXT PRIMARY KEY, device INTEGER NOT NULL, inode INTEGER NOT NULL,
 offset INTEGER NOT NULL, sequence INTEGER NOT NULL,
 head_length INTEGER NOT NULL, head_hash TEXT NOT NULL, tail_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending (
 source TEXT NOT NULL, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
 conversation_scope TEXT NOT NULL, event_id TEXT NOT NULL,
 usage TEXT NOT NULL, event_ms INTEGER NOT NULL, sequence INTEGER NOT NULL,
 PRIMARY KEY(source,project_id,session_id,conversation_scope,event_id));
CREATE TABLE IF NOT EXISTS counters (
 source TEXT NOT NULL, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
 conversation_scope TEXT NOT NULL, epoch TEXT NOT NULL,
 sequence INTEGER NOT NULL, event_ms INTEGER NOT NULL, request_count INTEGER NOT NULL,
 read_tokens INTEGER NOT NULL, write_tokens INTEGER NOT NULL, uncached_tokens INTEGER NOT NULL,
 PRIMARY KEY(source,project_id,session_id,conversation_scope,epoch));
CREATE TABLE IF NOT EXISTS issues (
 source_path TEXT NOT NULL, sequence INTEGER NOT NULL, code TEXT NOT NULL,
 event_ms INTEGER, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
 conversation_scope TEXT NOT NULL,
 PRIMARY KEY(source_path,sequence,code));
CREATE TABLE IF NOT EXISTS quarantined (
 source TEXT NOT NULL, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
 conversation_scope TEXT NOT NULL, event_id TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(source,project_id,session_id,conversation_scope,event_id));
CREATE TABLE IF NOT EXISTS views (scope_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resolved_issues (
 source_path TEXT NOT NULL, sequence INTEGER NOT NULL, code TEXT NOT NULL,
 event_id TEXT NOT NULL, resolved_ms INTEGER NOT NULL, policy INTEGER NOT NULL,
 resolution TEXT NOT NULL,
 PRIMARY KEY(source_path,sequence,code));
CREATE TABLE IF NOT EXISTS transcript_replays (
 source_path TEXT PRIMARY KEY, policy INTEGER NOT NULL);
"""


def scope_key(scope):
    return json.dumps(scope, sort_keys=True, separators=(",", ":"))


def selection(scope, alias=""):
    prefix = alias + "." if alias else ""
    parts, values = [], []
    for key in ("project_id", "session_id", "conversation_scope"):
        if scope.get(key):
            parts.append(prefix + key + "=?")
            values.append(scope[key])
    return (" AND ".join(parts) or "1=1"), values


def percentage(read, total):
    if total <= 0:
        return "?%"
    tenths = (read * 1000 * 2 + total) // (2 * total)
    whole, dec = divmod(tenths, 10)
    return f"{whole}.{dec}%" if dec else f"{whole}%"


def ramp_step(read, total):
    # int(floor((p - 80)/2.5)), all arithmetic exact; clamp 100% to slot 7.
    return max(0, min(7, (read * 100 * 2 - total * 160) // (total * 5)))


class History:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Create privately BEFORE WAL/SHM files inherit the database mode.
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            self.db.close()
            raise ValueError("unsupported_history_schema")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(DDL)
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def issue(self, path, seq, problem):
        self.db.execute("INSERT OR IGNORE INTO issues VALUES (?,?,?,?,?,?,?)",
                        (str(path), seq, problem.code, problem.event_ms,
                         problem.project_id, problem.session_id, problem.conversation_scope))

    def quarantine(self, key, reason):
        """Exclude an identity; never delete an already-retained ledger row.

        Revision is also a projection revision: isolating a previously counted
        event must invalidate every saved scope, even though no request arrived.
        A new client message alone never advances a revision or the picture.
        """
        inserted = self.db.execute("INSERT OR IGNORE INTO quarantined VALUES (?,?,?,?,?,?)",
                                   (*key, reason)).rowcount
        if not inserted:
            return False
        self.db.execute("DELETE FROM pending WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", key)
        if self.db.execute("SELECT 1 FROM events WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", key).fetchone():
            self.db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return True

    def reconcile_transcript_exclusions(self, sources, observed_ms):
        """One-time source-evidenced repair of legacy false client events.

        Only explicit client markers are classified. Ordinary zero-usage and
        stop_sequence records remain valid. Checkpoints, usage and source files
        are never rewritten; malformed/incomplete sources cannot finish a repair.
        Call inside the same transaction as ingestion and view rebuilding.
        """
        policy = self.db.execute("SELECT value FROM meta WHERE key='transcript_filter'").fetchone()
        if policy and policy[0] >= TRANSCRIPT_FILTER_VERSION:
            return 0
        count = 0
        for path, (adapter, project, scope) in sources:
            if adapter != "transcript":
                continue
            with Path(path).open("rb") as stream:
                for sequence, line in enumerate(stream, 1):
                    if not line.endswith(b"\n"):
                        break
                    row = json.loads(line)
                    if not transcript_exclusion_reason(row):
                        continue
                    seen_ms = observed_ms() if callable(observed_ms) else observed_ms
                    rec = parse_transcript(row, project, scope, sequence, seen_ms)
                    if isinstance(rec, Excluded):
                        count += int(self.quarantine(rec.key, rec.reason))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('transcript_filter',?)",
                        (TRANSCRIPT_FILTER_VERSION,))
        return count

    def put(self, rec, path="<feed>", failpoint=None):
        if isinstance(rec, Excluded):
            self.quarantine(rec.key, rec.reason)
            return False
        if isinstance(rec, Issue):
            self.issue(path, 0, rec)
            return False
        if rec is None:
            return False
        if self.db.execute("SELECT 1 FROM quarantined WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key).fetchone():
            return False
        old = self.db.execute("SELECT * FROM events WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key).fetchone()
        if old:
            if rec.kind == "request" and tuple(rec.usage[k] for k in TOKEN_KEYS) != (old["read_tokens"], old["write_tokens"], old["uncached_tokens"]):
                self.issue(path, rec.sequence, Issue("conflicting_final_usage", rec.event_ms,
                           rec.project_id, rec.session_id, rec.conversation_scope))
            return False
        if rec.event_ms > rec.observed_ms:
            self.issue(path, rec.sequence, Issue("future_event", rec.event_ms,
                       rec.project_id, rec.session_id, rec.conversation_scope))
            self.db.execute("INSERT OR IGNORE INTO quarantined VALUES (?,?,?,?,?,?)",
                            (*rec.key, "future_event"))
            # Even rotation/replay after wall time catches up cannot release it.
            return False
        if rec.kind == "counter":
            return self._counter(rec, path, failpoint)
        if rec.kind.startswith("stream_"):
            pending = self.db.execute("SELECT * FROM pending WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key).fetchone()
            if pending and rec.sequence <= pending["sequence"]:
                return False
            usage = json.loads(pending["usage"]) if pending else {}
            # Stream usage is cumulative/replacement, NEVER additive per chunk.
            usage.update(rec.usage)
            if rec.kind != "stream_stop":
                self.db.execute("INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?,?,?,?)",
                                (*rec.key, json.dumps(usage), rec.event_ms, rec.sequence))
                return False
            try:
                usage = token_usage(usage)
            except ValueError:
                self.issue(path, rec.sequence, Issue("incomplete_stream_usage", rec.event_ms,
                           rec.project_id, rec.session_id, rec.conversation_scope))
                return False
        else:
            usage = rec.usage
        return self._insert(rec, usage, None, "exact", failpoint)

    def _insert(self, rec, usage, start, quality, failpoint):
        inserted = self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (*rec.key, rec.schema_version, rec.source_version,
                                   rec.event_ms, rec.observed_ms, rec.sequence, rec.counter_epoch,
                                   *(usage[k] for k in TOKEN_KEYS), start, quality)).rowcount
        if inserted:
            self.db.execute("DELETE FROM pending WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key)
            self.db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
            if failpoint:
                failpoint("after_event")
        return bool(inserted)

    def _counter(self, rec, path, failpoint):
        key = (*rec.key[:4], rec.counter_epoch)
        old = self.db.execute("SELECT * FROM counters WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND epoch=?", key).fetchone()
        current = tuple(rec.usage[k] for k in TOKEN_KEYS)
        if old:
            previous = (old["read_tokens"], old["write_tokens"], old["uncached_tokens"])
            if current == previous and rec.request_count == old["request_count"]:
                return False  # redraw/replay does not alter diagnostics/baseline
            code = None
            if rec.sequence <= old["sequence"] or rec.event_ms < old["event_ms"]:
                code = "out_of_order_counter"
            if any(a < b for a, b in zip(current, previous)) or rec.request_count < old["request_count"]:
                code = code or "counter_decrease_requires_new_epoch"
            if code:
                self.issue(path, rec.sequence, Issue(code, rec.event_ms, rec.project_id,
                                                   rec.session_id, rec.conversation_scope))
                return False
            if rec.request_count == old["request_count"]:
                if current != previous:
                    self.issue(path, rec.sequence, Issue("counter_without_new_request", rec.event_ms,
                               rec.project_id, rec.session_id, rec.conversation_scope))
                return False  # also retain the original baseline TIME
            usage = dict(zip(TOKEN_KEYS, (a - b for a, b in zip(current, previous))))
            same = old["event_ms"] // BUCKET_MS == rec.event_ms // BUCKET_MS
            changed = self._insert(rec, usage, old["event_ms"], "exact" if same else "coarse", failpoint)
        else:
            changed = False  # Missing baseline is never treated as counter=0.
        self.db.execute("INSERT OR REPLACE INTO counters VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (*key, rec.sequence, rec.event_ms, rec.request_count, *current))
        return changed

    def resolve_transcript_issues(self, path, sequence, rec):
        """Retain raw diagnostics and resolve only source-matched, booked usage.

        Path/line alone is unsafe after rotation. Also match original source
        time and scope; require the complete identity's stored final counts.
        Never resolve conflicting usage, quarantined future events or failures.
        """
        if not isinstance(rec, Record) or rec.kind != "request":
            return 0
        if self.db.execute("SELECT 1 FROM quarantined WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key).fetchone():
            return 0
        event = self.db.execute("SELECT read_tokens,write_tokens,uncached_tokens FROM events WHERE source=? AND project_id=? AND session_id=? AND conversation_scope=? AND event_id=?", rec.key).fetchone()
        if not event or tuple(event) != tuple(rec.usage[k] for k in TOKEN_KEYS):
            return 0
        issues = self.db.execute("""SELECT i.code FROM issues i WHERE
            i.source_path=? AND i.sequence=? AND i.event_ms=?
            AND i.project_id=? AND i.session_id=? AND i.conversation_scope=?
            AND NOT EXISTS (SELECT 1 FROM resolved_issues r WHERE
                r.source_path=i.source_path AND r.sequence=i.sequence AND r.code=i.code)""",
            (str(path), sequence, rec.event_ms, rec.project_id, rec.session_id,
             rec.conversation_scope)).fetchall()
        resolved = 0
        for issue in issues:
            if issue[0] not in RECOVERABLE_TRANSCRIPT_ISSUES:
                continue
            self.db.execute("INSERT OR IGNORE INTO resolved_issues VALUES (?,?,?,?,?,?,?)",
                (str(path), sequence, issue[0], rec.event_id, rec.observed_ms,
                 TRANSCRIPT_CONTRACT_POLICY, "source_validated_complete_usage"))
            resolved += 1
        if resolved:
            # Projection revision, not a request count. Invalidate all saved
            # scopes/sidecars safely even when a deduped replay books no tokens.
            self.db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
        return resolved

    def ingest_file(self, path, adapter, observed_ms, project="", scope="main", failpoint=None):
        path = Path(path).resolve()
        with path.open("rb") as stream:
            st = os.fstat(stream.fileno())
            old = self.db.execute("SELECT * FROM checkpoints WHERE path=?", (str(path),)).fetchone()
            offset = seq = 0
            policy = self.db.execute("SELECT policy FROM transcript_replays WHERE source_path=?", (str(path),)).fetchone() if adapter == "transcript" else None
            replay = adapter == "transcript" and (not policy or policy[0] < TRANSCRIPT_CONTRACT_POLICY)
            if not replay and old and (old["device"], old["inode"]) == (st.st_dev, st.st_ino) and st.st_size >= old["offset"]:
                head = stream.read(old["head_length"])
                stream.seek(max(0, old["offset"] - 4096))
                tail = stream.read(min(4096, old["offset"]))
                if digest(head) == old["head_hash"] and digest(tail) == old["tail_hash"]:
                    offset, seq = old["offset"], old["sequence"]
            stream.seek(offset)
            while stream.tell() < st.st_size:
                line = stream.readline()
                if stream.tell() > st.st_size or not line or not line.endswith(b"\n"):
                    break  # incomplete final line is retried at the same offset
                row = json.loads(line)  # malformed complete JSON aborts the transaction
                seq += 1
                seen_ms = observed_ms() if callable(observed_ms) else observed_ms
                rec = (parse_transcript(row, project, scope, seq, seen_ms)
                       if adapter == "transcript" else parse_feed(row, seq, seen_ms))
                if isinstance(rec, Issue):
                    self.issue(path, seq, rec)
                else:
                    self.put(rec, str(path), failpoint)
                    if adapter == "transcript":
                        self.resolve_transcript_issues(path, seq, rec)
                offset = stream.tell()
            # Detect an in-place truncate while reading; never checkpoint across it.
            after = os.fstat(stream.fileno())
            if after.st_size < st.st_size:
                raise ValueError("source_truncated_during_read")
            stream.seek(0)
            head_len = min(offset, 4096)
            head = stream.read(head_len)
            stream.seek(max(0, offset - 4096))
            tail = stream.read(min(offset, 4096))
            if failpoint:
                failpoint("before_checkpoint")
            self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?,?,?)",
                            (str(path), after.st_dev, after.st_ino, offset, seq,
                             head_len, digest(head), digest(tail)))
            if adapter == "transcript":
                self.db.execute("INSERT OR REPLACE INTO transcript_replays VALUES (?,?)",
                                (str(path), TRANSCRIPT_CONTRACT_POLICY))

    def view(self, scope=None):
        scope = scope or {}
        where, args = selection(scope)
        revision = self.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        saved = self.db.execute("SELECT * FROM views WHERE scope_key=?", (scope_key(scope),)).fetchone()
        if saved and saved["revision"] == revision:
            try:
                cached = json.loads(saved["data"])
            except (TypeError, ValueError):
                cached = None
            if valid_view(cached):
                return cached
        # Rebuild a legacy wall-clock projection once, even at the same event
        # revision. Never delete requests, clear the ledger or re-ingest it.
        # The global revision may change from another scope; the picture is still
        # recomputed exclusively from this scope's events, never from now/TTL.
        where, args = selection(scope, "e")
        rows = self.db.execute(f"""SELECT e.* FROM events e WHERE {where}
            AND NOT EXISTS (SELECT 1 FROM quarantined q WHERE
                q.source=e.source AND q.project_id=e.project_id
                AND q.session_id=e.session_id AND q.conversation_scope=e.conversation_scope
                AND q.event_id=e.event_id) ORDER BY e.event_ms,e.event_id""", args).fetchall()
        if not rows:
            result = dict(UNKNOWN_VIEW, revision=revision)
        else:
            result = self._build_view(rows, scope, revision)
        self.db.execute("INSERT OR REPLACE INTO views VALUES (?,?,?)",
                        (scope_key(scope), revision, json.dumps(result, separators=(",", ":"))))
        return result

    def _build_view(self, rows, scope, revision):
        anchor_ms = max(row["event_ms"] for row in rows)
        anchor = anchor_ms // BUCKET_MS
        occupied = sorted({row["event_ms"] // BUCKET_MS for row in rows})
        selected = occupied[-BUCKETS:]
        first = selected[0]
        all_selected = len(occupied) <= BUCKETS
        # No synthetic timestamps for unused history slots. A day-long gap
        # followed by one request appends ONE cell, not 288 idle cells.
        padding = [{"bucket": None, "read": 0, "write": 0, "uncached": 0,
                    "quality": "padding"} for _ in range(BUCKETS-len(selected))]
        active = [{"bucket": b, "read": 0, "write": 0, "uncached": 0,
                   "quality": "exact"} for b in selected]
        by_bucket = {bucket["bucket"]: bucket for bucket in active}
        totals = [0, 0, 0]
        for row in rows:
            b = row["event_ms"] // BUCKET_MS
            if b not in by_bucket:
                continue
            if row["quality"] == "coarse":
                start = row["interval_start_ms"] // BUCKET_MS
                for bucket in active:
                    if start <= bucket["bucket"] <= b:
                        bucket["quality"] = "unknown"
                # A counter delta proves usage, not its per-clock-bucket
                # allocation. Keep one uncertain endpoint slot, never fill
                # idle time or fabricate individual heights. Whole intervals
                # count once when none of their evidence has been excluded.
                if all_selected or start >= first:
                    for i, key in enumerate(("read_tokens", "write_tokens", "uncached_tokens")):
                        totals[i] += row[key]
                continue
            bucket = by_bucket[b]
            for i, (key, col) in enumerate(zip(("read", "write", "uncached"),
                                             ("read_tokens", "write_tokens", "uncached_tokens"))):
                bucket[key] += row[col]
                totals[i] += row[col]
        where, args = selection(scope, "i")
        evidence_hole = False
        # Unresolved quality issues cannot create slots or drive the axis.
        # Recovered issues remain in the audit ledger, not in active coverage.
        for issue in self.db.execute(f"""SELECT i.event_ms FROM issues i WHERE {where}
            AND i.code != 'future_event' AND NOT EXISTS (
                SELECT 1 FROM resolved_issues r WHERE r.source_path=i.source_path
                AND r.sequence=i.sequence AND r.code=i.code)""", args):
            if issue[0] is None:
                continue
            b = issue[0] // BUCKET_MS
            if b in by_bucket:
                by_bucket[b]["quality"] = "unknown"
            elif first <= b <= anchor:
                evidence_hole = True
        buckets = padding + active
        codes = []
        for bucket in buckets:
            total = bucket["read"] + bucket["write"] + bucket["uncached"]
            if bucket["quality"] == "padding":
                codes.append("-")
            elif bucket["quality"] == "unknown" or not total:
                codes.append("?")
            else:
                codes.append(str(ramp_step(bucket["read"], total)))
        pct = percentage(totals[0], sum(totals))
        quality = "partial" if evidence_hole or "?" in codes else "exact"
        if quality == "partial" and pct != "?%":
            pct = "~" + pct
        return {"axis": VIEW_AXIS, "active_bucket_count": len(selected),
                "anchor_ms": anchor_ms, "anchor_bucket": anchor, "percentage": pct,
                "codes": codes, "buckets": buckets, "read": totals[0], "write": totals[1],
                "uncached": totals[2], "revision": revision, "quality": quality}

    def maintain(self):
        # Conservative compaction: retain EVERY event identity/weight, baseline,
        # checkpoint, and saved view (including old explicitly filtered scopes).
        # No wall-clock cutoff, tail-N truncation, or replay-dedup loss.
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self.db.execute("PRAGMA optimize")
        self.db.execute("VACUUM")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def discover(root, explicit_transcript=None, feed=None):
    root = Path(root).expanduser().resolve()
    paths = {}
    if root.is_dir():
        # ALL projects and main/subagent conversations, not just the current cwd.
        for path in sorted(root.glob("*/*.jsonl")):
            paths[path.resolve()] = ("transcript", path.parent.name, "main")
        for path in sorted(root.glob("*/*/subagents/*.jsonl")):
            paths[path.resolve()] = ("transcript", path.parents[2].name,
                                     "subagent:" + path.stem)
    if isinstance(explicit_transcript, (str, Path)) and explicit_transcript:
        path = Path(explicit_transcript).expanduser().resolve()
        # Claude advertises a path before creating its first transcript.
        # That absence must not block other sessions' shared recovered history.
        if path.is_file() and path not in paths:
            try:
                rel = path.relative_to(root)
                project = rel.parts[0]
            except ValueError:
                # Outside the configured root: a stable location identifier.
                project = "path:" + digest(str(path.parent).encode())[:24]
            scope = "subagent:" + path.stem if path.parent.name == "subagents" else "main"
            paths[path] = ("transcript", project, scope)
    if feed:
        paths[Path(feed).expanduser().resolve()] = ("feed", "", "")
    return sorted(paths.items(), key=lambda item: str(item[0]))


def load_fallback(path, scope):
    try:
        all_views = json.loads(Path(path).read_text())
        result = all_views.get(scope_key(scope)) if isinstance(all_views, dict) else None
        if valid_view(result):
            return result
    except (OSError, ValueError):
        pass
    return dict(UNKNOWN_VIEW)


def save_fallback(path, scope, view):
    path = Path(path)
    # Lock the sidecar, merge scopes, and reject stale writes after concurrency.
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = json.loads(path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
        key = scope_key(scope)
        old = existing.get(key)
        if valid_view(old) and old["revision"] > view["revision"]:
            return
        if existing.get(key) == view:
            return
        existing[key] = view
        fd, temp = tempfile.mkstemp(prefix=".history-view-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(existing, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def bridge(payload, env=os.environ):
    scope = {key: env[name] for key, name in (("project_id", "CACHE_SCOPE_PROJECT"),
             ("session_id", "CACHE_SCOPE_SESSION"), ("conversation_scope", "CACHE_SCOPE_CONVERSATION"))
             if env.get(name)}
    config = Path(env.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    root = env.get("CACHE_TRANSCRIPT_ROOT", str(config / "projects"))
    state = Path(env.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    db_path = Path(env.get("CACHE_HISTORY_DB", str(state / "claude-statusline-cockpit/history.sqlite3")))
    fallback = str(db_path) + ".view.json"
    view = load_fallback(fallback, scope)
    history = None
    try:
        # Real observation time is measured while reading, not before discovery.
        # A deterministic injected clock is available only for offline tests.
        observed_ms = (int(Decimal(env["CACHE_HISTORY_NOW"]) * 1000)
                       if env.get("CACHE_HISTORY_NOW") else lambda: time.time_ns() // 1_000_000)
        history = History(db_path)
        with history.transaction():
            sources = discover(root, payload.get("transcript_path"), env.get("CACHE_EVENT_FILE"))
            history.reconcile_transcript_exclusions(sources, observed_ms)
            for path, (adapter, project, conversation) in sources:
                history.ingest_file(path, adapter, observed_ms, project, conversation)
            candidate = history.view(scope)
        # Assign only AFTER the context manager confirms COMMIT. If COMMIT
        # itself fails, retain the pre-transaction fallback, not candidate.
        view = candidate
        save_fallback(fallback, scope, view)
    except (OSError, ValueError, sqlite3.Error, OverflowError, InvalidOperation):
        if env.get("CACHE_HISTORY_DEBUG") == "1":
            print("statusline: source/storage unavailable; retained last committed history", file=sys.stderr)
    finally:
        if history:
            history.close()
    return input_fields(payload) + history_fields(view)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="content-free ledger diagnostics")
    parser.add_argument("--maintain", action="store_true", help="compact SQLite pages without deleting history")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.inspect or args.maintain:
        if not args.db:
            parser.error("--db required for inspection/maintenance")
        history = History(args.db)
        try:
            if args.maintain:
                history.maintain()
            with history.transaction():
                view = history.view()
            counts = {table: history.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("events", "pending", "checkpoints", "counters", "issues", "quarantined")}
            issues = dict(history.db.execute("SELECT code,COUNT(*) FROM issues GROUP BY code").fetchall())
            active_issues = dict(history.db.execute("""SELECT i.code,COUNT(*) FROM issues i
                WHERE NOT EXISTS (SELECT 1 FROM resolved_issues r
                WHERE r.source_path=i.source_path AND r.sequence=i.sequence AND r.code=i.code)
                GROUP BY i.code""").fetchall())
            resolved = history.db.execute("SELECT COUNT(*) FROM resolved_issues").fetchone()[0]
            print(json.dumps({"schema_version": SCHEMA_VERSION, "counts": counts,
                              "issue_counts": issues, "active_issue_counts": active_issues,
                              "resolved_issue_count": resolved,
                              "transcript_contract_policy": TRANSCRIPT_CONTRACT_POLICY,
                              "view": view}, indent=2))
        finally:
            history.close()
        return
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}
    print("\n".join(bridge(payload)))


if __name__ == "__main__":
    main()
