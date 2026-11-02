#!/bin/sh
# MemexOS installer for macOS and Linux (on Windows, run it inside WSL).
#
#   curl -fsSL https://raw.githubusercontent.com/liushengyu/memexos/main/install.sh | sh
#   sh install.sh [--yes] [--with LIST] [--lang en|zh] [--no-setup] [--minimal]
#
# Always installed, because MemexOS does not work fully without them:
#   uv and MemexOS; Node.js 22+; qmd, the local search engine; a model command line you are logged in to
#   (Claude Code or Codex; skipped when you already have one, an API key or a local model server); then
#   `memex setup`, which also downloads the search models (about 1 GB).
# Optional, you choose (asked, or --with a,b,c / --with all):
#   second-cli   the other model command line, so writing and reviewing use different model families (recommended)
#   video        yt-dlp, ffmpeg, whisper.cpp and a speech model (~550 MB): videos and podcasts, also without subtitles
#   pdf          poppler: PDFs
#   social       collect what you save on X, YouTube and Bilibili: twitter-cli, yt-dlp, and a browser for the
#                one-time logins (Chrome for Testing if you have no Chrome, Chromium, Edge or Brave)
#   deep-search  qmd's query expansion model (~1.3 GB), only for memex search --mode deep
#
#   --yes        no questions: the required parts, plus the optional ones named in --with
#   --with LIST  optional parts to install, comma-separated, or "all"
#   --lang       en or zh: the language of MemexOS (default: from $LANG); also picks the embedding model
#   --minimal    programs only: no model command line, no logins, no model downloads (for CI and containers)
#   --no-setup   stop before `memex setup`
#
# It skips what is already installed and can be run again at any time. Everything goes into your home directory
# (~/.local); sudo is only used for system packages on Linux, after you say yes.
# Environment: MEMEX_SOURCE (a local checkout or a pip/uv requirement to install instead of the GitHub repository).

set -eu

REPO="https://github.com/liushengyu/memexos"
NODE_LINE="latest-v24.x"
PLAYWRIGHT="playwright@1.64"
WHISPER_MODEL="ggml-large-v3-turbo-q5_0.bin"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
BIN="$HOME/.local/bin"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/memexos"
OPTIONAL="second-cli video pdf social deep-search"

