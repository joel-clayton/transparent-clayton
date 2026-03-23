# start Redis as backend for Celery
if [ "$(redis-cli ping)" = "PONG" ]; then
  echo "Redis is running"
else
  echo "Redis is not running"
fi

# Start Celery
if [[ "$(celery -A celery_app inspect ping)" =~ $"pong" ]]; then
  echo "Celery is running"
else
  nohup celery -A celery_app worker --loglevel=INFO &
  # Start beat for periodic tasks
  nohup celery -A celery_app beat &
  # Start Flower to monitor Celery
  nohup celery -A celery_app flower --port=5555 &
fi
