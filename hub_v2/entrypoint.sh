#!/bin/bash
set -e

echo "=== ikabot hub_v2 entrypoint ==="

ADMIN_CREATE="${ADMIN_CREATE:-true}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@ikabot.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"

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

if [ "$ADMIN_CREATE" = "true" ] || [ "$ADMIN_CREATE" = "1" ] || [ "$ADMIN_CREATE" = "yes" ]; then
    # Create or update the initial admin from environment variables.
    echo "Checking superuser..."
    python manage.py shell -c "
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ.get('ADMIN_USERNAME', 'admin')
email = os.environ.get('ADMIN_EMAIL', 'admin@ikabot.local')
password = os.environ.get('ADMIN_PASSWORD', 'admin')

user = User.objects.filter(username=username).first()
if user is None:
    User.objects.create_superuser(username, email, password)
    print(f'  Superuser created: {username}')
else:
    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if not user.is_staff or not user.is_superuser:
        user.is_staff = True
        user.is_superuser = True
        changed = True
    if password:
        user.set_password(password)
        changed = True
    if changed:
        user.save()
        print(f'  Superuser updated: {username}')
    else:
        print(f'  Superuser already exists: {username}')
"
else
    echo "Skipping admin bootstrap (ADMIN_CREATE=false)"
fi

echo "=== Starting server ==="
exec "$@"