# ---------------------------------------------------------------- helpers
if [ -t 1 ]; then B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; R="$(printf '\033[0m')"
else B=""; D=""; G=""; Y=""; R=""; fi
step() { printf '\n%s%s%s\n' "$B" "$*" "$R"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$R" "$*"; }
note() { printf '  %s·%s %s\n' "$Y" "$R" "$*"; }
info() { printf '  %s%s%s\n' "$D" "$*" "$R"; }
die()  { printf '\n%sStopped:%s %s\n' "$B" "$R" "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }
want() { case ",$WITH," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }

TTY=""
if [ -r /dev/tty ] && (exec </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi
interactive() { [ "$YES" = 0 ] && [ -n "$TTY" ]; }

# ask "question" default(y|n) → returns 0 for yes
ask() {
  if ! interactive; then [ "$2" = y ]; return; fi
  if [ "$2" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  printf '  %s %s ' "$1" "$hint" >"$TTY"
  read -r reply <"$TTY" || reply=""
  case "$reply" in
    [Yy]*) return 0 ;;
    [Nn]*) return 1 ;;
    *) [ "$2" = y ] ;;
  esac
}

# choose "question" default option... → prints the chosen option
choose() {
  q="$1"; def="$2"; shift 2
  if ! interactive; then echo "$def"; return; fi
  printf '  %s (%s) [%s] ' "$q" "$*" "$def" >"$TTY"
  read -r reply <"$TTY" || reply=""
  for o in "$@"; do [ "$reply" = "$o" ] && { echo "$o"; return; }; done
  echo "$def"
}

download() {   # url file
  curl -fL --retry 3 -C - --progress-bar -o "$2.part" "$1" && mv "$2.part" "$2"
}

node_major() {
  has node || { echo 0; return; }
  node --version 2>/dev/null </dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p' | head -1
}

link_bin() {   # source-file name
  mkdir -p "$BIN"
  ln -sf "$1" "$BIN/$2"
}

npm_global() {   # package: into npm's global prefix when writable, else ~/.local
  prefix="$(npm prefix -g 2>/dev/null </dev/null || echo /usr)"
  if [ -w "$prefix/lib" ] 2>/dev/null || [ -w "$prefix" ]; then
    npm install -g --no-fund --no-audit --loglevel=error "$1" </dev/null
  else
    npm install -g --no-fund --no-audit --loglevel=error --prefix "$HOME/.local" "$1" </dev/null
  fi
  hash -r 2>/dev/null || true
}

sys_install() {   # packages... (Linux; names for Debian/Ubuntu, mapped for dnf and pacman)
  [ "$#" -gt 0 ] || return 0
  if [ "$(id -u)" = 0 ]; then SUDO=""; else SUDO="sudo "; fi
  if has apt-get; then cmd="${SUDO}apt-get install -y $*"
  elif has dnf; then cmd="${SUDO}dnf install -y $(echo " $* " | sed 's/ g++ / gcc-c++ /g')"
  elif has pacman; then cmd="${SUDO}pacman -S --needed --noconfirm $(echo " $* " | sed 's/ poppler-utils / poppler /g; s/ g++ / gcc /g; s/ python3 / python /g')"
  else note "install with your package manager: $*"; return 1; fi
  if ask "Run: $cmd ?" y; then
    if has apt-get && [ -z "${APT_UPDATED:-}" ]; then sh -c "${SUDO}apt-get update -qq" </dev/null || true; APT_UPDATED=1; fi
    sh -c "$cmd" </dev/null || { note "the system packages did not install"; return 1; }
  else return 1; fi
}

find_browser() {   # a Chromium-family browser for site logins, as memexos.capture.browser.find looks for them
  for app in "Google Chrome" "Chromium" "Microsoft Edge" "Brave Browser" "Google Chrome for Testing"; do
    for root in /Applications "$HOME/Applications"; do
      [ -x "$root/$app.app/Contents/MacOS/$app" ] && { echo "$root/$app.app/Contents/MacOS/$app"; return 0; }
    done
  done
  for n in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser; do
    has "$n" && { command -v "$n"; return 0; }
  done
  for p in "$SHARE"/browsers/chromium-*/chrome-mac*/*.app/Contents/MacOS/* "$SHARE"/browsers/chromium-*/chrome-linux*/chrome; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}

claude_logged_in() { claude auth status --json 2>/dev/null </dev/null | grep -q '"loggedIn": *true'; }
codex_logged_in() { codex login status >/dev/null 2>&1 </dev/null; }

install_cli() {   # claude | codex
  case "$1" in
    claude) has claude || { curl -fsSL https://claude.ai/install.sh | bash || note "Claude Code did not install"; hash -r 2>/dev/null || true; } ;;
    codex)  has codex || { if has npm; then npm_global @openai/codex; else note "Codex needs Node.js"; fi; } ;;
  esac
}

login_cli() {   # claude | codex: the login opens a page in your browser
  case "$1" in
    claude)
      has claude || return 0
      if claude_logged_in; then ok "Claude Code is logged in"; return 0; fi
      if [ -n "$TTY" ] && ask "Log in to Claude Code now? A page opens in your browser." y; then
        claude auth login <"$TTY" >"$TTY" 2>&1 || true
      fi
      if claude_logged_in; then ok "Claude Code is logged in"; else note "Claude Code is not logged in yet: run 'claude auth login'"; fi ;;
    codex)
      has codex || return 0
      if codex_logged_in; then ok "Codex is logged in"; return 0; fi
      if [ -n "$TTY" ] && ask "Log in to Codex now? A page opens in your browser." y; then
        # without a screen (SSH, a container) Codex cannot receive the browser's answer: use a code you enter on the page
        if [ "$PLATFORM" = linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then codex login --device-auth <"$TTY" >"$TTY" 2>&1 || true
        elif [ -n "${SSH_CONNECTION:-}" ]; then codex login --device-auth <"$TTY" >"$TTY" 2>&1 || true
        else codex login <"$TTY" >"$TTY" 2>&1 || true; fi
      fi
      if codex_logged_in; then ok "Codex is logged in"; else note "Codex is not logged in yet: run 'codex login'"; fi ;;
  esac
}

