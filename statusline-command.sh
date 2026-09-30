#!/bin/bash
# Claude Code status line — "cockpit" layout, three lines
#   Opus 5.5  | high  tok 94.4k/200k (47%)  ■■■■■■■■■···   $0.42
#   ▲ hit 94%  ▁▃▄▅▆▇█
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
      (.workspace.current_dir // .cwd // ""),
      (.prompt_cache.hit_ratio // ""),
      (.prompt_cache.requests // ""),
      (.prompt_cache.misses // "")
    ] | @tsv' 2>/dev/null)
  [ -n "$fields" ] && IFS=$'\t' read -r model effort in_tok out_tok size cost_raw dir \
                                       hit_raw req_raw miss_raw <<< "$fields"
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
  # prompt_cache is absent until caching has been observed, so all three are
  # allowed to come back empty and the whole segment is skipped when they do.
  hit_raw=$(scalar prompt_cache.hit_ratio)
  req_raw=$(scalar prompt_cache.requests)
  miss_raw=$(scalar prompt_cache.misses)
fi

# --- truecolor capability --------------------------------------------------
TC=0
case "${COLORTERM:-}" in truecolor|24bit) TC=1 ;; esac
[ "${TERM_PROGRAM:-}" = "iTerm.app" ] && TC=1
[ "${TERM_PROGRAM:-}" = "WezTerm" ]   && TC=1
case "${TERM:-}" in *-direct|*-truecolor) TC=1 ;; esac

# One block per height step, U+2581 .. U+2588. Index is the step number, so the
# colour array and this one are addressed by the same value. Kept as plain
# variables rather than an array because they are handed to awk via -v below,
# and a literal character is safer there than a \uXXXX escape: whether awk
# decodes those depends on the build and the locale, while a character that
# arrives as UTF-8 bytes goes through untouched.
RAMP_CH0='▁'; RAMP_CH1='▂'; RAMP_CH2='▃'; RAMP_CH3='▄'
RAMP_CH4='▅'; RAMP_CH5='▆'; RAMP_CH6='▇'; RAMP_CH7='█'

if [ "$TC" = 1 ]; then
  BADGE_BG=$'\033[48;2;44;48;58m'      # #2c303a slate badge field
  BADGE_FG=$'\033[1;38;2;255;255;255m' # pure white, bold
  SEP_C=$'\033[38;2;90;102;120m'       # #5a6678 dot / third-line separator
  # Fixed colour rather than the terminal default (SGR 39): #233d4d was chosen
  # explicitly. Note this does NOT adapt — see the fallback branch.
  PIPE_C=$'\033[38;2;35;61;77m'        # #233d4d deep petrol
  TOK_LABEL_C=$'\033[38;2;93;119;140m' # #5d778c slate — 'tok' label
  TOK_NUM_C=$'\033[1;38;2;143;184;218m' # #8fb8da powder blue, bold — numbers + percent
  TOK_PUNC_C=$'\033[38;2;143;184;218m'  # same colour, NOT bold — parens
  BAR_FILL_C=$'\033[38;2;224;228;235m' # #e0e4eb light fill
  BAR_EMPTY_C=$'\033[38;2;74;85;104m'  # #4a5568 dim empty slots
  COST_C=$'\033[38;2;224;228;235m'
  SHEEN=gradient                       # off | gradient | spot  (see below)
  COST_SPOT=last                       # accent char when SHEEN=spot: first|last|N
  COST_R0=224; COST_G0=228; COST_B0=235 # #e0e4eb — base colour
  COST_R1=223; COST_G1=241; COST_B1=241 # #DFF1F1 — accent colour
  EFF_C=$'\033[38;2;254;127;45m'       # #fe7f2d amber — same on every level
  DIR_C=$'\033[38;2;187;213;218m'      # #bbd5da pale ice — third line path
  GIT_C=$'\033[38;2;90;158;214m'
  DIRTY_C=$'\033[1;38;2;224;228;235m'
  TRI_C=$'\033[38;2;118;171;174m'      # #76ABAE — the ▲ marker
  HIT_LABEL_C=$'\033[38;2;48;56;65m'   # #303841 — the word 'hit'
  HIT_NUM_C=$'\033[38;2;255;0;0m'      # #FF0000 — the percentage
  CACHE_BLANK_C=$'\033[38;2;245;201;181m' # #F5C9B5 — an idle bucket wears the lowest step

  # --- the eight-step amplitude ramp ---------------------------------------
  # Bucket edges are 80, 82.5, 84, ... 100 — 2.5 points per step, eight steps.
  # Anything at or below 80% clamps into step 0, so the bar cannot show how far
  # below it went: that is what the printed percentage beside it is for.
  #
  # Colours are the agreed B ramp — the three supplied anchors (#FFC6B0 at 80%,
  # #A5D7D5 at 90%, #DFF1F1 at 100%) interpolated in OKLab — sampled at each of
  # the eight bucket midpoints:
  #   81.25 #F5C9B5   86.25 #CAD2C8   91.25 #ACDAD8   96.25 #C9E7E6
  #   83.75 #E1CDBE   88.75 #B2D5D1   93.75 #BBE1DF   98.75 #D8EEED
  # Adjacent OKLab ΔE runs 2.5-3.2 except 86.25->88.75, which is 1.4. That is
  # still above the ~1.0 just-noticeable threshold, but it is the weakest seam
  # in the set, and it sits on the 87.5 boundary — right where "nearly fine"
  # turns into "fine". If it ever needs widening, make the warm and cool halves
  # separately monotonic in lightness instead of interpolating between anchors.
  RAMP_C[0]=$'\033[38;2;245;201;181m'
  RAMP_C[1]=$'\033[38;2;225;205;190m'
  RAMP_C[2]=$'\033[38;2;202;210;200m'
  RAMP_C[3]=$'\033[38;2;178;213;209m'
  RAMP_C[4]=$'\033[38;2;172;218;216m'
  RAMP_C[5]=$'\033[38;2;187;225;223m'
  RAMP_C[6]=$'\033[38;2;201;231;230m'
  RAMP_C[7]=$'\033[38;2;216;238;237m'
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
  SHEEN=off                            # flat here: the 256 ramp has no mint step
  COST_SPOT=last                       # (unused while SHEEN=off)
  COST_R0=224; COST_G0=228; COST_B0=235
  COST_R1=223; COST_G1=241; COST_B1=241
  EFF_C=$'\033[38;5;208m'              # nearest 256 to #fe7f2d
  DIR_C=$'\033[38;5;152m'              # nearest 256 to #bbd5da
  GIT_C=$'\033[38;5;74m'
  DIRTY_C=$'\033[1;38;5;252m'
  TRI_C=$'\033[38;5;109m'              # nearest 256 to #76ABAE
  HIT_LABEL_C=$'\033[38;5;238m'        # nearest 256 to #303841
  HIT_NUM_C=$'\033[38;5;196m'          # nearest 256 to #FF0000
  CACHE_BLANK_C=$'\033[38;5;223m'      # nearest 256 to #F5C9B5 — the lowest step
  # The 256 cube cannot hold the mint end of the ramp, so these eight are chosen
  # by hand along the nearest faces: warm (223) -> grey-green (187) -> teal
  # (151/115) -> pale (152). Pairs repeat at the ends because the cube has no
  # closer entry, which is a real limitation of this branch, not an oversight.
  RAMP_C[0]=$'\033[38;5;223m'
  RAMP_C[1]=$'\033[38;5;223m'
  RAMP_C[2]=$'\033[38;5;187m'
  RAMP_C[3]=$'\033[38;5;151m'
  RAMP_C[4]=$'\033[38;5;115m'
  RAMP_C[5]=$'\033[38;5;115m'
  RAMP_C[6]=$'\033[38;5;152m'
  RAMP_C[7]=$'\033[38;5;152m'
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

# How many columns the cost occupies. Measured HERE, on the plain text, before
# the sheen below wraps every glyph in its own escape sequence — reading it
# afterwards would count ~18 bytes of SGR per character as if it were visible
# width and throw the layout arithmetic off.
cost_vis=${#cost_s}

# --- cost colouring: across characters, never inside one -------------------
# SHEEN=off       one flat colour (COST_C)
# SHEEN=gradient  each character drifts one step from the base toward the accent
# SHEEN=spot      all base except one character, chosen by COST_SPOT
#
# A terminal cell holds one character and one foreground colour, so a gradient
# *inside* a glyph is not expressible — the terminal paints the whole shape with
# the cell's single colour. Everything here is therefore quantised to one colour
# per character, which is what the eye reads as a sheen at this length.
if [ -n "$cost_s" ] && [ "$SHEEN" != "off" ]; then
  cost_render=""; clen=${#cost_s}; ci=0
  den=$(( clen - 1 ))
  [ "$den" -lt 1 ] && den=1
  while [ "$ci" -lt "$clen" ]; do
    ch=${cost_s:$ci:1}
    if [ "$SHEEN" = "spot" ]; then
      # Which character wears the accent: first, last, or a 1-based index.
      case "$COST_SPOT" in
        first)      spot=$(( 0 )) ;;
        last)       spot=$(( clen - 1 )) ;;
        ''|*[!0-9]*) spot=-1 ;;
        *)          spot=$(( COST_SPOT - 1 )) ;;
      esac
      if [ "$ci" -eq "$spot" ]; then
        r=$COST_R1; g=$COST_G1; b=$COST_B1
      else
        r=$COST_R0; g=$COST_G0; b=$COST_B0
      fi
    else
      # Round half away from zero, spelled out because bash truncates toward
      # zero: a negative numerator (the channels that drift DOWN to the accent)
      # would otherwise lose its rounding and stop one step short of the target.
      dr=$(( (COST_R1 - COST_R0) * ci ))
      dg=$(( (COST_G1 - COST_G0) * ci ))
      db=$(( (COST_B1 - COST_B0) * ci ))
      r=$(( COST_R0 + (dr >= 0 ? (dr + den / 2) / den : -((-dr + den / 2) / den)) ))
      g=$(( COST_G0 + (dg >= 0 ? (dg + den / 2) / den : -((-dg + den / 2) / den)) ))
      b=$(( COST_B0 + (db >= 0 ? (db + den / 2) / den : -((-db + den / 2) / den)) ))
    fi
    cost_render="${cost_render}"$'\033[38;2;'"${r};${g};${b}m${ch}"
    ci=$(( ci + 1 ))
  done
  cost_s="${cost_render}${RESET}"
