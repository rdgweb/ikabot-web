#!/bin/bash
set -e

echo "=== ikabot hub_v2 entrypoint ==="

# Wait for DB to be ready (extra safety beyond healthcheck)
echo "Waiting for database..."
for i in $(seq 1 30); do
    python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/dev/null && break
    echo "  DB not ready, retrying ($i/30)..."
    sleep 2
done

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

# Create superuser if it doesn't exist
echo "Checking superuser..."
python manage.py shell -c "
from django.contrib.auth.models import User
if not User.objects.filter(is_superuser=True).exists():
    User.objects.create_superuser('admin', 'admin@ikabot.local', 'admin')
    print('  Superuser created: admin / admin')
else:
    print('  Superuser already exists')
"

echo "=== Starting server ==="
exec "$@"