# ---------------------------------------------------------------- main
# Everything runs from main, called on the last line, so the shell has read the whole script before anything
# runs (with `curl … | sh` the script arrives on standard input, which a child command could otherwise consume).
main() {
YES=0
MINIMAL=0
SETUP=1
WITH=""
WITH_SET=0
LANG_CHOICE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --minimal) MINIMAL=1 ;;
    --no-setup) SETUP=0 ;;
    --with) shift; WITH="${1:-}"; WITH_SET=1 ;;
    --with=*) WITH="${1#--with=}"; WITH_SET=1 ;;
    --lang) shift; LANG_CHOICE="${1:-}" ;;
    --lang=*) LANG_CHOICE="${1#--lang=}" ;;
    -h|--help) sed -n '2,/^$/p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
[ "$WITH" = all ] && WITH="$(echo "$OPTIONAL" | tr ' ' ',')"
for w in $(echo "$WITH" | tr ',' ' '); do
  case " $OPTIONAL " in *" $w "*) ;; *) die "unknown optional part: $w (choose from: $OPTIONAL)" ;; esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) PLATFORM=darwin ;;
  Linux) PLATFORM=linux ;;
  *) die "MemexOS runs on macOS and Linux (on Windows, inside WSL). This system is $OS." ;;
esac
case "$ARCH" in
  x86_64|amd64) NODE_ARCH=x64 ;;
  arm64|aarch64) NODE_ARCH=arm64 ;;
  *) NODE_ARCH="" ;;
esac
BREW=""
if has brew && [ "${MEMEX_INSTALL_NO_BREW:-0}" != 1 ]; then BREW=brew; fi

case ":$PATH:" in *":$BIN:"*) ;; *) PATH="$BIN:$PATH"; export PATH; PATH_ADDED=1 ;; esac

printf '%sMemexOS installer%s  %s(%s %s)%s\n' "$B" "$R" "$D" "$OS" "$ARCH" "$R"

# ---------------------------------------------------------------- 0. what to install
if [ -z "$LANG_CHOICE" ]; then
  case "${LC_ALL:-${LANG:-}}" in zh*) LANG_CHOICE=zh ;; *) LANG_CHOICE=en ;; esac
  LANG_CHOICE="$(choose "Language for MemexOS" "$LANG_CHOICE" en zh)"
fi
case "$LANG_CHOICE" in en|zh) ;; *) die "--lang must be en or zh" ;; esac

step "What gets installed"
info "Required: uv, MemexOS, Node.js 22+, qmd (local search), a model command line you are logged in to,"
info "and the search models (about 1 GB, during setup)."
if [ "$WITH_SET" = 0 ] && interactive; then
  info "Optional parts:"
  info "  1  second-cli   the other model command line: writing and reviewing by different model families (recommended)"
  info "  2  video        videos and podcasts, also without subtitles: yt-dlp, ffmpeg, whisper.cpp, a speech model (~550 MB)"
  info "  3  pdf          PDFs: poppler"
  info "  4  social       what you save on X, YouTube, Bilibili: twitter-cli, yt-dlp, a browser for the one-time logins"
  info "  5  deep-search  query expansion for memex search --mode deep (~1.3 GB)"
  printf '  Which ones? Numbers or names, comma-separated; "all"; "none". [1] ' >"$TTY"
  read -r reply <"$TTY" || reply=""
  [ -z "$reply" ] && reply=1
  [ "$reply" = all ] && reply="1,2,3,4,5"
  [ "$reply" = none ] && reply=""
  WITH=""
  for w in $(echo "$reply" | tr ',' ' '); do
    case "$w" in 1) w="second-cli" ;; 2) w="video" ;; 3) w="pdf" ;; 4) w="social" ;; 5) w="deep-search" ;; esac
    case " $OPTIONAL " in *" $w "*) WITH="$WITH,$w" ;; *) note "ignored: $w" ;; esac
  done
  WITH="${WITH#,}"
