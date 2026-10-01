# Status line — improvement notes

Findings from inspecting a real status-line payload (Claude Code 2.1.284,
model `claude-opus-5-5[1m]`, effort `xhigh`) and from a line-by-line check of
`statusline-command.sh`. Everything below is measured, not assumed.

Nothing here has been implemented. The current script is unchanged.

---

## What the script reads today

Eight fields out of more than forty available:

```
.model.display_name              .workspace.current_dir
.effort.level                    .context_window.context_window_size
.context_window.total_input_tokens
.context_window.total_output_tokens
.cost.total_cost_usd
```

---

## Recommended changes

### 1. Use `used_percentage` instead of computing it

**The current formula is right by accident, not by construction.**

The script computes `(total_input_tokens + total_output_tokens) / context_window_size`.
Claude Code computes `used_percentage` from the input side only — `input_tokens +
cache_creation_input_tokens + cache_read_input_tokens` — and deliberately excludes
output tokens.

In the captured session, `total_output_tokens` was **1**, so adding it changed
nothing and both agreed at 19%. But that is a property of this particular
session, not of the formula. When output is large the two will diverge.

```sh
# current
used=$(( in_tok + out_tok ))
pct=$(( (used * 100 + size / 2) / size ))

# correct — the field is already computed and already correct
pct=${used_pct:-0}
```

Add `(.context_window.used_percentage // 0)` to the single-pass `jq` call and to
the `scalar` fallback in the no-jq branch.

**Note:** keep reading `total_input_tokens` and `total_output_tokens` if the
`174.2k/1M` display is worth keeping — those are current-context figures, which
is the right semantic for a context bar. Only the *percentage* should come from
the precomputed field.

---

### 2. Show cache hit ratio

**The single most useful field that is not currently displayed.**

`prompt_cache.hit_ratio` was **0.9620** with a **5m** TTL. Every cache miss
rebuilds tens of thousands of tokens: `recache_tokens_if_cold` was **214,165**
in this session, and the one recorded miss cost `miss_recache_tokens: 184,347`.

At a 5-minute TTL the cache expires during any pause — reading a long file,
waiting on a build, stepping away. A hit ratio drifting below ~0.9 means the
session is silently paying to rebuild context it already had.

```sh
# prompt_cache.hit_ratio is 0..1; render as a percentage
hit=$(awk -v h="$hit_raw" 'BEGIN{ if (h=="") exit; printf "%d%%", h*100 + 0.5 }')
```

Worth pairing with `prompt_cache.ttl` (`5m` here), since the fix for a low ratio
is usually "stop leaving it idle".

`prompt_cache` is only present once caching has been observed, so guard it:

```sh
[ -n "$hit_raw" ] && hit_seg="  ${TOK_LABEL_C}cache${RESET} ${...}${hit}${RESET}"
```

---

### 3. Show session duration

`cost.total_duration_ms` was **20,595,151 (5h43m)** with
`total_api_duration_ms` **12,955,207 (3h35m)** — meaning roughly **63% of wall
clock was spent waiting on the API**.

Duration is cheap to render and useful to see; the API ratio is interesting but
probably too much for a status line that must read at a glance. Consider showing
duration alone, and only surfacing the ratio in a long session.

```sh
dur=$(awk -v ms="$dur_raw" 'BEGIN{
  s = int(ms/1000); h = int(s/3600); m = int((s%3600)/60)
  if (h > 0) printf "%dh%02dm", h, m; else printf "%dm", m
}')
```

---

## Fields that are NOT available

Ten candidate fields were absent from the real payload. Do not write code that
assumes any of them exist without a presence check.

| field | why it is missing |
| - | - |
| `rate_limits` (+ `five_hour`, `seven_day`, `spend_limit`) | Only for claude.ai Pro/Max subscribers or a gateway with a spend limit configured. **Never delivered to API-key users.** This session runs through a local proxy, so it is absent. |
| `worktree` | Only during a Claude Code worktree session. |
| `workspace.git_worktree` | Only when the cwd is inside some linked worktree. |
| `workspace.repo` | Only when `origin` resolves to a recognised host. |
| `agent` | Only with `--agent` or an agent setting. |
| `pr` | Only with an associated pull request. |
| `vim` | Only with vim mode enabled. |

**This rules out usage-quota display**, which would otherwise have been the most
valuable addition. It cannot be worked around — the data is never sent.

---

## Fields available but probably not worth the space

