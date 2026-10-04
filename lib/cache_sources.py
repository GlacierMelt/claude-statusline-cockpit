"""Contract-validated, content-free adapters. No redraw is a request.

Claude's application version is provenance, not an eligibility gate. Each
record must satisfy the inspected assistant identity/completion/token contract.
Compatible version updates and additive fields work without an allowlist edit;
changed/malformed required fields fail closed, never guessed into usage.
The optional cockpit-events/v1 feed is an explicit integration contract, NOT
an assertion that Claude Code emits that schema.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

TRANSCRIPT_CONTRACT = "claude-assistant-jsonl/v1"
# Observation inventory ONLY: neither this set nor a semver range gates data.
INSPECTED_TRANSCRIPT_VERSIONS = frozenset(
    ["2.1.241"] + [f"2.1.{n}" for n in range(280, 290)]
)
TOKEN_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens")
STOP_REASONS = frozenset(("end_turn", "tool_use", "stop_sequence", "max_tokens",
                          "pause_turn", "refusal", "model_context_window_exceeded"))


def integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def timestamp_ms(value):
    if not isinstance(value, str):
        raise ValueError("missing_event_time")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone_required")
        elapsed = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        if elapsed.days < 0:
            raise ValueError("invalid_event_time")
        # Floor exact microseconds, not floating-point round across a boundary.
        return (elapsed.days * 86_400_000 + elapsed.seconds * 1000
                + elapsed.microseconds // 1000)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid_event_time") from exc


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("missing_identity")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid_identity")
    return value


def token_usage(value, partial=False):
    if not isinstance(value, dict):
        raise ValueError("missing_usage")
    result = {}
    for key in TOKEN_KEYS:
        if partial and key not in value:
            continue
        if not integer(value.get(key)):
            raise ValueError("invalid_usage")
        result[key] = value[key]
    return result


@dataclass(frozen=True)
class Record:
    source: str
    source_version: str
    project_id: str
    session_id: str
    conversation_scope: str
    event_id: str
    event_ms: int
    observed_ms: int
    sequence: int
    kind: str = "request"
    usage: dict = field(default_factory=dict)
    counter_epoch: str = ""
    request_count: int = 0
    schema_version: str = "cockpit-events/v1"

    @property
    def key(self):
        return (self.source, self.project_id, self.session_id,
                self.conversation_scope, self.event_id)


@dataclass(frozen=True)
class Issue:
    code: str
    event_ms: Optional[int] = None
    project_id: str = ""
    session_id: str = ""
    conversation_scope: str = ""


@dataclass(frozen=True)
class Excluded:
    """Identified client-generated message, not a completed model request."""
    key: tuple
    reason: str


def transcript_exclusion_reason(row):
    if not isinstance(row, dict) or row.get("type") != "assistant":
        return None
    if row.get("isApiErrorMessage") is True:
        return "client_api_error_message"
    message = row.get("message")
    if isinstance(message, dict) and message.get("model") == "<synthetic>":
        return "client_synthetic_message"
    return None


def parse_transcript(row, project, scope, sequence, observed_ms):
    if not isinstance(row, dict) or row.get("type") != "assistant":
        return None
    event_ms = None
    session = ""
    excluded_reason = transcript_exclusion_reason(row)
    try:
        session = identifier(row.get("sessionId") or row.get("session_id"))
        if row.get("sessionId") and row.get("session_id") and row["sessionId"] != row["session_id"]:
            raise ValueError("conflicting_session_identity")
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("invalid_assistant")
        event_id = identifier(message.get("id"))
        if excluded_reason:
            # Neither a stop_sequence nor all-zero usage proves a request.
            # Keep the identity for audit/isolation, without counting a slot.
            return Excluded(("claude-transcript", identifier(project), session,
                             identifier(scope), event_id), excluded_reason)
        event_ms = timestamp_ms(row.get("timestamp"))
        version = row.get("version")
        if version is None:
            version = "unreported"  # metadata absence is not zero/missing usage
        else:
            try:
                version = identifier(version)
            except ValueError as exc:
                raise ValueError("invalid_transcript_version") from exc
        if "stop_reason" not in message:
            raise ValueError("missing_completion")
        stop = message["stop_reason"]
        if stop is None:
            # Input counts may be present before completion. Never book them yet.
            kind = "stream_start"
            usage = token_usage(message.get("usage"), partial=True)
        elif isinstance(stop, str) and stop in STOP_REASONS:
            kind = "request"
            usage = token_usage(message.get("usage"))
        else:
            raise ValueError("unsupported_stop_reason")
        return Record("claude-transcript", version, identifier(project), session,
                      identifier(scope), event_id, event_ms, observed_ms, sequence,
                      kind=kind, usage=usage, schema_version=TRANSCRIPT_CONTRACT)
    except ValueError as exc:
        # A malformed *explicit client error* still isn't missing request usage.
        # Without a trustworthy identity it cannot be quarantined by key.
        if excluded_reason:
            return None
        return Issue(str(exc), event_ms, project, session, scope)


def parse_feed(row, sequence, observed_ms):
    """Explicit optional feed; producers must supply real request identities/times."""
    event_ms = None
    project = session = scope = ""
    try:
        if not isinstance(row, dict) or row.get("schema") != "cockpit-events/v1":
            raise ValueError("unsupported_feed_schema")
        project = identifier(row.get("project_id"))
        session = identifier(row.get("session_id"))
        scope = identifier(row.get("conversation_scope"))
        event_ms = timestamp_ms(row.get("event_time"))
        kind = row.get("kind", "request")
        if kind not in ("request", "stream_start", "stream_delta", "stream_stop", "counter"):
            raise ValueError("unsupported_event_kind")
        seq = row.get("source_sequence", sequence)
        if not integer(seq):
            raise ValueError("invalid_sequence")
        epoch = ""
        count = 0
        if kind == "counter":
            epoch = identifier(row.get("counter_epoch"))
            count = row.get("request_count")
            if not integer(count):
                raise ValueError("invalid_request_count")
        usage = token_usage(row.get("usage", {}), partial=kind.startswith("stream_"))
        return Record(identifier(row.get("source")), identifier(row.get("source_version")),
                      project, session, scope, identifier(row.get("event_id")),
                      event_ms, observed_ms, seq, kind=kind, usage=usage,
                      counter_epoch=epoch, request_count=count)
    except ValueError as exc:
        return Issue(str(exc), event_ms, project, session, scope)
