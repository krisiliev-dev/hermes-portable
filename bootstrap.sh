#!/usr/bin/env bash
# ============================================================================
# hermes-portable bootstrap — clone anywhere, run: ./bootstrap.sh
#
# 1. Ensure uv (Python package manager) is installed
# 2. Install clean upstream Hermes (hermes-agent from PyPI) into an isolated tool env
# 3. Overlay the free-first config (config/config.template.yaml)
# 4. Interactively collect provider API keys (skippable)
# 5. Health-check the chain (drop dead models, live-first order)
# 6. Print how to launch
#
# Re-running is safe & idempotent. Flags:
#   --no-keys     skip interactive key prompts
#   --no-health   skip the connectivity health check
#   --version X   pin hermes-agent==X (default: latest)
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PIN=""
DO_KEYS=1
DO_HEALTH=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-keys)   DO_KEYS=0 ;;
    --no-health) DO_HEALTH=0 ;;
    --version)   PIN="==$2"; shift ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; RST=$'\033[0m'
say() { printf "%s\n" "${C}▸ $*${RST}"; }

say "hermes-portable bootstrap"
mkdir -p "$HERMES_HOME"

# --- 1. uv -----------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  say "installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck disable=SC1090
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { echo "uv not on PATH; add ~/.local/bin to PATH and re-run"; exit 1; }
say "uv $(uv --version | awk '{print $2}')"

# --- 2. install clean upstream Hermes --------------------------------------
if command -v hermes >/dev/null 2>&1; then
  say "hermes already installed: $(hermes --version 2>/dev/null | head -1)"
else
  say "installing hermes-agent${PIN:-} (clean upstream, PyPI)…"
  # uv tool install gives an isolated, PATH-exposed `hermes` command.
  uv tool install "hermes-agent${PIN}" --python 3.11 \
    || uv tool install "hermes-agent${PIN}"
  export PATH="$HOME/.local/bin:$PATH"
fi
HERMES_BIN="$(command -v hermes || echo "$HOME/.local/bin/hermes")"

# Python that has PyYAML for our helper scripts: prefer uv's tool env, else uv run.
PYRUN=(uv run --with pyyaml python)

# --- 3. overlay config ------------------------------------------------------
say "applying free-first config overlay…"
"${PYRUN[@]}" "$REPO/scripts/apply_config.py"

# --- 4. keys ---------------------------------------------------------------
if [ "$DO_KEYS" -eq 1 ]; then
  say "provider keys…"
  "${PYRUN[@]}" "$REPO/scripts/setup_keys.py" || true
else
  say "skipping key setup (--no-keys)"
fi

# --- 5. health -------------------------------------------------------------
if [ "$DO_HEALTH" -eq 1 ]; then
  say "health-checking the chain…"
  "${PYRUN[@]}" "$REPO/scripts/health_check.py" || true
else
  say "skipping health check (--no-health)"
fi

# --- 6. done ---------------------------------------------------------------
cat <<EOF

${G}✓ hermes-portable ready.${RST}

  Launch:        hermes            (or ${REPO}/bin/hermes)
  Re-run setup:  ${REPO}/bin/hermes --setup
  Health check:  ${REPO}/bin/hermes --health
  Add keys:      ${REPO}/bin/hermes --keys
  Validate:      hermes doctor

Config: ${HERMES_HOME}/config.yaml   Keys: ${HERMES_HOME}/.env
EOF
