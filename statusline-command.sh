#!/bin/bash
# Claude Code status line — "cockpit" layout, two lines
#   Opus 5.5  | high  tok 94.4k/200k (47%)  ■■■■■■■■■···   $0.42
#   ~/AI/CODEX/LLM MODEL TEST/Opus_5-5 · main*
#
# Palette follows the reference design: white-on-slate badge, powder-blue token
# numbers (bold) with unbolded parens, light-gray progress fill, dim-gray empty
# slots. Colours were sampled from the reference image rather than eyeballed.
# None has a precise 256-colour equivalent, so we emit truecolor and fall back
# to the nearest 256 entries when the terminal doesn't advertise 24-bit support.
#
# Dependencies: bash, awk and git. jq is used when present and worked around
# when not — see `scalar` below, because jq is not installed by default on
# macOS or on many minimal Linux images.

input=$(cat)

RESET=$'\033[0m'

# --- read the payload ------------------------------------------------------
# Every field comes from ONE jq invocation rather than six. Process spawn is
# what this script actually spends its time on, and it re-runs on every status
# line update, so the difference is the bulk of its cost.
#
# A missing jq is the documented-common case (macOS ships without it); a jq
# that is present but broken or too old to parse is the nastier one, because a
# silently blank status line is much harder to diagnose than a slightly coarser
# one. Both land in the same place: `fields` comes back empty and the regex
# reader below takes over. That check costs nothing on the happy path, unlike a
# separate preflight call.
model=""; effort=""; in_tok=""; out_tok=""; size=""; cost_raw=""; dir=""

