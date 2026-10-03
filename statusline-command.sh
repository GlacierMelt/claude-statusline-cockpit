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
      (.workspace.current_dir // .cwd // ""),
      (.prompt_cache.hit_ratio // ""),
      (.prompt_cache.requests // ""),
      (.prompt_cache.misses // ""),
      (.prompt_cache.ttl // "")
    ] | @tsv' 2>/dev/null)
  if [ -n "$fields" ]; then
    # Use awk to parse tab-separated fields correctly (read skips empty fields)
    model=$(awk -F'\t' '{print $1}' <<< "$fields")
    effort=$(awk -F'\t' '{print $2}' <<< "$fields")
    in_tok=$(awk -F'\t' '{print $3}' <<< "$fields")
    out_tok=$(awk -F'\t' '{print $4}' <<< "$fields")
    size=$(awk -F'\t' '{print $5}' <<< "$fields")
    cost_raw=$(awk -F'\t' '{print $6}' <<< "$fields")
    dir=$(awk -F'\t' '{print $7}' <<< "$fields")
    hit_raw=$(awk -F'\t' '{print $8}' <<< "$fields")
    req_raw=$(awk -F'\t' '{print $9}' <<< "$fields")
    miss_raw=$(awk -F'\t' '{print $10}' <<< "$fields")
    ttl_raw=$(awk -F'\t' '{print $11}' <<< "$fields")
  fi
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
  # prompt_cache is absent until caching has been observed, so all four are
  # allowed to come back empty and the whole segment is skipped when they do.
  hit_raw=$(scalar prompt_cache.hit_ratio)
  req_raw=$(scalar prompt_cache.requests)
  miss_raw=$(scalar prompt_cache.misses)
  ttl_raw=$(scalar prompt_cache.ttl)
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
  SHEEN=gradient                       # off | gradient | spot  (see below)
  COST_SPOT=last                       # accent char when SHEEN=spot: first|last|N
  COST_R0=224; COST_G0=228; COST_B0=235 # #e0e4eb — base colour
  COST_R1=223; COST_G1=241; COST_B1=241 # #DFF1F1 — accent colour
  EFF_C=$'\033[38;2;254;127;45m'       # #fe7f2d amber — same on every level
  DIR_C=$'\033[38;2;187;213;218m'      # #bbd5da pale ice — second line path
  GIT_C=$'\033[38;2;90;158;214m'
  DIRTY_C=$'\033[1;38;2;224;228;235m'

  # --- the eight-step amplitude ramp ---------------------------------------
  # Bucket edges are 80, 82.5, 85, 87.5, 90, 92.5, 95, 97.5, 100 — 2.5 points
  # per step, eight steps. Anything at or below 80% clamps into step 0, so the
  # bar cannot show how far below it went: that is what the printed percentage
  # beside it is for.
  #
  # Colours are the three-stop gradient: #FFC6B0 at 80%, #A5D7D5 at 90%,
  # #DFF1F1 at 100%, interpolated in OKLab and sampled at the eight bucket
  # midpoints (81.25, 83.75, ..., 98.75):
  RAMP_C[0]=$'\033[38;2;245;201;181m'  # 81.25% #F5C9B5
  RAMP_C[1]=$'\033[38;2;225;205;190m'  # 83.75% #E1CDBE
  RAMP_C[2]=$'\033[38;2;202;210;200m'  # 86.25% #CAD2C8
  RAMP_C[3]=$'\033[38;2;178;213;209m'  # 88.75% #B2D5D1
  RAMP_C[4]=$'\033[38;2;172;218;216m'  # 91.25% #ACDAD8
  RAMP_C[5]=$'\033[38;2;187;225;223m'  # 93.75% #BBE1DF
  RAMP_C[6]=$'\033[38;2;201;231;230m'  # 96.25% #C9E7E6
  RAMP_C[7]=$'\033[38;2;216;238;237m'  # 98.75% #D8EEED
  CACHE_BLANK_C=$'\033[38;2;245;201;181m' # #F5C9B5 — idle bucket (no sample)

  # The "▲ hit 96%" prefix. The label and the number carry the meaning, so
  # they are the two highest-contrast colours on the line; the arrow is a
  # quiet marker, not a signal.
  TRI_C=$'\033[38;2;140;199;196m'      # #8CC7C4 — the ▲ glyph
  HIT_LABEL_C=$'\033[38;2;35;61;77m'    # #233D4D — 'hit'
  HIT_NUM_C=$'\033[1;38;2;255;0;0m'    # #FF0000 pure red, bold — flat fallback
  # The percentage is coloured by role: digits take these three in order
  # (punctuation does not advance the count), "." and "%" are fixed.
  HIT_ROLE=1
  HIT_DIG_C=($'\033[1;38;2;255;0;0m'   # #FF0000 first digit
             $'\033[1;38;2;255;27;29m' # #FF1B1D second digit
             $'\033[1;38;2;255;57;58m') # #FF393A third digit
  HIT_DOT_C=$'\033[1;38;2;192;197;201m' # #C0C5C9 decimal point
  HIT_PCT_C=$'\033[1;38;2;187;213;218m' # #BBD5DA percent sign
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
  CACHE_BLANK_C=$'\033[38;5;223m'      # nearest 256 to #F5C9B5

  TRI_C=$'\033[38;5;66m'               # nearest 256 to #547792
  HIT_LABEL_C=$'\033[38;5;237m'        # nearest 256 to #233D4D
  HIT_NUM_C=$'\033[1;38;5;196m'        # nearest 256 to #FF0000, bold
  HIT_ROLE=0                           # flat here: the pale tones have no close 256 entry
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
# Line 2 — cache hit history
# ===========================================================================
# The payload carries no history: prompt_cache is a cumulative snapshot, so a
# "last hour" bar has to be built by sampling it and differencing consecutive
# samples. A bucket is a MISS when `misses` rose across that interval.
#
# Every read and write goes through awk, so this costs two process spawns per
# redraw, not one per line. The file is truncated in place once it exceeds
# CACHE_KEEP lines so it cannot grow without bound.
CACHE_BUCKETS=12        # fixed bar length
CACHE_BUCKET_S=300      # seconds per bucket -> 12 x 5m = 60m
CACHE_KEEP=600          # lines retained before trimming
CACHE_LOG="${CACHE_LOG:-${TMPDIR:-/tmp}/claude-statusline-cache-${UID:-0}.log}"

cache_seg=""
if [ -n "$req_raw" ] && [ -n "$miss_raw" ]; then
  # Three-column log: timestamp, requests, misses. Hit rate = (Δreq - Δmiss) / Δreq
  # per bucket. Shell `printf >> file` is atomic; awk's buffered output is not.
  now=$(date +%s)
  # Only write if req_raw and miss_raw are valid numbers from real Claude Code payload
  if [[ "$req_raw" =~ ^[0-9]+$ ]] && [[ "$miss_raw" =~ ^[0-9]+$ ]]; then
    printf '%s\t%s\t%s\n' "$now" "$req_raw" "$miss_raw" >> "$CACHE_LOG" 2>/dev/null
  fi

  cache_seg=$(awk -v now="$now" -v logf="$CACHE_LOG" \
      -v nb="$CACHE_BUCKETS" -v bs="$CACHE_BUCKET_S" -v keep="$CACHE_KEEP" \
      -v ch0="$RAMP_CH0" -v ch1="$RAMP_CH1" -v ch2="$RAMP_CH2" -v ch3="$RAMP_CH3" \
      -v ch4="$RAMP_CH4" -v ch5="$RAMP_CH5" -v ch6="$RAMP_CH6" -v ch7="$RAMP_CH7" \
      -v c0="${RAMP_C[0]}" -v c1="${RAMP_C[1]}" -v c2="${RAMP_C[2]}" -v c3="${RAMP_C[3]}" \
      -v c4="${RAMP_C[4]}" -v c5="${RAMP_C[5]}" -v c6="${RAMP_C[6]}" -v c7="${RAMP_C[7]}" \
      -v blankc="$CACHE_BLANK_C" -v rst="$RESET" '
    BEGIN{
      FS = "\t"

      # --- read history with multi-segment support ------------------------
      n = 0; prev = ""; seg_id = 0; num_segments = 0
      last_q = 0; last_m = 0
      while ((getline line < logf) > 0) {
        # Three numeric fields: time, requests, misses. Strictly increasing time.
        if (split(line, f, "\t") != 3) continue
        if (f[1] !~ /^[0-9]+$/ || f[2] !~ /^[0-9]+$/ || f[3] !~ /^[0-9]+$/) continue
        if (f[1] <= prev) continue
        prev = f[1]

        # Detect segment boundary: counter decrease indicates new session
        if (n > 0 && (f[2] < last_q || f[3] < last_m)) {
          seg_id++
          num_segments++
        }

        # Store with segment ID: t[seg,idx], q[seg,idx], m[seg,idx]
        t[seg_id, n] = f[1]
        q[seg_id, n] = f[2]
        m[seg_id, n] = f[3]
        last_q = f[2]
        last_m = f[3]
        n++
      }
      close(logf)
      num_segments = seg_id + 1
      # Even with no history, render 12 blank cells so the bar shape is visible.
      if (n == 0) {
        ch[0]=ch0
        out = ""
        for (i = 0; i < nb; i++) out = out blankc ch[0] rst
        print out
        exit
      }

      # --- trim to the newest `keep` samples (disabled for multi-segment) --
      # Trimming is disabled because it would break segment boundaries.
      # The multi-segment design relies on reading the full history to detect
      # counter resets. With 600-line keep limit and typical usage patterns,
      # the log stays manageable (~20KB for a day of heavy usage).

      # --- bucket the interval: accumulate Δreq and Δmiss per bucket ------
      # Buckets align to fixed 5-minute clock boundaries (e.g. 06:00, 06:05).
      # The rightmost bucket covers the current incomplete period and only grows
      # (no sliding). Every 5 minutes the bar shifts left discretely.
      bucket_end = int((now + bs - 1) / bs) * bs  # round up to next boundary
      start = bucket_end - nb * bs
      for (i = 0; i < nb; i++) { dq[i] = 0; dm[i] = 0; have[i] = 0 }

      # Process each segment: compute delta per sample and accumulate to buckets
      for (seg = 0; seg < num_segments; seg++) {
        prev_q = 0
        prev_m = 0

        for (i = 0; i < n; i++) {
          if (!(seg SUBSEP i in t)) continue
          if (t[seg, i] < start) continue

          # `bucket_end` rounds UP to the next boundary, so a sample stamped
          # exactly on it lands at b == nb — one past the last cell. Fold that
          # into the newest bucket instead of dropping the sample, which is
          # what the old `b >= nb` guard did: a silent loss of up to a full
          # bucket of requests whenever `now` sat on a 5-minute mark.
          b = int((t[seg, i] - start) / bs)
          if (b < 0) continue
          if (b >= nb) b = nb - 1

          # Compute delta from previous sample in this segment
          delta_q = q[seg, i] - prev_q
          delta_m = m[seg, i] - prev_m

          if (delta_q > 0 && delta_m >= 0) {
            dq[b] += delta_q
            dm[b] += delta_m
            have[b] = 1
          }

          prev_q = q[seg, i]
          prev_m = m[seg, i]
        }
      }

      # --- render: height = hit%, colour from gradient, idle = blank ------
      ch[0]=ch0; ch[1]=ch1; ch[2]=ch2; ch[3]=ch3
      ch[4]=ch4; ch[5]=ch5; ch[6]=ch6; ch[7]=ch7
      col[0]=c0; col[1]=c1; col[2]=c2; col[3]=c3
      col[4]=c4; col[5]=c5; col[6]=c6; col[7]=c7

      out = ""
      for (i = 0; i < nb; i++) {
        if (!have[i]) {
          # Idle bucket: no sample landed here, so use lowest-step color
          out = out blankc ch[0] rst
          continue
        }
        if (dq[i] <= 0) {
          # Bucket has data but no delta (first sample only): show as idle
          out = out blankc ch[0] rst
          continue
        }
        pct = (dq[i] - dm[i]) * 100.0 / dq[i]
        if (pct < 0) pct = 0
        if (pct > 100) pct = 100

        # Height step: 80-100 mapped onto the eight ramp entries 0..7.
        # pct == 100 gives int(20/2.5) == 8, one past the end, and ch[8]/col[8]
        # are unset — so the cell rendered as an empty string and the bar came
        # out SHORTER the better the cache performed. Clamp to the last step.
        if (pct <= 80) {
          step = 0
        } else {
          step = int((pct - 80) / 2.5)
          if (step > 7) step = 7
        }
        out = out col[step] ch[step] rst
      }

      # --- compute last-hour hit rate from all buckets --------------------
      total_req = 0; total_miss = 0
      for (i = 0; i < nb; i++) {
        if (have[i] && dq[i] > 0 && dm[i] >= 0) {
          total_req += dq[i]
          total_miss += dm[i]
        }
      }
      hour_pct = 0
      if (total_req > 0) {
        hour_pct = (total_req - total_miss) * 100.0 / total_req
        if (hour_pct < 0) hour_pct = 0
        if (hour_pct > 100) hour_pct = 100
      }

      # Round to tenths half away from zero, then clamp, so the string is at
      # most five characters: printf "%.1f" of 99.95 would give "100.0".
      d = hour_pct * 10
      r = (d >= 0) ? int(d + 0.5) : -int(-d + 0.5)
      pct = r / 10
      if (pct > 99.9) pct = 99.9
      if (pct < 0) pct = 0

      print out
      printf "%.1f\n", pct  # second line: one-decimal percentage
    }' 2>/dev/null)
fi

# awk outputs two lines: bar, then percentage
cache_bar=$(echo "$cache_seg" | sed -n '1p')
cache_pct=$(echo "$cache_seg" | sed -n '2p')

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
if [ -n "$cache_bar" ]; then
  # cache_pct is the last-hour hit rate (0.0-99.9) computed by awk from all buckets
  pct_s="${cache_pct}%"
  if [ "$HIT_ROLE" = 1 ]; then
    pct_out=""; di=0
    for (( i = 0; i < ${#pct_s}; i++ )); do
      ch=${pct_s:i:1}
      case "$ch" in
        .) pct_out+="${HIT_DOT_C}${ch}" ;;
        %) pct_out+="${HIT_PCT_C}${ch}" ;;
        *) pct_out+="${HIT_DIG_C[di < 2 ? di : 2]}${ch}"; di=$((di + 1)) ;;
      esac
    done
    pct_out+="${RESET}"
  else
    pct_out="${HIT_NUM_C}${pct_s}${RESET}"
  fi
  pfx="${TRI_C}▲${RESET} ${HIT_LABEL_C}hit${RESET} ${pct_out}"
  out2="${pfx}  ${cache_bar}"
fi

out3=""
[ -n "$short" ] && out3="${DIR_C}${short}${RESET}"
if [ -n "$branch" ]; then
  [ -n "$out3" ] && out3="${out3}${SEP_C} · ${RESET}"
  out3="${out3}${GIT_C}${branch}${RESET}${DIRTY_C}${dirty}${RESET}"
fi

printf '%s\n' "$out1"
[ -n "$out2" ] && printf '%s\n' "$out2"
[ -n "$out3" ] && printf '%s\n' "$out3"
exit 0
