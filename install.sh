#!/bin/sh
# MemexOS installer for macOS and Linux (on Windows, run it inside WSL).
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/memexos/main/install.sh | sh
#   sh install.sh [--yes] [--minimal] [--no-setup]
#
#   --yes        accept every default without asking
#   --minimal    MemexOS, Node.js and qmd only: no model downloads, no video or PDF tools
#   --no-setup   stop before `memex setup`
#
# It asks before each step (unless --yes), skips what is already installed, and can be run again at any time:
#   1. git and curl must already be there
#   2. uv (the Python tool manager), then MemexOS itself
#   3. Node.js 22 or newer, then qmd, the local vector search engine
#   4. qmd's models (about 2.2 GB), so the first search does not wait for them
#   5. optional: yt-dlp, ffmpeg, whisper.cpp and a speech model (videos without subtitles), poppler (PDFs)
#   6. a model backend: Claude Code, Codex, an API key or a local model server; it can install a CLI for you
#   7. `memex setup`: a few questions, then your knowledge base is created (and, if you want, the scheduled jobs)
#
# Environment: MEMEX_SOURCE (a local checkout or a pip/uv requirement to install instead of the GitHub repository).
# Everything goes into your home directory (~/.local); nothing needs sudo except optional system packages on Linux,
# and those are only run after you say yes.

set -eu

REPO="https://github.com/OWNER/memexos"
NODE_LINE="latest-v24.x"
WHISPER_MODEL="ggml-large-v3-turbo-q5_0.bin"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
BIN="$HOME/.local/bin"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/memexos"


