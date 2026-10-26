#!/usr/bin/env bash
# One-time setup on the GB10 (Ubuntu, ARM64). Safe to run again.
set -euo pipefail
cd "$(dirname "$0")"
say() { printf '\n\033[1;31m==\033[0m \033[1m%s\033[0m\n' "$1"; }

say "1/5  Checking Python"
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is needed. Install it with:  sudo apt install -y python3 python3-venv python3-pip"; exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "The venv module is missing. Run:  sudo apt install -y python3-venv   and then run ./setup.sh again"; exit 1
fi
"$PY" --version

say "2/5  Creating the private Python environment (.venv) and installing libraries"
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel >/dev/null || echo "(could not upgrade pip - continuing with the current one)"
if ! python -m pip install -r requirements.txt; then
  echo
  echo "Some required libraries could not be downloaded. Checking the connection to the Python package index..."
  python -m app.cli netcheck || true
  echo "  pip configuration (an index-url here means pip uses a private mirror):"
  python -m pip config list || true
  cat <<'HELP'

What to do:
  - If pypi.org is NOT reachable and the bank uses a proxy:
        export HTTPS_PROXY=http://<proxy-host>:<port>   (same for HTTP_PROXY), then run ./setup.sh again
  - If pip shows an index-url (a private mirror) that lacks a package, ask for it to be added,
    or run once with the public index:   PIP_INDEX_URL=https://pypi.org/simple ./setup.sh
  - Then run ./setup.sh again. It is safe to re-run.
HELP
  exit 1
fi
if python -m pip install "matplotlib>=3.8" >/dev/null 2>&1; then
  echo "Optional chart library (matplotlib) installed."
else
  echo "Optional chart library (matplotlib) is not available here - Word report charts will be drawn with Pillow instead. Nothing else changes."
fi

say "3/5  Creating the settings file (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${SECRET}/" .env
  echo "Created .env with a fresh secret key."
else
  echo ".env already exists - left unchanged."
fi
mkdir -p data/inbox/{PNB,SBI,BOB,CANARA,UNION}

say "4/5  Local language model (optional but recommended)"
if [ "${SKIP_MODELS:-0}" = "1" ]; then
  echo "Skipped (SKIP_MODELS=1)."
elif command -v ollama >/dev/null 2>&1; then
  MODEL=$(grep -E '^OLLAMA_MODEL=' .env | cut -d= -f2); MODEL=${MODEL:-qwen2.5:14b}
  EMB=$(grep -E '^EMBED_MODEL=' .env | cut -d= -f2); EMB=${EMB:-nomic-embed-text}
  echo "Downloading $MODEL and $EMB with Ollama (several GB the first time)..."
  ollama pull "$MODEL" && ollama pull "$EMB" || echo "Model download failed - the app still works in template mode."
else
  echo "Ollama is not installed. The app will work in template mode (no AI wording)."
  echo "To add it later:  curl -fsSL https://ollama.com/install.sh | sh   then run ./setup.sh again"
fi

say "5/5  Self-test (uses a temporary folder, your data is not touched)"
python -m app.cli selftest

cat <<MSG

All set. Start the app with:   ./run.sh
Then open  http://$(hostname -I 2>/dev/null | awk '{print $1}'):$(grep -E '^PORT=' .env | cut -d= -f2)  in a browser.
MSG
