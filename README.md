# Cockpit status line for Claude Code

A three-line status line for [Claude Code](https://code.claude.com/docs/en/statusline)
with a model badge, reasoning effort, a context-window progress bar, session
cost, **prompt cache hit rate visualization**, and your current path and git branch.

```
 Opus 5.5  | medium  tok 290k/1M (29%) ■■■■■■■················ $9.54
 ▲ hit 94.2%  ▃▁▅▅▁█████▁▆
 ~/code/my-project · main*
```

- **Model badge** on a slate field, with any `(1M context)` qualifier stripped
  so the badge stays compact.
- **Reasoning effort** in amber — one colour at every level, so the level reads
  from the word rather than from a colour you have to memorise.
- **Token counter** with powder-blue numbers and unbolded parentheses, plus a
  progress bar and the running session cost.
- **Cache hit rate bar** on the second line — 12 buckets covering the last 60
  minutes, with 8-level color gradation (80-100%) showing cache performance.
  Each bucket is 5 minutes, aligned to wall-clock boundaries (06:00, 06:05, etc.).
  **Multi-session aware**: correctly merges data from multiple Claude Code sessions
  sharing the same cache log, filtering negative deltas to prevent >100% rates.
  The last-hour hit rate sits in front of the bar to one decimal place, capped
  at `99.9%` so it never grows past five characters.
- **Path and git branch** on the third line, with a `*` when the tree is dirty.
- **Degrades instead of wrapping.** The bar and then the cost drop away as the
  terminal narrows; below ~40 columns you are left with just the model badge.
- **Truecolor with a 256-colour fallback** for terminals that don't advertise
  24-bit support.

## Install

```sh
git clone https://github.com/GlacierMelt/claude-statusline-cockpit.git
cd claude-statusline-cockpit
bash install.sh
```

Then **open a new Claude Code session** — settings are read at startup, so the
current one won't pick it up.

The installer copies the script to `~/.claude/statusline-command.sh`, points
`statusLine` at it in `~/.claude/settings.json`, and backs up both files first
with a timestamp suffix. It only ever touches the `statusLine` key; every other
setting you have is left alone. Re-run it any time to upgrade.

To install somewhere other than `~/.claude`, set `CLAUDE_CONFIG_DIR`:

```sh
CLAUDE_CONFIG_DIR=~/my-claude bash install.sh
```

### Manual install

Copy `statusline-command.sh` somewhere permanent, then add this to
`~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline-command.sh"
  }
}
```

Use a **quoted absolute path** rather than `~` if your home directory contains
spaces — the command runs through a shell, so an unquoted path would split.

## Requirements

`bash`, `awk`, and `git` — all present by default on macOS and every mainstream
Linux distribution.

**`jq` is optional.** It's used when available, but the script reads the payload
with shell pattern matching when it isn't, which covers the macOS default (no
`jq`), minimal container images, and a `jq` that's installed but broken. Both
paths produce identical output, so you don't need to install anything.

## Uninstall

```sh
jq 'del(.statusLine)' ~/.claude/settings.json > /tmp/s.json \
  && mv /tmp/s.json ~/.claude/settings.json
```

Or run `/statusline delete` inside Claude Code, or just delete the `statusLine`
key by hand.

## Customising

### Cache hit rate visualization

The cache bar shows prompt cache performance over the last 60 minutes:

- **12 buckets** × 5 minutes each = 60-minute rolling window
- **8-level color gradation** from 80% to 100% hit rate:
  - `▁` 80-82.5% (warmest, indicates lower cache efficiency)
  - `▂` 82.5-85%
  - `▃` 85-87.5%
  - `▄` 87.5-90%
  - `▅` 90-92.5%
  - `▆` 92.5-95%
  - `▇` 95-97.5%
  - `█` 97.5-100% (coolest, indicates optimal cache performance)
- **Fixed-boundary alignment**: Buckets align to wall-clock boundaries (e.g., 06:00, 06:05, 06:10)
  so the rightmost bucket updates in real-time as the current 5-minute window accumulates data
- **Multi-session support**: When multiple Claude Code sessions run concurrently or sequentially,
  the bar correctly merges all cache data by detecting session boundaries (counter resets) and
  filtering negative deltas to prevent impossible >100% hit rates

