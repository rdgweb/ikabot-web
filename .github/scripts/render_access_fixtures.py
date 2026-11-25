"""Disposable browser fixtures against migrated in-memory data, never production."""
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "hub_v2"))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
import django
django.setup()
from django.core.management import call_command
from django.test import Client
from django.contrib.auth import get_user_model
from apps.orgs.models import Membership, Organization
from apps.access_control.models import Role

call_command("migrate", verbosity=0)
organization = Organization.objects.get(slug="principal")
root_user = get_user_model().objects.create_user("browser-root", email="rodrigo@example.com", is_staff=True, is_superuser=True)
person = get_user_model().objects.create_user("browser-person")
client = Client()
client.force_login(root_user)
pages = {}
for name, path in [("people", "/users/permissoes/"), ("roles", "/users/permissoes/?tab=roles"),
                   ("person", f"/users/permissoes/?person={person.pk}"), ("docs", "/api/docs/")]:
    response = client.get(path)
    assert response.status_code == 200
    pages[name] = response.content.decode()
response = client.post("/users/permissoes/", {"action": "grant", "user_id": person.pk,
                       "role_id": Role.objects.get(system_key="platform_legacy").pk})
assert response.status_code == 200
pages["preview"] = response.content.decode()
pages["schema"] = client.get("/api/schema/").json()
from apps.users.models import UserProfile
from PIL import Image
from io import BytesIO
import base64
photo = BytesIO()
Image.new("RGB", (256, 256), "#96774e").save(photo, "JPEG")
UserProfile.objects.update_or_create(user=root_user, defaults={"display_name": "Rodrigo de teste", "avatar": photo.getvalue(), "avatar_version": 1})
other_browser = Client()
other_browser.force_login(root_user)
other_browser.get("/conta/", HTTP_USER_AGENT="Windows Chrome/130.0", HTTP_X_REAL_IP="203.0.113.9")
for tab in ("profile", "security", "preferences"):
    response = client.get("/conta/?tab="+tab)
    assert response.status_code == 200
    pages["account_"+tab] = response.content.decode()
pages["avatar"] = base64.b64encode(photo.getvalue()).decode()
print(json.dumps(pages))
