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
    timezone="UTC",
    enable_utc=True,
)

r = redis.Redis(host="localhost", port=6379, db=0)

if __name__ == "__main__":
    app.start()