fi
if [ -n "$WITH" ]; then ok "optional: $(echo "$WITH" | tr ',' ' ')"; else info "no optional parts"; fi
if [ "$MINIMAL" = 1 ]; then note "--minimal: programs only (no model command line, no logins, no model downloads)"; fi

# ---------------------------------------------------------------- 1. basics
step "1. Basics"
if ! has git; then
  if [ "$PLATFORM" = darwin ]; then die "git is missing: run xcode-select --install, then run this again"; fi
  sys_install git || die "git is required"
fi
ok "git"
has curl || die "curl is missing. Install it with your package manager and run this again."
ok "curl"

# ---------------------------------------------------------------- 2. uv and MemexOS
step "2. MemexOS"
if ! has uv; then
  info "uv installs MemexOS and the Python it needs, without touching your system Python (https://docs.astral.sh/uv)."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  hash -r 2>/dev/null || true
  has uv || die "uv was installed but is not on PATH; open a new terminal and run this again."
fi
ok "uv $(uv --version 2>/dev/null </dev/null | cut -d' ' -f2)"

SRC="${MEMEX_SOURCE:-}"
if [ -z "$SRC" ]; then
  here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
  if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && grep -q '^name = "memexos"' "$here/pyproject.toml"; then
    SRC="$here"
  else
    SRC="git+$REPO"
  fi
fi
info "installing from $SRC"
if [ -d "$SRC" ]; then
  uv tool install -q --force --reinstall-package memexos --with anthropic "$SRC" </dev/null
else
  uv tool install -q --force --with anthropic "$SRC" </dev/null
fi
has memex || die "memex was installed but is not on PATH ($BIN)."
ok "$(memex --version 2>/dev/null </dev/null || echo memex)"

# ---------------------------------------------------------------- 3. Node.js and qmd
step "3. Search: Node.js and qmd"
info "qmd is the local search engine (vector search and reranking, all on this machine). It needs Node.js 22 or newer."
NODE_MAJOR="$(node_major)"
if [ "${NODE_MAJOR:-0}" -lt 22 ]; then
  if [ "${NODE_MAJOR:-0}" -gt 0 ]; then note "Node.js $NODE_MAJOR is too old; installing a current one next to it"; fi
  if [ -n "$BREW" ]; then
    brew install node </dev/null || brew upgrade node </dev/null || true
  elif [ -n "$NODE_ARCH" ]; then
    dir="$SHARE/node"
    mkdir -p "$SHARE"
    tmp="$(mktemp -d)"
    base="https://nodejs.org/dist/$NODE_LINE"
    download "$base/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
    file="$(grep " node-v[0-9.]*-$PLATFORM-$NODE_ARCH.tar.gz\$" "$tmp/SHASUMS256.txt" | awk '{print $2}' | head -1)"
    [ -n "$file" ] || die "no Node.js build for $PLATFORM-$NODE_ARCH in $base"
    download "$base/$file" "$tmp/$file"
    want_sum="$(grep " $file\$" "$tmp/SHASUMS256.txt" | awk '{print $1}')"
    if has shasum; then got_sum="$(shasum -a 256 "$tmp/$file" | cut -d' ' -f1)"; else got_sum="$(sha256sum "$tmp/$file" | cut -d' ' -f1)"; fi
    [ "$got_sum" = "$want_sum" ] || die "the Node.js download does not match its checksum"
    rm -rf "$dir.new" && mkdir -p "$dir.new"
    tar -xzf "$tmp/$file" -C "$dir.new" --strip-components 1
    rm -rf "$dir" && mv "$dir.new" "$dir"
    rm -rf "$tmp"
    for t in node npm npx; do link_bin "$dir/bin/$t" "$t"; done
    hash -r 2>/dev/null || true
  else
    die "no Node.js build for $ARCH; install Node.js 22+ from https://nodejs.org and run this again"
  fi
  NODE_MAJOR="$(node_major)"
