import redis
from celery import Celery

from src.settings import (
    REDIS_BACKEND_URL,
    REDIS_BROKER_URL,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_STATE_DB,
)

# Define the app instance
app = Celery(
    "transparent_clayton",
    broker=REDIS_BROKER_URL,
    backend=REDIS_BACKEND_URL,
    include=["src.tasks"],  # Modules to import when workers start
)

# Optional: Configure more settings
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Los_Angeles",
    enable_utc=True,
)

# Data client for pipeline state. Its DB is overridable (REDIS_DB) so a dry run
# can point at an isolated database without touching production keys; host/port
# come from the same settings surface as the broker/backend.
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_STATE_DB)

if __name__ == "__main__":
    app.start()