# ---------------------------------------------------------------- helpers
if [ -t 1 ]; then B="$(printf '\033[1m')"; D="$(printf '\033[2m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; R="$(printf '\033[0m')"
else B=""; D=""; G=""; Y=""; R=""; fi
step() { printf '\n%s%s%s\n' "$B" "$*" "$R"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$R" "$*"; }
note() { printf '  %s·%s %s\n' "$Y" "$R" "$*"; }
info() { printf '  %s%s%s\n' "$D" "$*" "$R"; }
die()  { printf '\n%sStopped:%s %s\n' "$B" "$R" "$*" >&2; exit 1; }
has()  { command -v "$1" >/dev/null 2>&1; }

TTY=""
if [ -r /dev/tty ] && (exec </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi

# ask "question" default(y|n) → returns 0 for yes
ask() {
  if [ "$YES" = 1 ] || [ -z "$TTY" ]; then [ "$2" = y ]; return; fi
  if [ "$2" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  printf '  %s %s ' "$1" "$hint"
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
  if [ "$YES" = 1 ] || [ -z "$TTY" ]; then echo "$def"; return; fi
  printf '  %s (%s) [%s] ' "$q" "$*" "$def" >"$TTY"
  read -r reply <"$TTY" || reply=""
  for o in "$@"; do [ "$reply" = "$o" ] && { echo "$o"; return; }; done
  echo "$def"
}

download() {   # url file
  curl -fL --retry 3 --progress-bar -o "$2.part" "$1" && mv "$2.part" "$2"
}

sha256() {
  if has shasum; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi
}

node_major() {
  has node || { echo 0; return; }
  node --version 2>/dev/null </dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p' | head -1
}

link_bin() {   # source-file name
  mkdir -p "$BIN"
  ln -sf "$1" "$BIN/$2"
}

# ---------------------------------------------------------------- main
# Everything runs from main, called on the last line, so the shell has read the whole script before anything
# runs (with `curl … | sh` the script arrives on standard input, which a child command could otherwise consume).
main() {
YES=0
MINIMAL=0
SETUP=1
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --minimal) MINIMAL=1 ;;
    --no-setup) SETUP=0 ;;
    -h|--help) sed -n '2,23p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
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

# ---------------------------------------------------------------- 1. basics
step "1. Basics"
has git || die "git is missing. macOS: xcode-select --install   Debian/Ubuntu: sudo apt install git   Fedora: sudo dnf install git"
ok "git"
has curl || die "curl is missing. Install it with your package manager and run this again."
ok "curl"

# ---------------------------------------------------------------- 2. uv and MemexOS
step "2. MemexOS"
if ! has uv; then
  info "uv installs MemexOS and the Python it needs, without touching your system Python (https://docs.astral.sh/uv)."
  ask "Install uv?" y || die "MemexOS is installed with uv. Install it from https://docs.astral.sh/uv and run this again."
  curl -LsSf https://astral.sh/uv/install.sh | sh
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
  if [ "${NODE_MAJOR:-0}" -gt 0 ]; then note "Node.js $NODE_MAJOR is too old for qmd"; fi
  if ask "Install Node.js?" y; then
    if [ -n "$BREW" ]; then
      brew install node </dev/null || brew upgrade node </dev/null
    elif [ -n "$NODE_ARCH" ]; then
      dir="$SHARE/node"
      mkdir -p "$SHARE"
      tmp="$(mktemp -d)"
      base="https://nodejs.org/dist/$NODE_LINE"
      download "$base/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
      file="$(grep " node-v[0-9.]*-$PLATFORM-$NODE_ARCH.tar.gz\$" "$tmp/SHASUMS256.txt" | awk '{print $2}' | head -1)"
      [ -n "$file" ] || die "no Node.js build for $PLATFORM-$NODE_ARCH in $base"
      download "$base/$file" "$tmp/$file"
      want="$(grep " $file\$" "$tmp/SHASUMS256.txt" | awk '{print $1}')"
      [ "$(sha256 "$tmp/$file")" = "$want" ] || die "the Node.js download does not match its checksum"
      rm -rf "$dir.new" && mkdir -p "$dir.new"
      tar -xzf "$tmp/$file" -C "$dir.new" --strip-components 1
      rm -rf "$dir" && mv "$dir.new" "$dir"
      rm -rf "$tmp"
      for t in node npm npx; do link_bin "$dir/bin/$t" "$t"; done
      hash -r 2>/dev/null || true
    else
      die "no Node.js build for $ARCH; install Node.js 22+ from https://nodejs.org and run this again"
    fi
  fi
  NODE_MAJOR="$(node_major)"
fi
if [ "${NODE_MAJOR:-0}" -ge 22 ]; then
  ok "Node.js $NODE_MAJOR"
  if has qmd; then
    ok "$(qmd --version 2>/dev/null </dev/null | head -1)"
  elif ask "Install qmd?" y; then
    prefix="$(npm prefix -g 2>/dev/null </dev/null || echo /usr)"
    if [ -w "$prefix/lib" ] 2>/dev/null || [ -w "$prefix" ]; then
      npm install -g --no-fund --no-audit --loglevel=error @tobilu/qmd </dev/null
    else
      npm install -g --no-fund --no-audit --loglevel=error --prefix "$HOME/.local" @tobilu/qmd </dev/null
    fi
    hash -r 2>/dev/null || true
    if ! has qmd && [ -x "$prefix/bin/qmd" ]; then link_bin "$prefix/bin/qmd" qmd; fi
    if has qmd; then ok "qmd"
    else note "qmd did not install; search will use the built-in full-text index (memex doctor shows how to add qmd later)"; fi
  fi
else
  note "no Node.js 22+: search will use the built-in full-text index until you install Node.js and qmd"
fi

# ---------------------------------------------------------------- 4. qmd models
if has qmd && [ "$MINIMAL" = 0 ]; then
  step "4. Search models"
  models="${XDG_CACHE_HOME:-$HOME/.cache}/qmd/models"
  if [ -n "$(ls "$models"/*reranker* 2>/dev/null)" ] && [ -n "$(ls "$models"/*mbedding* 2>/dev/null)" ]; then
    ok "qmd's models are already downloaded"
  else
    info "qmd uses three small local models: embeddings (~0.3 GB), a reranker (~0.6 GB) and query expansion (~1.3 GB)."
    info "Without them search still works with the built-in index; qmd would download them on first use instead."
    if ask "Download them now (about 2.2 GB)?" y; then
      qmd pull --progress </dev/null || note "the download did not finish; run 'qmd pull' later, or let 'memex reindex' fetch them"
    fi
  fi
fi

# ---------------------------------------------------------------- 5. video and PDF tools
if [ "$MINIMAL" = 0 ]; then
  step "5. Video and PDF support (optional)"
  info "yt-dlp downloads subtitles and audio; ffmpeg and whisper.cpp transcribe videos without subtitles on this"
  info "machine; poppler reads PDFs. Without them MemexOS still collects web pages, RSS and your agent sessions."
  missing=""
  for t in yt-dlp ffmpeg whisper-cli pdftotext; do has "$t" || missing="$missing $t"; done
  if [ -z "$missing" ]; then
    ok "yt-dlp, ffmpeg, whisper.cpp, poppler"
  elif ask "Install the missing ones:$missing?" y; then
    if [ -n "$BREW" ]; then
      pk=""
      for t in $missing; do
        case "$t" in whisper-cli) pk="$pk whisper-cpp" ;; pdftotext) pk="$pk poppler" ;; *) pk="$pk $t" ;; esac
      done
      # shellcheck disable=SC2086
      brew install $pk </dev/null || note "some packages did not install"
    else
      case " $missing " in *" yt-dlp "*) uv tool install -q yt-dlp </dev/null && ok "yt-dlp" ;; esac
      sys=""
      case " $missing " in *" ffmpeg "*) sys="$sys ffmpeg" ;; esac
      case " $missing " in *" pdftotext "*) sys="$sys poppler-utils" ;; esac
      if [ -n "$sys" ]; then
        if has apt-get; then cmd="sudo apt-get install -y$sys"
        elif has dnf; then cmd="sudo dnf install -y$sys"
        elif has pacman; then cmd="sudo pacman -S --needed$(echo "$sys" | sed 's/poppler-utils/poppler/')"
        else cmd=""; fi
        if [ -n "$cmd" ] && ask "Run: $cmd ?" y; then sh -c "$cmd" || note "the system packages did not install"
        elif [ -z "$cmd" ]; then note "install$sys with your package manager"; fi
      fi
      case " $missing " in *" whisper-cli "*)
        if has cmake && (has c++ || has g++ || has clang++); then
          if ask "Build whisper.cpp from source (a few minutes)?" y; then
            src="$SHARE/whisper.cpp"
            if [ -d "$src/.git" ]; then git -C "$src" pull -q; else git clone -q --depth 1 https://github.com/ggml-org/whisper.cpp "$src"; fi
            (cd "$src" && cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null && cmake --build build -j --config Release >/dev/null)
            link_bin "$src/build/bin/whisper-cli" whisper-cli && ok "whisper-cli"
          fi
        else
          note "whisper.cpp: install cmake and a C++ compiler and run this again, or see https://github.com/ggml-org/whisper.cpp"
        fi ;;
      esac
    fi
  fi
  if has whisper-cli; then
    if [ -n "$(ls "$SHARE/models"/ggml-*.bin 2>/dev/null)" ]; then
      ok "speech model $(basename "$(ls "$SHARE/models"/ggml-*.bin | head -1)")"
    elif ask "Download the speech model $WHISPER_MODEL (about 550 MB)?" y; then
      mkdir -p "$SHARE/models"
      if download "$WHISPER_URL" "$SHARE/models/$WHISPER_MODEL"; then ok "speech model in $SHARE/models"
      else note "the download did not finish; run this again later"; fi
    fi
  fi