The cache colors use a carefully tuned 8-step gradient from warm (#F5C9B5) to cool (#D8EEED)
tones, making performance trends readable at a glance. Each color is defined in the
`CACHE_COLORS` array near the top of the script.

**Interactive demos** showing the cache bar behavior with real data are available in `demos/`:
- `cache-bar-live-demo.html` — Real-time simulation with speed controls
- `cache-bar-multi-segment-fix.html` — Multi-session bug fix documentation

### The hit-rate number

The percentage after `▲ hit` is the hit rate across all 12 buckets. It is
rounded half away from zero to one decimal place and clamped to `0.0`–`99.9`,
so the field is at most five characters wide. `100.0%` never appears, because
`printf "%.1f"` of 99.95 would otherwise produce it.

On truecolor terminals each character is coloured by its role, and all five are
bold:

| Character | Colour |
| - | - |
| 1st digit | `#FF0000` |
| 2nd digit | `#FF282C` |
| 3rd digit | `#FF5657` |
| `.` | `#C0C5C9` |
| `%` | `#BBD5DA` |

Digits are counted left to right with the punctuation skipped, so a
four-character value like `5.0%` uses only the first two reds. The colours live
in `HIT_DIG_C`, `HIT_DOT_C` and `HIT_PCT_C`. Set `HIT_ROLE=0` to fall back to the
single flat colour in `HIT_NUM_C`. The 256-colour branch always uses that flat
colour, bold red (`196`), because the pale symbol tones have no close entry in
the 256 ramp.

### Color customization

Every colour is a named variable near the top of the script, in two branches —
one for truecolor, one for the 256-colour fallback. Edit the one that matches
your terminal.

```sh
BADGE_BG=$'\033[48;2;44;48;58m'       # #2c303a  badge field
TOK_NUM_C=$'\033[1;38;2;143;184;218m' # #8fb8da  token numbers (bold)
EFF_C=$'\033[38;2;254;127;45m'        # #fe7f2d  reasoning effort
DIR_C=$'\033[38;2;187;213;218m'       # #bbd5da  path on line 2
```

The `48;2;R;G;B` form is a truecolor background, `38;2;R;G;B` a truecolor
foreground, and a leading `1;` makes it bold.

### The cost sheen

The cost figure is coloured one character at a time — `$34.58` starts at the
reference grey and drifts toward a mint accent by the last digit. Three modes,
set by `SHEEN`:

| `SHEEN` | Effect |
| - | - |
| `gradient` | every character drifts one step from the base toward the accent (default) |
| `spot` | all base except one character, chosen by `COST_SPOT` (`first`, `last`, or a 1-based index) |
| `off` | one flat colour, `COST_C` |

```sh
SHEEN=gradient                        # off | gradient | spot
COST_SPOT=last                        # accent char when SHEEN=spot: first|last|N
COST_R0=224; COST_G0=228; COST_B0=235 # #e0e4eb  base
COST_R1=223; COST_G1=241; COST_B1=241 # #DFF1F1  accent
```

**A gradient inside a single glyph is not possible.** A terminal cell holds one
character and one foreground colour, and the terminal paints the whole shape
with that colour — nothing can colour part of a `$`. The sheen is therefore
quantised to one colour per character, which on a six-character number is what
reads as a gradient at all.

**`#DFF1F1` on `#e0e4eb` is subtle by design — ΔE2000 of 7.78.** Both sit near
94% luminance, so the accent is not brighter, only cooler: G+13, B+6, R−1.
Per character that is a move of about two levels. If you want the sheen to read
at a glance, darken the base to `#c3ccd8` (`195;204;216`), which lifts the
difference to ΔE 18.6 while keeping the accent you asked for.

The 256-colour fallback leaves `SHEEN=off`: that ramp's nearest entries are flat
greys a full 10 levels apart with no mint at all, so a gradient there would be
pure noise.

To change the bar characters, edit `FILLED_CHAR` and `EMPTY_CHAR`. To resize it,
the width is computed around `bar_w=$(( avail * 55 / 100 ))` — the `55` and the
following `* 2 / 3` together decide how much of the free space it takes.

### Layout

Line 1 is assembled from the widest arrangement down, so nothing ever overflows.
Line 2 shows the cache hit rate bar (always present when cache data exists).
Line 3 shows the current path and git branch.

Measured with the example above:

| Terminal width | What is shown |
| - | - |
| ≥ 54 | badge, effort, tokens, bar, cost |
| 45–53 | badge, effort, tokens, cost |
| 38–44 | badge, effort, tokens |
| 36–37 | badge without padding, effort, tokens |
| 27–35 | badge without padding, tokens |
| < 27 | badge only |

The exact cut-offs shift with the length of the model name and the cost, since
those are what the widths are computed from.

The cache bar (line 2) adapts to terminal width but is typically shown at full
width (12 buckets) unless the terminal is very narrow.

## Notes

**The status line is blank until you accept the workspace trust dialog** in a
given folder. That's Claude Code's behaviour for any `statusLine` command, not
something this script controls. `claude --debug` logs
`Status line command skipped: workspace trust not accepted` while it's pending.

**The light/dark contrast is uneven by design.** The palette was tuned against a
dark terminal. On a light background the path colour (`#bbd5da`) is low-contrast
and `#233d4d` — the pipe separator — nearly disappears. If you work mostly in a
light theme, raise `DIR_C` and `PIPE_C`; there's a comment at `PIPE_C` marking it
as the one fixed colour that doesn't adapt. The hit-rate `.` and `%`
(`#C0C5C9`, `#BBD5DA`) are also faint on light grounds, at about 1.5–1.7:1.

**This cannot auto-detect your terminal theme.** A `statusLine` command gets no
theme in its payload and can't read the terminal it's drawing into, so the two
branches are selected by 24-bit colour support, not by light/dark. Pick your
palette by hand.

**It costs about 80ms per redraw** — roughly 3× less than a naïve
one-`jq`-per-field version, because all payload fields are read in a single `jq`
call and the git branch and dirty flag come from one `git status`. If you set
`refreshInterval`, remember this runs on every update.

**Performance note for anyone extending this:** process spawns dominate the
runtime, not the shell logic. That single-pass `jq` call and the merged
`git status` cut the cost from ~243ms to ~79ms on the machine this was built on.
Adding one more `jq` or `git` invocation per field costs far more than any
amount of in-shell string handling.

## Credits

Layout and palette were modelled on
[statusline.sh/community/cockpit-4shq](https://statusline.sh/community/cockpit-4shq).

## License

MIT — see [LICENSE](LICENSE).