if command -v jq >/dev/null 2>&1; then
  fields=$(printf '%s' "$input" | jq -r '[
      (.model.display_name // ""),
      (.effort.level // ""),
      (.context_window.total_input_tokens // 0),
      (.context_window.total_output_tokens // 0),
      (.context_window.context_window_size // 0),
      (.cost.total_cost_usd // ""),
      (.workspace.current_dir // .cwd // "")
    ] | @tsv' 2>/dev/null)
  [ -n "$fields" ] && IFS=$'\t' read -r model effort in_tok out_tok size cost_raw dir <<< "$fields"
fi

if [ -z "$model$effort$in_tok$out_tok$size$cost_raw$dir" ]; then
  # Flatten newlines so the regex never has to span a line break, then read the
  # leaf key. Enough for the flat string and number fields used here. It is not
  # a JSON parser — it cannot tell a nested field from a lookalike string
  # elsewhere in the payload — but the payload shape is fixed.
  flat=$(printf '%s' "$input" | tr -d '\n\r')
  scalar() {
    local key=${1##*.}
    if [[ $flat =~ \"$key\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then
      printf '%s' "${BASH_REMATCH[1]}"
    elif [[ $flat =~ \"$key\"[[:space:]]*:[[:space:]]*(-?[0-9]+\.?[0-9]*) ]]; then
      printf '%s' "${BASH_REMATCH[1]}"
    fi
  }
  model=$(scalar model.display_name)
  effort=$(scalar effort.level)
  in_tok=$(scalar context_window.total_input_tokens)
  out_tok=$(scalar context_window.total_output_tokens)
  size=$(scalar context_window.context_window_size)
  cost_raw=$(scalar cost.total_cost_usd)
  dir=$(scalar workspace.current_dir)
  [ -z "$dir" ] && dir=$(scalar cwd)
fi

# --- truecolor capability --------------------------------------------------
TC=0
case "${COLORTERM:-}" in truecolor|24bit) TC=1 ;; esac
[ "${TERM_PROGRAM:-}" = "iTerm.app" ] && TC=1
[ "${TERM_PROGRAM:-}" = "WezTerm" ]   && TC=1
case "${TERM:-}" in *-direct|*-truecolor) TC=1 ;; esac

if [ "$TC" = 1 ]; then
  BADGE_BG=$'\033[48;2;44;48;58m'      # #2c303a slate badge field
  BADGE_FG=$'\033[1;38;2;255;255;255m' # pure white, bold
  SEP_C=$'\033[38;2;90;102;120m'       # #5a6678 dot / second-line separator
  # Fixed colour rather than the terminal default (SGR 39): #233d4d was chosen
  # explicitly. Note this does NOT adapt — see the fallback branch.
  PIPE_C=$'\033[38;2;35;61;77m'        # #233d4d deep petrol
  TOK_LABEL_C=$'\033[38;2;93;119;140m' # #5d778c slate — 'tok' label
  TOK_NUM_C=$'\033[1;38;2;143;184;218m' # #8fb8da powder blue, bold — numbers + percent
  TOK_PUNC_C=$'\033[38;2;143;184;218m'  # same colour, NOT bold — parens
  BAR_FILL_C=$'\033[38;2;224;228;235m' # #e0e4eb light fill
  BAR_EMPTY_C=$'\033[38;2;74;85;104m'  # #4a5568 dim empty slots
  COST_C=$'\033[38;2;224;228;235m'
  EFF_C=$'\033[38;2;254;127;45m'       # #fe7f2d amber — same on every level
  DIR_C=$'\033[38;2;187;213;218m'      # #bbd5da pale ice — second line path
  GIT_C=$'\033[38;2;90;158;214m'
  DIRTY_C=$'\033[1;38;2;224;228;235m'
else
  BADGE_BG=$'\033[48;5;236m'
  BADGE_FG=$'\033[1;38;5;231m'
  SEP_C=$'\033[38;5;243m'
  PIPE_C=$'\033[38;5;237m'             # nearest 256 to #233d4d (loses the blue)
  TOK_LABEL_C=$'\033[38;5;66m'         # nearest 256 to #5d778c
  TOK_NUM_C=$'\033[1;38;5;110m'        # nearest 256 to #8fb8da, bold
  TOK_PUNC_C=$'\033[38;5;110m'         # same colour, NOT bold — parens
  BAR_FILL_C=$'\033[38;5;252m'
  BAR_EMPTY_C=$'\033[38;5;240m'
  COST_C=$'\033[38;5;252m'
  EFF_C=$'\033[38;5;208m'              # nearest 256 to #fe7f2d
  DIR_C=$'\033[38;5;152m'              # nearest 256 to #bbd5da
  GIT_C=$'\033[38;5;74m'
  DIRTY_C=$'\033[1;38;5;252m'
fi

FILLED_CHAR='■'
EMPTY_CHAR='·'

# --- humanise a token count: 94400 -> 94.4k, 200000 -> 200k ----------------
humanise() {
  awk -v n="${1:-0}" 'BEGIN{
    if (n >= 1000000)   { s = sprintf("%.1fM", n/1000000) }
    else if (n >= 1000) { s = sprintf("%.1fk", n/1000) }
    else                { s = sprintf("%d",   n) }
    sub(/\.0M$/, "M", s); sub(/\.0k$/, "k", s)
    print s
  }'
}

# --- model badge (drop any "(1M context)" qualifier to stay compact) -------
model=$(printf '%s' "$model" | sed 's/ *(.*)$//')
[ -z "$model" ] && model="Claude Code"

# --- token usage -----------------------------------------------------------
# Counts arrive as integers; strip any fraction so the arithmetic is safe.
in_tok=${in_tok%%.*}
out_tok=${out_tok%%.*}
size=${size%%.*}
case "$in_tok"  in ''|*[!0-9]*) in_tok=0  ;; esac
case "$out_tok" in ''|*[!0-9]*) out_tok=0 ;; esac
case "$size"    in ''|*[!0-9]*) size=0    ;; esac

used=$(( in_tok + out_tok ))
pct=0
[ "$size" -gt 0 ] 2>/dev/null && pct=$(( (used * 100 + size / 2) / size ))

used_s=$(humanise "$used")
size_s=$(humanise "$size")

# --- session cost ----------------------------------------------------------
cost_s=""
[ -n "$cost_raw" ] && cost_s=$(awk -v c="$cost_raw" 'BEGIN{printf "$%.2f", c}')

# --- terminal width (tput cannot see it from a status line script) ---------
# Claude Code sets COLUMNS to the current terminal width before running this.
cols=${COLUMNS:-0}
case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
[ "$cols" -gt 0 ] 2>/dev/null || cols=80

# ===========================================================================
# Line 1 — the cockpit row
#
# Segments are built first, then included from the widest layout down, so a
# narrow or split terminal degrades by dropping the bar, then the cost, then
# the effort label — rather than overflowing and wrapping.
# ===========================================================================
badge_seg="${BADGE_BG}${BADGE_FG} ${model} ${RESET}"
badge_len=$(( ${#model} + 2 ))
badge_bare="${BADGE_BG}${BADGE_FG}${model}${RESET}"   # no padding, for tight fits
badge_bare_len=${#model}

# Effort is plain amber text — one colour for every level, no reverse-video
# chip and no size variation, so the level reads from the word itself.
eff_seg=""; eff_len=0
if [ -n "$effort" ]; then
  eff_seg="${PIPE_C} | ${RESET}${EFF_C}${effort}${RESET}"
  eff_len=$(( 3 + ${#effort} ))
fi

tok_seg=""; tok_len=0; have_tok=0
if [ "$size" -gt 0 ] 2>/dev/null; then
  plain_tok="tok ${used_s}/${size_s} (${pct}%)"
  tok_seg="  ${TOK_LABEL_C}tok${RESET} ${TOK_NUM_C}${used_s}/${size_s}${RESET} ${TOK_PUNC_C}(${RESET}${TOK_NUM_C}${pct}%${RESET}${TOK_PUNC_C})${RESET}"
  tok_len=$(( 2 + ${#plain_tok} ))
  have_tok=1
fi

cost_vis=${#cost_s}

# Minimum widths for each rung of the ladder.
min_full=$(( badge_len + eff_len + tok_len + 2 + 8 + 1 + cost_vis ))
min_nobar=$(( badge_len + eff_len + tok_len + 2 + cost_vis ))

bar_w=0
out1=""; vis=0
show_bar=0; show_cost=0

# Rung 1 — everything: badges, tokens, bar, right-aligned cost.
if [ "$have_tok" = 1 ] && [ "$cols" -ge "$min_full" ]; then
  show_bar=1; show_cost=1
# Rung 2 — bar doesn't fit; keep the cost.
elif [ "$have_tok" = 1 ] && [ "$cols" -ge "$min_nobar" ]; then
  show_cost=1
fi
# Rung 3 — badge/effort/tokens only (default flags), when even that is tight.
out1="${badge_seg}${eff_seg}${tok_seg}"
vis=$(( badge_len + eff_len + tok_len ))

# Rung 4 — even rung 3 doesn't fit: shed the badge padding, then the effort
# chip, then the whole token segment, so nothing ever overflows and wraps.
if [ "$cols" -lt "$vis" ]; then
  out1="${badge_bare}${eff_seg}${tok_seg}"
  vis=$(( badge_bare_len + eff_len + tok_len ))
fi
if [ "$cols" -lt "$vis" ]; then
  out1="${badge_bare}${tok_seg}"
  vis=$(( badge_bare_len + tok_len ))
fi
if [ "$cols" -lt "$vis" ]; then
  out1="${badge_bare}"
  vis=$badge_bare_len
fi

if [ "$show_bar" = 1 ]; then
  # Bar width is derived from the widest layout only, then scaled to ~2/3 so
  # the row stays compact instead of stretching to fill the terminal.
  avail=$(( cols - vis - 2 - cost_vis ))
  bar_w=$(( avail * 55 / 100 ))
  [ "$bar_w" -lt 8 ]  && bar_w=8
  [ "$bar_w" -gt 40 ] && bar_w=40
  bar_w=$(( bar_w * 2 / 3 ))
  [ "$bar_w" -lt 6 ] && bar_w=6

  filled=$(( (pct * bar_w + 50) / 100 ))
  [ "$filled" -gt "$bar_w" ] && filled=$bar_w
  [ "$filled" -lt 0 ] && filled=0
  empty=$(( bar_w - filled ))

  fill_str=""; empty_str=""; i=0
  while [ "$i" -lt "$filled" ]; do fill_str="${fill_str}${FILLED_CHAR}"; i=$(( i + 1 )); done
  i=0
  while [ "$i" -lt "$empty" ];  do empty_str="${empty_str}${EMPTY_CHAR}"; i=$(( i + 1 )); done

  out1="${out1} ${BAR_FILL_C}${fill_str}${BAR_EMPTY_C}${empty_str}${RESET}"
fi

# Cost sits one space after the bar — a fixed gap, not right-aligned to the
# terminal edge, so it never floats away from the progress it belongs to.
if [ "$show_cost" = 1 ]; then
  out1="${out1} ${COST_C}${cost_s}${RESET}"
fi

# ===========================================================================
# Line 2 — where you are
# ===========================================================================
case "$dir" in
  "$HOME")   short="~" ;;
  "$HOME"/*) short="~${dir#"$HOME"}" ;;
  *)         short="$dir" ;;
esac

# One `git status --porcelain -b` yields both the branch and the dirty flag:
# its first line is "## <branch>...<upstream> [ahead N]", and any further line
# means there are changes. That is one git process per redraw instead of two,
# which matters because this runs on every status line update.
branch=""; dirty=""
if [ -n "$dir" ] && [ -d "$dir" ]; then
  porcelain=$(GIT_OPTIONAL_LOCKS=0 git --no-optional-locks -C "$dir" \
                status --porcelain -b 2>/dev/null)
  head_line=${porcelain%%$'\n'*}
  case "$head_line" in
    '## '*)
      branch=${head_line#\#\# }
      # A repository with no commits yet reports "## No commits yet on main".
      # That is not a branch name, so show nothing rather than the word "No".
      case "$branch" in 'No commits yet'*) branch="" ;; esac
      if [ -n "$branch" ]; then
        branch=${branch%%...*}   # drop the upstream and ahead/behind counts
        branch=${branch%% *}     # and any trailing state
        # A detached HEAD reports the literal string "HEAD"; show the short SHA.
        if [ "$branch" = "HEAD" ]; then
          branch=$(GIT_OPTIONAL_LOCKS=0 git --no-optional-locks \
                     -C "$dir" rev-parse --short HEAD 2>/dev/null)
        fi
      fi
      # Anything past the first line means changes are present.
      [ "$porcelain" != "$head_line" ] && dirty="*"
      ;;
  esac
fi

out2=""
[ -n "$short" ] && out2="${DIR_C}${short}${RESET}"
if [ -n "$branch" ]; then
  [ -n "$out2" ] && out2="${out2}${SEP_C} · ${RESET}"
  out2="${out2}${GIT_C}${branch}${RESET}${DIRTY_C}${dirty}${RESET}"
fi

printf '%s\n' "$out1"
[ -n "$out2" ] && printf '%s\n' "$out2"
exit 0
