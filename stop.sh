pkill -9 -f 'celery_app worker'

redis-cli shutdown