fi
[ "${NODE_MAJOR:-0}" -ge 22 ] || die "Node.js 22+ did not install; install it from https://nodejs.org and run this again"
ok "Node.js $NODE_MAJOR"
if ! has qmd; then
  npm_global @tobilu/qmd || true
  if ! has qmd && [ "$PLATFORM" = linux ] && { ! has make || ! has g++ || ! has python3; }; then
    info "Parts of qmd are compiled on this machine when no ready-made build fits it; that needs python3, make and a C++ compiler."
    if sys_install python3 make g++; then npm_global @tobilu/qmd || true; fi
  fi
  if ! has qmd; then
    prefix="$(npm prefix -g 2>/dev/null </dev/null || echo /usr)"
    if [ -x "$prefix/bin/qmd" ]; then link_bin "$prefix/bin/qmd" qmd; fi
  fi
fi
has qmd || die "qmd did not install (npm install -g @tobilu/qmd); run this again after fixing the error above"
ok "$(qmd --version 2>/dev/null </dev/null | head -1)"

# ---------------------------------------------------------------- 4. optional tools
if want video || want pdf || want social; then
  step "4. Optional tools"
  pk=""; sys=""
  if want video || want social; then has yt-dlp || pk="$pk yt-dlp"; fi
  if want video; then has ffmpeg || pk="$pk ffmpeg"; has whisper-cli || pk="$pk whisper-cli"; fi
  if want pdf; then has pdftotext || pk="$pk pdftotext"; fi
  if [ -n "$BREW" ] && [ -n "$pk" ]; then
    bp=""
    for t in $pk; do case "$t" in whisper-cli) bp="$bp whisper-cpp" ;; pdftotext) bp="$bp poppler" ;; *) bp="$bp $t" ;; esac; done
    # shellcheck disable=SC2086
    brew install $bp </dev/null || note "some packages did not install"
  elif [ -n "$pk" ]; then
    case " $pk " in *" yt-dlp "*) uv tool install -q yt-dlp </dev/null && ok "yt-dlp" ;; esac
    case " $pk " in *" ffmpeg "*) sys="$sys ffmpeg" ;; esac
    case " $pk " in *" pdftotext "*) sys="$sys poppler-utils" ;; esac
    case " $pk " in *" whisper-cli "*) has cmake || sys="$sys cmake"; has c++ || has g++ || sys="$sys g++" ;; esac
    # shellcheck disable=SC2086
    [ -n "$sys" ] && sys_install $sys || true
    case " $pk " in *" whisper-cli "*)
      if has cmake && { has c++ || has g++ || has clang++; }; then
        info "building whisper.cpp from source (a few minutes)"
        src="$SHARE/whisper.cpp"
        if [ -d "$src/.git" ]; then git -C "$src" pull -q </dev/null; else git clone -q --depth 1 https://github.com/ggml-org/whisper.cpp "$src" </dev/null; fi
        (cd "$src" && cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null && cmake --build build -j --config Release >/dev/null) </dev/null
        link_bin "$src/build/bin/whisper-cli" whisper-cli && ok "whisper-cli"
      else
        note "whisper.cpp needs cmake and a C++ compiler; see https://github.com/ggml-org/whisper.cpp"
      fi ;;
    esac
  fi
  for t in yt-dlp ffmpeg whisper-cli pdftotext; do
    case " $pk " in *" $t "*) if has "$t"; then ok "$t"; else note "$t is still missing"; fi ;; esac
  done
  if want video && has whisper-cli; then
    if [ -n "$(ls "$SHARE/models"/ggml-*.bin 2>/dev/null)" ]; then
      ok "speech model $(basename "$(ls "$SHARE/models"/ggml-*.bin | head -1)")"
    else
      info "downloading the speech model $WHISPER_MODEL (about 550 MB)"
      mkdir -p "$SHARE/models"
      if download "$WHISPER_URL" "$SHARE/models/$WHISPER_MODEL"; then ok "speech model in $SHARE/models"
      else note "the speech model did not download; run this again later"; fi
    fi
  fi
  if want social; then
    if has twitter; then ok "twitter-cli"
    else uv tool install -q twitter-cli </dev/null && ok "twitter-cli" || note "twitter-cli did not install (uv tool install twitter-cli)"; fi
    if b="$(find_browser)"; then ok "browser for logins: $b"
    else
      info "No Chrome, Chromium, Edge or Brave found. Installing Chrome for Testing (about 150 MB) for MemexOS's logins only;"
      info "it lives in $SHARE/browsers and is not your everyday browser."
      deps=""; input=/dev/null
      if [ "$PLATFORM" = linux ]; then
        # the system libraries Chrome needs; Playwright's installer knows them for each distribution (it uses sudo)
        if [ "$(id -u)" = 0 ]; then deps="--with-deps"
        elif [ -n "$TTY" ] && ask "Install the system libraries the browser needs (uses sudo)?" y; then deps="--with-deps"; input="$TTY"; fi
      fi
      if PLAYWRIGHT_BROWSERS_PATH="$SHARE/browsers" npx -y "$PLAYWRIGHT" install $deps chromium <"$input"; then
        if b="$(find_browser)"; then ok "browser for logins: $b"; else note "the browser did not install"; fi
      else note "the browser did not install; install Chrome, Chromium, Edge or Brave and run 'memex login <site>'"; fi
    fi
  fi
