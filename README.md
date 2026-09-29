# Cockpit status line for Claude Code

A two-line status line for [Claude Code](https://code.claude.com/docs/en/statusline)
with a model badge, reasoning effort, a context-window progress bar, session
cost, and your current path and git branch.

```
 Opus 5.5  | medium  tok 290k/1M (29%) ■■■■■■■················ $9.54
 ~/code/my-project · main*
```

- **Model badge** on a slate field, with any `(1M context)` qualifier stripped
  so the badge stays compact.
- **Reasoning effort** in amber — one colour at every level, so the level reads
  from the word rather than from a colour you have to memorise.
- **Token counter** with powder-blue numbers and unbolded parentheses, plus a
  progress bar and the running session cost.
- **Path and git branch** on the second line, with a `*` when the tree is dirty.
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

To change the bar characters, edit `FILLED_CHAR` and `EMPTY_CHAR`. To resize it,
the width is computed around `bar_w=$(( avail * 55 / 100 ))` — the `55` and the
following `* 2 / 3` together decide how much of the free space it takes.

### Layout

Line 1 is assembled from the widest arrangement down, so nothing ever overflows.
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

## Notes

**The status line is blank until you accept the workspace trust dialog** in a
given folder. That's Claude Code's behaviour for any `statusLine` command, not
something this script controls. `claude --debug` logs
`Status line command skipped: workspace trust not accepted` while it's pending.

**The light/dark contrast is uneven by design.** The palette was tuned against a
dark terminal. On a light background the path colour (`#bbd5da`) is low-contrast
and `#233d4d` — the pipe separator — nearly disappears. If you work mostly in a
light theme, raise `DIR_C` and `PIPE_C`; there's a comment at `PIPE_C` marking it
as the one fixed colour that doesn't adapt.

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
