import os

import redis
from celery import Celery

# Define the app instance
app = Celery(
    "transparent_clayton",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
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
# can point at an isolated database without touching production keys.
r = redis.Redis(host="localhost", port=6379, db=int(os.environ.get("REDIS_DB", "0")))

if __name__ == "__main__":
    app.start()
