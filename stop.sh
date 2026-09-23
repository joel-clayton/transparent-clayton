#!/usr/bin/env bash
#
# Stop the pipeline's Celery processes (Flower, beat, worker) gracefully.
#
# Redis is intentionally left running: Redis db 0 is the pipeline's persistent
# state store (all detail/links/watermarks), and it's a shared Homebrew-managed
# service that auto-starts. To stop it too:  brew services stop redis
set -uo pipefail
cd "$(dirname "$0")"

# Stop the scheduler first so no new work is queued, then Flower, then the worker.
for target in beat flower worker; do
  pattern="celery -A celery_app $target"
  if pgrep -f "$pattern" >/dev/null; then
    echo "Stopping celery $target..."
    pkill -f "$pattern" || true
  else
    echo "celery $target: not running"
  fi
done

echo "Done. Redis left running (stop with: brew services stop redis)."
