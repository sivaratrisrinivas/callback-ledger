#!/bin/sh
# Container entrypoint (Render and docker compose). One web process plus the timeout worker.
set -e
python manage.py migrate --noinput
python manage.py seed_demo
if [ -n "$CELERY_BROKER_URL" ]; then
  # Celery worker with embedded beat: ETA timeouts plus the sweeper every 10 s.
  celery -A config worker -B --pool=threads --concurrency=2 --loglevel=info &
else
  # No broker: the sweeper alone applies timeouts.
  python manage.py sweep --loop --every 2 &
fi
exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 16 --timeout 60