fi

# ---------------------------------------------------------------- 5. a model command line, logged in
if [ "$MINIMAL" = 0 ]; then
  step "5. Models"
  info "MemexOS uses the Claude Code or Codex command line you are logged in to (a subscription works; no API key"
  info "needed). An Anthropic or OpenAI-compatible API key, or a local model server, works too."
  others=""
  [ -n "${ANTHROPIC_API_KEY:-}" ] && others="$others ANTHROPIC_API_KEY"
  [ -n "${OPENAI_API_KEY:-}" ] && others="$others OPENAI_API_KEY"
  if curl -fs --max-time 2 --noproxy '*' http://127.0.0.1:11434/api/tags >/dev/null 2>&1 </dev/null; then others="$others Ollama"; fi
  [ -n "$others" ] && ok "also found:$others"
  first=""
  if has claude; then first=claude; elif has codex; then first=codex; fi
  if [ -z "$first" ]; then
    if [ -n "$others" ] && ! ask "No Claude Code or Codex yet. Install one anyway (recommended: they do the agent work best)?" y; then
      first=""
    else
      first="$(choose "Install which model command line" claude claude codex)"
      install_cli "$first"
    fi
  fi
  [ -n "$first" ] && login_cli "$first"
  if want second-cli; then
    if [ "$first" = codex ]; then second=claude; else second=codex; fi
    install_cli "$second"
    login_cli "$second"
  fi
  has claude || has codex || [ -n "$others" ] || note "no model backend yet: MemexOS can search, but not write, until one is set up"
fi

# ---------------------------------------------------------------- 6. setup
if [ "$SETUP" = 1 ]; then
  step "6. memex setup"
  set -- --lang "$LANG_CHOICE"
  if want social; then
    sites="x,youtube,bilibili"
    if interactive; then
      printf '  Collect from which sites? (x, youtube, bilibili, comma-separated) [%s] ' "$sites" >"$TTY"
      read -r reply <"$TTY" || reply=""
      [ -n "$reply" ] && sites="$reply"
    fi
    set -- "$@" --plugins "$sites"
  fi
  [ "$MINIMAL" = 1 ] && set -- "$@" --no-qmd
  # setup ends with memex doctor, which fails while something required is missing (a model not logged in yet);
  # that is reported above and must not stop the remaining steps
  if [ "$YES" = 1 ] || [ -z "$TTY" ]; then memex setup --yes "$@" </dev/null || note "setup finished with items to fix (memex doctor above)"
  else memex setup "$@" <"$TTY" || note "setup finished with items to fix (memex doctor above)"; fi
  if want deep-search && [ "$MINIMAL" = 0 ]; then
    memex models pull --deep </dev/null || note "the query expansion model did not download; run 'memex models pull --deep' later"
  fi
else
  step "Done"
  info "Next: memex setup --lang $LANG_CHOICE"
fi

if [ "${PATH_ADDED:-0}" = 1 ]; then
  # shellcheck disable=SC2016  # $PATH is meant literally here
  printf '\n%s is not on your PATH yet. Open a new terminal, or run:\n  export PATH="%s:$PATH"\n' "$BIN" "$BIN"
fi
}

main "$@"
