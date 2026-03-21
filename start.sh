# start Redis as backend for Celery
if [ "$(redis-cli ping)" = "PONG" ]; then
  echo "Redis is running"
else
  echo "Redis is not running"
fi

# Start Celery
if [[ "$(celery -A celery_app inspect ping)" =~ $"pong" ]]; then
  echo "Redis is running"
else
  nohup celery -A celery_app worker --loglevel=INFO &
fi

# Start Flower to monitor Celery
nohup celery -A celery_app flower --port=5555 &
