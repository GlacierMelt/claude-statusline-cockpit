"""Payload-only input parser; independent of SQLite and history adapters.

Python 3.9+ stdlib. A history failure must not erase model/context/path fields.
"""
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys

TOKEN_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens")
VIEW_AXIS = "active-five-minute/v1"


def _integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def clean_text(value):
    if not isinstance(value, str):
        return ""
    return "".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        n = Decimal(str(value))
        # Keep malformed giant numbers out of Decimal formatting/terminal math.
        return n if n.is_finite() and 0 <= n <= 2**53 - 1 else None
    except InvalidOperation:
        return None


def context_fields(payload):
    context = payload.get("context_window")
    context = context if isinstance(context, dict) else {}
    current = context.get("current_usage")
    if isinstance(current, dict) and all(_integer(current.get(k)) for k in TOKEN_KEYS):
        used = sum(current[k] for k in TOKEN_KEYS)
    elif _integer(context.get("total_input_tokens")):
        # Officially input includes cache reads/writes. Output is never added.
        used = context["total_input_tokens"]
    else:
        used = None
    size = context.get("context_window_size")
    size = size if _integer(size) and size > 0 else None
    pct = number(context.get("used_percentage"))
    if pct is None and used is not None and size:
        pct = Decimal(used) * 100 / size
    pct_label = "?" if pct is None else format(pct.quantize(Decimal("0.1")), "f").rstrip("0").rstrip(".")
    # rstrip on an integer 0.0 leaves '0'; use explicit safeguard.
    pct_label = pct_label or "0"
    pct_bar = "" if pct is None else str(max(0, min(100, int(pct + Decimal("0.5")))))
    return ("" if used is None else str(used), "" if size is None else str(size),
            pct_label, pct_bar)


def input_fields(payload):
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    effort = payload.get("effort") if isinstance(payload.get("effort"), dict) else {}
    workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    cost = payload.get("cost") if isinstance(payload.get("cost"), dict) else {}
    raw_cost = number(cost.get("total_cost_usd"))
    return [clean_text(model.get("display_name")), clean_text(effort.get("level")),
            *context_fields(payload), "" if raw_cost is None else str(raw_cost),
            clean_text(workspace.get("current_dir") or payload.get("cwd"))]


def valid_view(view):
    return (isinstance(view, dict) and view.get("axis") == VIEW_AXIS
            and isinstance(view.get("codes"), list)
            and len(view["codes"]) == 12
            and all(isinstance(c, str) and c in "01234567?-" and len(c) == 1
                    for c in view["codes"])
            and isinstance(view.get("percentage"), str)
            and re.fullmatch(r"(?:\?%|~?\d+(?:\.\d)?%)", view["percentage"]) is not None
            and _integer(view.get("revision")))


def saved_history_fields(env=os.environ):
    """Read the last committed projection only; never ingest or advance time."""
    scope = {key: env[name] for key, name in (
        ("project_id", "CACHE_SCOPE_PROJECT"), ("session_id", "CACHE_SCOPE_SESSION"),
        ("conversation_scope", "CACHE_SCOPE_CONVERSATION")) if env.get(name)}
    state = Path(env.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    db_path = env.get("CACHE_HISTORY_DB", str(state / "claude-statusline-cockpit/history.sqlite3"))
    try:
        views = json.loads(Path(str(db_path) + ".view.json").read_text())
        key = json.dumps(scope, sort_keys=True, separators=(",", ":"))
        view = views.get(key) if isinstance(views, dict) else None
        if valid_view(view):
            return [view["percentage"], "".join(view["codes"])]
    except (OSError, ValueError):
        pass
    return ["?%", "?" * 12]


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}
    # Preserve known committed history; unknown only if no valid saved view.
    print("\n".join(input_fields(payload) + saved_history_fields()))


if __name__ == "__main__":
    main()
