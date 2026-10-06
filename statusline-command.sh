#!/bin/bash
# Claude Code status line — "cockpit" layout, three logical rows
#   Opus 5.5  | high  tok 94.4k/200k (47%)  ■■■■■■■■■···   $0.42
#   ▲ hit 95.0%  ▁▂▃▄▅▆▇█████
#   ~/AI/CODEX/LLM MODEL TEST/Opus_5-5 · main*
#
# Palette follows the reference design: white-on-slate badge, powder-blue token
# numbers (bold) with unbolded parens, light-gray progress fill, dim-gray empty
# slots. Colours were sampled from the reference image rather than eyeballed.
# None has a precise 256-colour equivalent, so we emit truecolor and fall back
# to the nearest 256 entries when the terminal doesn't advertise 24-bit support.
#
# Dependencies: macOS Bash 3.2+ / Linux Bash, awk, git and Python 3.9+.
# Python is the real JSON parser and SQLite request ledger; jq is not needed.

input=$(cat)
RESET=$'\033[0m'
here=$(cd -- "$(dirname -- "$0")" && pwd)
model=""; effort=""; used=""; size=""; pct_label="?"; pct_bar=""
cost_raw=""; dir=""; cache_pct="?%"; cache_codes="????????????"; cache_write=-1
# Pin the interpreter validated by install.sh. The user's interactive shell
# can resolve a different python3 than the installation shell (e.g. Conda).
python_bin="${CACHE_HISTORY_PYTHON:-}"
if [ -z "$python_bin" ] && [ -r "$here/.python-path" ]; then
  IFS= read -r python_bin < "$here/.python-path"
fi
if [ -z "$python_bin" ] || ! command -v "$python_bin" >/dev/null 2>&1; then
  python_bin=python3
fi
fields=""; helper_ok=0
if command -v "$python_bin" >/dev/null 2>&1; then
  if [ -f "$here/lib/cache_history.py" ]; then
    if [ "${CACHE_HISTORY_DEBUG:-}" = 1 ]; then
      fields=$(printf '%s' "$input" | PYTHONDONTWRITEBYTECODE=1 "$python_bin" "$here/lib/cache_history.py")
    else
      fields=$(printf '%s' "$input" | PYTHONDONTWRITEBYTECODE=1 "$python_bin" "$here/lib/cache_history.py" 2>/dev/null)
    fi
    [ "$?" -eq 0 ] && [ -n "$fields" ] && helper_ok=1
  fi
  if [ "$helper_ok" != 1 ] && [ -f "$here/lib/statusline_input.py" ]; then
    # A ledger/import failure must not hide the model, tokens, cost or path.
    [ "${CACHE_HISTORY_DEBUG:-}" = 1 ] && printf '%s\n' 'statusline: history helper failed; preserving payload fields' >&2
    fields=$(printf '%s' "$input" | PYTHONDONTWRITEBYTECODE=1 "$python_bin" "$here/lib/statusline_input.py" 2>/dev/null)
  fi
  if [ -n "$fields" ]; then
    # A line per field retains empty and zero values on Bash 3.2 (IFS tab does not).
    {
      IFS= read -r model; IFS= read -r effort
      IFS= read -r used; IFS= read -r size
      IFS= read -r pct_label; IFS= read -r pct_bar
      IFS= read -r cost_raw; IFS= read -r dir
      IFS= read -r cache_pct; IFS= read -r cache_codes; IFS= read -r cache_write
    } <<< "$fields"
  fi
fi
case "$cache_codes" in
  *[!0-7?-]*) cache_codes="????????????"; cache_pct="?%" ;;
esac
[ "${#cache_codes}" -eq 12 ] || { cache_codes="????????????"; cache_pct="?%"; }
case "$cache_write" in ''|*[!0-9]*) cache_write=-1 ;; esac

# Preserve the installed original's display contract. Internal quality and
# unknown codes stay in the saved diagnostics, not as new UI symbols. With no
# measurable rate, 0.0% is the original placeholder (not measured 0% hits).
cache_pct=${cache_pct#\~}
case "$cache_pct" in
  '?%') cache_pct="0.0%" ;;
  *.*%) ;;
  *%) cache_pct="${cache_pct%\%}.0%" ;;
esac

# --- truecolor capability --------------------------------------------------
TC=0
case "${COLORTERM:-}" in truecolor|24bit) TC=1 ;; esac
[ "${TERM_PROGRAM:-}" = "iTerm.app" ] && TC=1
[ "${TERM_PROGRAM:-}" = "WezTerm" ]   && TC=1
case "${TERM:-}" in *-direct|*-truecolor) TC=1 ;; esac

