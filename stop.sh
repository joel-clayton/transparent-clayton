pkill -9 -f 'celery_app flower'
pkill -9 -f 'celery_app beat'
pkill -9 -f 'celery_app worker'
redis-cli shutdown