| field | value seen | verdict |
| - | - | - |
| `output_style.name` | `default` | Constant for most users |
| `version` | `2.1.284` | No routine value |
| `fast_mode` | `false` | Already visible from behaviour |
| `thinking.enabled` | `true` | Rarely changes mid-session |
| `session_name` | `冰川蓝配色组合` | An AI-generated title; changes as the topic drifts |
| `model.id` | `claude-opus-5-5[1m]` | `display_name` is already used |
| `cost.total_lines_added/removed` | 1486 / 424 | Cheap activity signal, but low priority |
| `workspace.added_dirs` | `[]` | Only worth showing when non-empty |
| `prompt_id` | UUID | Debugging only |

---

## Corrections to earlier claims

Two things asserted before the payload was inspected that turned out to be wrong.

**The progress bar was not inconsistent.** Pixel-run measurement of the
screenshot suggested a 32-cell bar with 4 filled (12.5%) against a 17% label.
Recomputing the script's arithmetic shows `bar_w` is capped at **26** — a 32-cell
bar is unreachable at any terminal width:

```
avail * 55 / 100  ->  clamped to max 40  ->  * 2/3  ->  26
```

The "32 cells" was an antialiasing artefact of my measurement. With the real
payload the bar reads `5 of 26 = 19.2%` against a `19%` label. **The bar and the
label agree; there is no bug here.**

**`rate_limits` was recommended, then found absent.** The recommendation came
from documentation rather than observation. It is not deliverable in this setup.

---

## Unrelated finding: `model` is gone from settings.json

`~/.claude/settings.json` has no `model` key, and none of the three backups in
`~/.claude/` have one either — including the oldest, which contained only
`env`. The key was already absent before this work began, so nothing here
removed it.

Something else did rewrite the file between 04:45 and 05:52: the 04:45 backup
holds `['env', 'statusLine']` while the current file holds
`['autoCompactWindow', 'env', 'modelSettings', 'skipDangerousModePermissionPrompt',
'statusLine']`. Three keys were added by some other tool.

Worth checking whether `cc-switch` (or whatever wrote those) also clobbers
MCP servers, skills, or prompt config.

---

## Reference: the captured payload

Kept at `/tmp/sl_payload_reference.json` (temporary — copy it somewhere durable
if it is still wanted after a reboot).

```json
{
  "session_id": "eac27173-2f65-4bd1-bf02-808bf572017b",
  "effort": { "level": "xhigh" },
  "session_name": "冰川蓝配色组合",
  "model": {
    "id": "claude-opus-5-5[1m]",
    "display_name": "Opus 5.5 (1M context)"
  },
  "workspace": {
    "current_dir": "/Users/eryck-petersen/AI/CODEX/LLM MODEL TEST/Opus_5-5",
    "project_dir": "/Users/eryck-petersen/AI/CODEX/LLM MODEL TEST/Opus_5-5",
    "added_dirs": []
  },
  "version": "2.1.284",
  "output_style": { "name": "default" },
  "cost": {
    "total_cost_usd": 42.29,
    "total_duration_ms": 20595151,
    "total_api_duration_ms": 12955207,
    "total_lines_added": 1486,
    "total_lines_removed": 424
  },
  "context_window": {
    "total_input_tokens": 214192,
    "total_output_tokens": 1,
    "context_window_size": 1000000,
    "current_usage": {
      "input_tokens": 372,
      "output_tokens": 1,
      "cache_creation_input_tokens": 72,
      "cache_read_input_tokens": 213748
    },
    "used_percentage": 21,
    "remaining_percentage": 79
  },
  "exceeds_200k_tokens": true,
  "prompt_cache": {
    "warm": true,
    "caching_observed": true,
    "ttl": "5m",
    "requests": 33,
    "misses": 1,
    "hit_ratio": 0.9620,
    "recache_tokens_if_cold": 214165
  },
  "fast_mode": false,
  "thinking": { "enabled": true }
}
```

Absent keys worth noting: `rate_limits`, `worktree`, `workspace.git_worktree`,
`workspace.repo`, `agent`, `pr`, `vim`.

---

## Suggested order of work

1. **`used_percentage`** — smallest change, removes a latent bug, no new render.
2. **Cache hit ratio** — highest value per column, but needs a presence guard.
3. **Duration** — cheap, purely additive.

The `xhigh` effort level renders correctly today and needs no change; it is a
valid level (`low` / `medium` / `high` / `xhigh` / `max`) delivered as-is.

---

## Verified-good, no action needed

- `bar_w` arithmetic and the fill calculation (checked against real values)
- The `effort.level` display — `xhigh` is the real upstream value
- The `(1M context)` stripping from `model.display_name`
- The single-pass `jq` read and the no-jq `scalar` fallback
- The merged `git status --porcelain -b` call for branch + dirty flag
- The cost sheen (`SHEEN=gradient` / `spot` / `off`) and its layout measurement