# One UTF-8 glyph per original height step, U+2581 .. U+2588. Keep each
# atomic when wrapping, rather than indexing multibyte glyph strings in C locale.
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
  TRI_C=$'\033[38;2;235;223;51m'       # #EBDF33 — the ▲ glyph
  HIT_LABEL_C=$'\033[38;2;44;104;123m'  # #2C687B — 'hit'
  HIT_NUM_C=$'\033[1;38;2;255;0;0m'    # #FF0000 pure red, bold — flat fallback
  # The percentage is coloured by role: digits take these three in order
  # (punctuation does not advance the count), "." and "%" are fixed.
  HIT_ROLE=1
  HIT_DIG_C=($'\033[1;38;2;255;0;0m'   # #FF0000 first digit
             $'\033[1;38;2;255;27;29m' # #FF1B1D second digit
             $'\033[1;38;2;255;57;58m') # #FF393A third digit
  HIT_DOT_C=$'\033[1;38;2;192;197;201m' # #C0C5C9 decimal point
  HIT_PCT_C=$'\033[1;38;2;187;213;218m' # #BBD5DA percent sign
  # Badge number: one reference stop per character, decimal point included.
  WRITE_NUM_C=($'\033[1;38;2;35;136;168m' # #2388A8 — first character, bold
               $'\033[1;38;2;45;140;164m' # #2D8CA4
               $'\033[1;38;2;54;143;160m' # #368FA0
               $'\033[1;38;2;64;147;156m' # #40939C
               $'\033[1;38;2;74;150;152m') # #4A9698 — fifth and later
  WRITE_UNIT_C=$'\033[1;38;2;230;173;53m' # #E6AD35 — badge M, bold
  WRITE_K_C=$'\033[1;38;2;239;211;82m'   # #EFD352 — badge k, bold
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

  TRI_C=$'\033[38;5;220m'              # nearest 256 to #EBDF33
  HIT_LABEL_C=$'\033[38;5;248m'        # original project's 256-color 'hit' label
  HIT_NUM_C=$'\033[1;38;5;196m'        # nearest 256 to #FF0000, bold
  HIT_ROLE=0                           # flat here: the pale tones have no close 256 entry
  # Nearest 256 entries; repeated stops reflect the limited colour cube.
  WRITE_NUM_C=($'\033[1;38;5;31m'      # #2388A8, bold
               $'\033[1;38;5;31m'      # #2D8CA4
               $'\033[1;38;5;67m'      # #368FA0
               $'\033[1;38;5;67m'      # #40939C
               $'\033[1;38;5;66m')     # #4A9698
  WRITE_UNIT_C=$'\033[1;38;5;178m'     # nearest 256 to #E6AD35, bold
  WRITE_K_C=$'\033[1;38;5;221m'       # nearest 256 to #EFD352, bold
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

write_badge=""; write_text=""; write_number=""; write_unit=""
if [ "$cache_write" != -1 ]; then
  write_text=$(humanise "$cache_write")
  case "$write_text" in
    *k) write_number=${write_text%k}; write_unit=k ;;
    *M) write_number=${write_text%M}; write_unit=M ;;
    *)  write_number=$write_text ;;
  esac
  write_badge="💭 "; write_i=0
  while [ "$write_i" -lt "${#write_number}" ]; do
    # Keep the specified left-to-right stops; do not wrap on long numbers.
    write_color_i=$write_i
    [ "$write_color_i" -lt "${#WRITE_NUM_C[@]}" ] || write_color_i=$(( ${#WRITE_NUM_C[@]} - 1 ))
    write_badge="${write_badge}${WRITE_NUM_C[$write_color_i]}${write_number:$write_i:1}"
    write_i=$(( write_i + 1 ))
  done
  write_badge="${write_badge}${RESET}"
  if [ -n "$write_unit" ]; then
    write_unit_color=$WRITE_UNIT_C
    [ "$write_unit" = k ] && write_unit_color=$WRITE_K_C
    write_badge="${write_badge}${write_unit_color}${write_unit}${RESET}"
  fi
fi

# --- model badge (drop any "(1M context)" qualifier to stay compact) -------
model=$(printf '%s' "$model" | sed 's/ *(.*)$//')
[ -z "$model" ] && model="Claude Code"

# --- input-side context; missing is not zero ------------------------------
used_s="?"; size_s="?"
case "$used" in ''|*[!0-9]*) ;; *) used_s=$(humanise "$used") ;; esac
case "$size" in ''|*[!0-9]*) ;; *) size_s=$(humanise "$size") ;; esac
pct=${pct_bar:-0}  # only for progress fill; unknown percent stays '?' in text

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

