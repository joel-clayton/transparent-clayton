#!/usr/bin/env bash
#
# Start every process the pipeline needs:
#   - Redis        broker + state store (Homebrew-managed)
#   - Celery worker runs the ingest chain
#   - Celery beat   hourly scheduler that triggers the workflow
#   - Flower        monitoring UI at http://localhost:5555
#
# Idempotent: re-running only starts what isn't already up.
#
# There is no .env, so the Celery processes inherit their config from THIS shell.
# Run it from a shell that has the pipeline's env vars set (WIKI_URL,
# WIKI_USERNAME, WIKI_PASSWORD, ASSEMBLYAI_AUTH_KEY, ...).
set -uo pipefail
cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# Prefer the in-project venv binary (clean process name); fall back to pipenv.
if [ -x ".venv/bin/celery" ]; then
  CELERY=(.venv/bin/celery)
else
  CELERY=(pipenv run celery)
fi

# Warn about missing required config rather than starting a doomed worker.
for var in WIKI_URL WIKI_USERNAME WIKI_PASSWORD ASSEMBLYAI_AUTH_KEY; do
  if [ -z "${!var:-}" ]; then
    echo "  WARNING: $var is not set in this shell"
  fi
done

# --- Redis (idempotent; Homebrew service) ---
echo "Redis: ensuring it's running..."
brew services start redis >/dev/null 2>&1 || true
for _ in $(seq 1 10); do
  [ "$(redis-cli ping 2>/dev/null)" = "PONG" ] && break
  sleep 1
done
if [ "$(redis-cli ping 2>/dev/null)" = "PONG" ]; then
  echo "  Redis: up"
else
  echo "  Redis: FAILED to start" >&2
  exit 1
fi

# --- Celery processes ---
start_celery() {
  local name="$1"; shift
  local pattern="celery -A celery_app $name"
  if pgrep -f "$pattern" >/dev/null; then
    echo "$name: already running"
  else
    echo "$name: starting..."
    nohup "${CELERY[@]}" -A celery_app "$name" "$@" \
      >"$LOG_DIR/$name.log" 2>&1 &
    echo "  $name started (logs: $LOG_DIR/$name.log)"
  fi
}

# --pool=solo: the chain is sequential and macOS prefork forking is fork-unsafe.
start_celery worker --loglevel=INFO --pool=solo
start_celery beat --loglevel=INFO
start_celery flower --port=5555

echo "Done. Monitor at http://localhost:5555 (Flower)."