fi

# ---------------------------------------------------------------- 6. a model backend
step "6. Models"
info "MemexOS writes and reviews with models you already use: the Claude Code or Codex command line (your"
info "subscription or API key), the Anthropic API, any OpenAI-compatible API, or a local server such as Ollama."
found=""
add_found() { if [ -z "$found" ]; then found="$1"; else found="$found, $1"; fi; }
if has claude; then add_found "Claude Code"; fi
if has codex; then add_found "Codex"; fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then add_found "ANTHROPIC_API_KEY"; fi
if [ -n "${OPENAI_API_KEY:-}" ]; then add_found "OPENAI_API_KEY"; fi
if curl -fs --max-time 2 --noproxy '*' http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then add_found "Ollama (local models)"; fi
if [ -n "$found" ]; then ok "found: $found"; fi
if ! has claude || ! has codex; then
  info "Two different model families work best: one writes, the other reviews. Install a command line now?"
  def=skip
  [ -z "$found" ] && def=claude
  pick="$(choose "Install" "$def" claude codex both skip)"
  case "$pick" in
    claude|both)
      if ! has claude; then curl -fsSL https://claude.ai/install.sh | bash || note "Claude Code did not install"; fi ;;
  esac
  case "$pick" in
    codex|both)
      if ! has codex; then
        if has npm; then
          prefix="$(npm prefix -g 2>/dev/null </dev/null || echo /usr)"
          if [ -w "$prefix/lib" ] 2>/dev/null || [ -w "$prefix" ]; then npm install -g --no-fund --no-audit --loglevel=error @openai/codex </dev/null
          else npm install -g --no-fund --no-audit --loglevel=error --prefix "$HOME/.local" @openai/codex </dev/null; fi
          if ! has codex && [ -x "$prefix/bin/codex" ]; then link_bin "$prefix/bin/codex" codex; fi
        else note "Codex needs Node.js; see https://github.com/openai/codex"; fi
      fi ;;
  esac
  hash -r 2>/dev/null || true
fi
if has codex && ! codex login status >/dev/null 2>&1 </dev/null; then
  if [ -n "$TTY" ] && ask "Log in to Codex now?" y; then codex login <"$TTY" || true; else note "log in later with: codex login"; fi
fi
if has claude; then info "Claude Code: if you have not logged in yet, run 'claude' once and follow the login prompt."; fi

# ---------------------------------------------------------------- 7. setup
if [ "$SETUP" = 1 ]; then
  step "7. memex setup"
  if [ "$YES" = 1 ]; then memex setup --yes
  elif [ -n "$TTY" ]; then memex setup <"$TTY"
  else memex setup --yes; fi
else
  step "Done"
  info "Next: memex setup"
fi

if [ "${PATH_ADDED:-0}" = 1 ]; then
  # shellcheck disable=SC2016  # $PATH is meant literally here
  printf '\n%s is not on your PATH yet. Open a new terminal, or run:\n  export PATH="%s:$PATH"\n' "$BIN" "$BIN"
fi
}

main "$@"