plain_tok="tok ${used_s}/${size_s} (${pct_label}%)"
tok_seg="  ${TOK_LABEL_C}tok${RESET} ${TOK_NUM_C}${used_s}/${size_s}${RESET} ${TOK_PUNC_C}(${RESET}${TOK_NUM_C}${pct_label}%${RESET}${TOK_PUNC_C})${RESET}"
tok_len=$(( 2 + ${#plain_tok} ))
have_tok=0
[ -n "$pct_bar" ] && have_tok=1

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
# Line 2 — immutable until new committed request usage, never wall time/TTL.
# '?' unknown usage; unused history and real <=80% share the lowest '▁'.
# Empty clock intervals are skipped; left padding contributes no token weight.
# Original eight heights, 80–100% enlarged scale and colours are unchanged.
# Every layout retains the label, percentage and ALL twelve history cells.
# ===========================================================================
# Claude Code 2.1.288 reserves two cells at each footer edge. A line that
# fits COLUMNS can still be clipped by the host, so reserve its measured margin.
# Custom padding/other hosts can explicitly override the reservation.
margin=${CACHE_HOST_MARGIN:-4}
case "$margin" in ''|*[!0-9]*) margin=4 ;; esac
content_cols=$(( cols - margin ))
[ "$content_cols" -lt 1 ] && content_cols=1
cache_bar=""; bar_col=0
for (( i=0; i<12; i++ )); do
  code=${cache_codes:$i:1}
  case "$code" in
    '?') cell="${CACHE_BLANK_C}${RAMP_CH0}${RESET}" ;;
    '-') cell="${RAMP_C[0]}${RAMP_CH0}${RESET}" ;;
    0) cell="${RAMP_C[0]}${RAMP_CH0}${RESET}" ;;
    1) cell="${RAMP_C[1]}${RAMP_CH1}${RESET}" ;;
    2) cell="${RAMP_C[2]}${RAMP_CH2}${RESET}" ;;
    3) cell="${RAMP_C[3]}${RAMP_CH3}${RESET}" ;;
    4) cell="${RAMP_C[4]}${RAMP_CH4}${RESET}" ;;
    5) cell="${RAMP_C[5]}${RAMP_CH5}${RESET}" ;;
    6) cell="${RAMP_C[6]}${RAMP_CH6}${RESET}" ;;
    7) cell="${RAMP_C[7]}${RAMP_CH7}${RESET}" ;;
  esac
  if [ "$bar_col" -ge "$content_cols" ]; then
    cache_bar="${cache_bar}"$'\n'; bar_col=0
  fi
  cache_bar="${cache_bar}${cell}"; bar_col=$(( bar_col + 1 ))
done
# The original percentage roles: digits progress through three reds;
# punctuation keeps its own colour and never advances the digit index.
# The same cells are reused below when wrapping, so narrow mode matches too.
pct_out=""; pct_cells=(); di=0
for (( i=0; i<${#cache_pct}; i++ )); do
  ch=${cache_pct:$i:1}; ch_c=$HIT_NUM_C
  if [ "$HIT_ROLE" = 1 ]; then
    case "$ch" in
      .) ch_c=$HIT_DOT_C ;;
      %) ch_c=$HIT_PCT_C ;;
      *) ch_c=${HIT_DIG_C[di < 2 ? di : 2]}; di=$(( di + 1 )) ;;
    esac
  fi
  pct_cells[${#pct_cells[@]}]="${ch_c}${ch}${RESET}"
  [ "$HIT_ROLE" = 1 ] && pct_out="${pct_out}${ch_c}${ch}"
done
if [ "$HIT_ROLE" = 1 ]; then
  pct_out="${pct_out}${RESET}"
else
  pct_out="${HIT_NUM_C}${cache_pct}${RESET}"
fi
pfx="${TRI_C}▲${RESET} ${HIT_LABEL_C}hit${RESET} ${pct_out}"
cache_width=$(( 6 + ${#cache_pct} + 2 + 12 ))
badge_tail=""; badge_extra=0
if [ -n "$write_badge" ]; then
  # Three plain spaces, a two-cell emoji, one space, and the ASCII value.
  badge_extra=$(( 6 + ${#write_text} ))
fi
if [ "$content_cols" -lt "$cache_width" ]; then
  # Prefix glyphs are atomic as well, even with a C locale (▲ is multibyte).
  # Continuations keep every label/percentage character instead of truncating.
  prefix_cells=("${TRI_C}▲${RESET}" " " "${HIT_LABEL_C}h${RESET}" "${HIT_LABEL_C}i${RESET}" "${HIT_LABEL_C}t${RESET}" " ")
  for (( i=0; i<${#cache_pct}; i++ )); do
    prefix_cells[${#prefix_cells[@]}]="${pct_cells[$i]}"
  done
  pfx_wrap=""; prefix_col=0
  for (( i=0; i<${#prefix_cells[@]}; i++ )); do
    if [ "$prefix_col" -ge "$content_cols" ]; then
      pfx_wrap="${pfx_wrap}"$'\n'; prefix_col=0
    fi
    pfx_wrap="${pfx_wrap}${prefix_cells[$i]}"; prefix_col=$(( prefix_col + 1 ))
  done
  if [ -n "$write_badge" ] && [ "$(( bar_col + badge_extra ))" -le "$content_cols" ]; then
    badge_tail="   ${write_badge}"
  fi
  out2="${pfx_wrap}"$'\n'"${cache_bar}${badge_tail}"
else
  if [ -n "$write_badge" ] && [ "$(( cache_width + badge_extra ))" -le "$content_cols" ]; then
    badge_tail="   ${write_badge}"
  fi
  out2="${pfx}  ${cache_bar}${badge_tail}"
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

printf '%s\n' "$out1"
[ -n "$out2" ] && printf '%s\n' "$out2"
[ -n "$out3" ] && printf '%s\n' "$out3"
exit 0