fi

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
# With the sheen on, cost_s already carries its own per-character colour, so
# prefixing COST_C as well would only be overridden glyph by glyph.
if [ "$show_cost" = 1 ]; then
  if [ "$SHEEN" != "off" ]; then
    out1="${out1} ${cost_s}"
  else
    out1="${out1} ${COST_C}${cost_s}${RESET}"
  fi
fi

# ===========================================================================
# Line 3 — where you are
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

out3=""
[ -n "$short" ] && out3="${DIR_C}${short}${RESET}"
if [ -n "$branch" ]; then
  [ -n "$out3" ] && out3="${out3}${SEP_C} · ${RESET}"
  out3="${out3}${GIT_C}${branch}${RESET}${DIRTY_C}${dirty}${RESET}"
fi

# ===========================================================================
# Line 2 — cache hit history
# ===========================================================================
# The payload carries no history: prompt_cache is a cumulative snapshot, so a
# per-bucket rate has to be reconstructed by sampling it and differencing
# consecutive samples. Two running totals are needed, not one — a bucket's rate
# is (Δrequests − Δmisses) / Δrequests, and `misses` alone supplies only the
# numerator.
#
# The log therefore holds three fields: epoch seconds, cumulative requests,
# cumulative misses. Two-field records written by the earlier version of this
# script may still be on disk, so the reader demands exactly three numeric
# fields and drops anything else rather than mis-parsing a two-field line as a
# result.
CACHE_BUCKETS=12        # fixed bar length
CACHE_BUCKET_S=300      # seconds per bucket -> 12 x 5m = 60m
CACHE_KEEP=600          # lines retained before trimming
CACHE_LOG="${TMPDIR:-/tmp}/claude-statusline-cache-${UID:-0}.log"

cache_seg=""
if [ -n "$miss_raw" ] && [ -n "$req_raw" ]; then
  # The sample is appended here rather than from inside awk. Two redraws can
  # run at once, and a shell `printf >> file` is a single atomic write while
  # awk's buffered output is not: interleaved writes tear a record in half and
  # the mangled line then reads as a counter thousands of times too large.
  now=$(date +%s)
  # Seed the log with one record dated at the start of the window it is about
  # to cover. Without it the oldest sample in the file has no predecessor, its
  # delta cannot be formed, and the first bucket renders empty forever — the
  # window's leading edge would read as "nothing sent" even under full load.
  # The sentinel carries the same counters as the first real sample, so the
  # delta it enables is exactly zero and it cannot invent traffic.
  if [ ! -s "$CACHE_LOG" ]; then
    printf '%s\t%s\t%s\n' "$(( now - CACHE_BUCKETS * CACHE_BUCKET_S ))" \
           "$req_raw" "$miss_raw" >> "$CACHE_LOG" 2>/dev/null
  fi
  printf '%s\t%s\t%s\n' "$now" "$req_raw" "$miss_raw" >> "$CACHE_LOG" 2>/dev/null

  cache_seg=$(awk -v now="$now" -v logf="$CACHE_LOG" \
      -v nb="$CACHE_BUCKETS" -v bs="$CACHE_BUCKET_S" -v keep="$CACHE_KEEP" \
      -v rst="$RESET" -v blankc="$CACHE_BLANK_C" \
      -v c0="${RAMP_C[0]}" -v c1="${RAMP_C[1]}" -v c2="${RAMP_C[2]}" -v c3="${RAMP_C[3]}" \
      -v c4="${RAMP_C[4]}" -v c5="${RAMP_C[5]}" -v c6="${RAMP_C[6]}" -v c7="${RAMP_C[7]}" \
      -v b1="$RAMP_CH0" -v b2="$RAMP_CH1" -v b3="$RAMP_CH2" -v b4="$RAMP_CH3" \
      -v b5="$RAMP_CH4" -v b6="$RAMP_CH5" -v b7="$RAMP_CH6" -v b8="$RAMP_CH7" '
    BEGIN{
      FS = "\t"
      ramp[0]=c0; ramp[1]=c1; ramp[2]=c2; ramp[3]=c3
      ramp[4]=c4; ramp[5]=c5; ramp[6]=c6; ramp[7]=c7
      chr[0]=b1; chr[1]=b2; chr[2]=b3; chr[3]=b4
      chr[4]=b5; chr[5]=b6; chr[6]=b7; chr[7]=b8

      # --- read history ---------------------------------------------------
      n = 0; prev = ""
      while ((getline line < logf) > 0) {
        # Exactly three numeric fields, strictly increasing in time, counters
        # non-decreasing. Anything else is dropped: a torn line, a two-field
        # record from the previous version of this script, or a stale file from
        # before either format. Without the field check a merged line parses as
        # a bogus counter and poisons every delta that follows it.
        if (split(line, f, "\t") != 3) continue
        if (f[1] !~ /^[0-9]+$/ || f[2] !~ /^[0-9]+$/ || f[3] !~ /^[0-9]+$/) continue
        if (f[1] <= prev) continue
        prev = f[1]
        if (n > 0 && (f[2] < q[n-1] || f[3] < m[n-1])) continue   # counter went backwards
        t[n] = f[1]; q[n] = f[2]; m[n] = f[3]; n++
      }
      close(logf)
      if (n == 0) { print ""; exit }

      # --- trim to the newest `keep` samples ------------------------------
      # One record older than the kept range is retained on purpose. Deltas are
      # formed between consecutive samples, so the oldest kept sample needs a
      # predecessor to be worth anything: without it the leading edge of the
      # window has no delta and renders as "nothing sent" even under load. The
      # extra line is that predecessor.
      if (n > keep + 1) {
        tmp = logf ".tmp"
        for (i = n - keep - 1; i < n; i++) printf "%d\t%d\t%d\n", t[i], q[i], m[i] > tmp
        close(tmp)
        system("mv -f \"" tmp "\" \"" logf "\"")
      }

      # --- bucket the interval covered by the bar -------------------------
      # Each bucket keeps its own deltas, so its rate comes from the change
      # across it rather than from a global ratio — which is the whole point of
      # a history bar: one bad minute should show as one short column, not
      # vanish into a healthy running average.
      #
      # A bucket with no requests at all is left unset and renders as a bare
      # baseline mark, not as a zero rate: nothing was sent, which is not the
      # same as everything missing, and drawing it as a miss would invent an
      # outage every time the session sat idle.
      #
      # `now` is the current redraw, not the newest sample: after a quiet
      # stretch the newest sample is old, and anchoring the window to it would
      # slide the whole bar left and misplace every cell.
      start = now - nb * bs
      for (i = 0; i < nb; i++) { dq[i] = 0; dm[i] = 0; have[i] = 0 }
      for (i = 1; i < n; i++) {
        if (t[i] < start) continue
        # i-1 belongs to the previous sample, which may fall before `start`;
        # that is fine, the delta is still the right one.
        b = int((t[i] - start) / bs)
        if (b < 0 || b >= nb) continue
        dq[b] += q[i] - q[i-1]
        dm[b] += m[i] - m[i-1]
        have[b] = 1
      }

      # --- render ---------------------------------------------------------
      # Eight steps spanning 80-100%, 2.5 points each. A rate at or below 80
      # clamps into step 0, so the bar stops distinguishing "just under" from
      # "far under" — the printed percentage beside it carries that.
      # Steps are emitted one colour run at a time: one escape per run rather
      # than one per cell, which is both cheaper and easier on the eye.
      out = ""; run = ""; run_c = ""
      for (i = 0; i < nb; i++) {
        if (!have[i] || dq[i] <= 0) {
          c = blankc; ch = chr[0]
        } else {
          r = (dq[i] - dm[i]) / dq[i] * 100
          s = int((r - 80) / 2.5)
          if (s < 0) s = 0; if (s > 7) s = 7
          c = ramp[s]; ch = chr[s]
        }
        if (c != run_c && run != "") { out = out run_c run rst; run = "" }
        run_c = c; run = run ch
      }
      if (run != "") out = out run_c run rst
      print out
    }
  ' 2>/dev/null)
fi

out2=""
if [ -n "$hit_raw" ]; then
  # hit_ratio is 0..1. Done in shell rather than awk: one fewer process, and
  # the fields are already strings here.
  hit_hi=${hit_raw%%.*}
  hit_lo=${hit_raw#*.}
  hit_lo=${hit_lo}000
  hit_lo=${hit_lo:0:3}
  # Guard the base-10 conversion: a malformed payload must not abort the script.
  case "$hit_hi$hit_lo" in *[!0-9]*|'') hit_pct=0 ;; *)
    hit_pct=$(( hit_hi * 100 + (10#$hit_lo + 5) / 10 )) ;;
  esac
  out2="  ${TRI_C}▲${RESET} ${HIT_LABEL_C}hit${RESET} ${HIT_NUM_C}${hit_pct}%${RESET}"
  [ -n "$cache_seg" ] && out2="${out2}  ${cache_seg}"
fi

printf '%s\n' "$out1"
[ -n "$out2" ] && printf '%s\n' "$out2"
[ -n "$out3" ] && printf '%s\n' "$out3"
exit 0
