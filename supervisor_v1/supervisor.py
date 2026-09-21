"""Minimal, outbound-only Docker host supervisor for Ikabot agents."""

import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import docker
import requests


HUB_URL = os.environ.get("HUB_URL", "").rstrip("/")
HOST_ID = os.environ.get("IKABOT_HOST_ID", "")
HOST_TOKEN = os.environ.get("IKABOT_HOST_TOKEN", "")
POLL_SECONDS = max(10, int(os.environ.get("SUPERVISOR_POLL_SECONDS", "30")))
VERSION = Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()
IMAGE = os.environ.get("SUPERVISOR_IMAGE", "")
ALLOWED_REPOSITORY = "blackoneal/ikabot-web-agent"
_running = True

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s supervisor %(message)s")
logger = logging.getLogger(__name__)


def stop(*_args):
    global _running
    _running = False


def env_map(container) -> dict[str, str]:
    result = {}
    for item in container.attrs.get("Config", {}).get("Env") or []:
        key, _, value = item.partition("=")
        result[key] = value
    return result


def request(method: str, path: str, **kwargs) -> dict:
    headers = {
        "X-Ikabot-Host-ID": HOST_ID,
        "X-Ikabot-Host-Token": HOST_TOKEN,
        "Content-Type": "application/json",
    }
    response = requests.request(method, f"{HUB_URL}{path}", headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else {}


def validate_image(image: str) -> None:
    prefix = f"{ALLOWED_REPOSITORY}@sha256:"
    digest = image.removeprefix(prefix)
    if not image.startswith(prefix) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise RuntimeError("Hub returned an image outside the pinned repository/digest policy.")


def find_target(client, node_id: str):
    matches = []
    own_id = os.environ.get("HOSTNAME", "")
    for container in client.containers.list(all=True):
        if container.id.startswith(own_id):
            continue
        container.reload()
        if env_map(container).get("AGENT_NODE_ID") == node_id:
            labels = container.attrs.get("Config", {}).get("Labels") or {}
            labeled_host = labels.get("com.ikabot.host-id", "")
            if labeled_host and labeled_host != HOST_ID:
                continue
            matches.append(container)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one container for node {node_id}; found {len(matches)}.")
    return matches[0]


def replacement_environment(config: dict, target_image: str) -> list[str]:
    values = {}
    for item in config.get("Env") or []:
        key, _, value = item.partition("=")
        values[key] = value
    values["AGENT_IMAGE"] = target_image
    values.pop("AGENT_VERSION", None)
    return [f"{key}={value}" for key, value in values.items()]


def host_config(api, attrs: dict):
    host = attrs.get("HostConfig", {})
    return api.create_host_config(
        restart_policy=host.get("RestartPolicy") or {"Name": "unless-stopped"},
        network_mode=host.get("NetworkMode") or "default",
        binds=host.get("Binds") or None,
        port_bindings=host.get("PortBindings") or None,
        privileged=bool(host.get("Privileged", False)),
        read_only=bool(host.get("ReadonlyRootfs", False)),
    )


def replace_agent(client, node_id: str, target_image: str) -> str:
    validate_image(target_image)
    old = find_target(client, node_id)
    old.reload()
    attrs = old.attrs
    config = attrs.get("Config", {})
    original_name = old.name
    backup_name = f"{original_name}-rollback-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    labels = dict(config.get("Labels") or {})
    labels.update({
        "com.ikabot.managed": "true",
        "com.ikabot.host-id": HOST_ID,
        "com.ikabot.node-id": node_id,
        "com.ikabot.component": "agent",
    })
    replacement = None
    client.images.pull(target_image)
    try:
        old.stop(timeout=30)
        old.rename(backup_name)
        created = client.api.create_container(
            image=target_image,
            name=original_name,
            command=config.get("Cmd"),
            entrypoint=config.get("Entrypoint"),
            environment=replacement_environment(config, target_image),
            labels=labels,
            user=config.get("User") or None,
            working_dir=config.get("WorkingDir") or None,
            host_config=host_config(client.api, attrs),
        )
        replacement = client.containers.get(created["Id"])
        replacement.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            replacement.reload()
            if replacement.status == "running":
                time.sleep(5)
                replacement.reload()
                if replacement.status == "running":
                    old.remove(force=True)
                    return original_name
            time.sleep(1)
        raise RuntimeError(f"Replacement ended in state {replacement.status}.")
    except Exception:
        if replacement is not None:
            try:
                replacement.remove(force=True)
            except Exception:
                logger.exception("Could not remove failed replacement")
        old.reload()
        old.rename(original_name)
        old.start()
        raise


def run():
    if not HUB_URL.startswith("https://") or not HOST_ID or not HOST_TOKEN:
        raise SystemExit("HTTPS HUB_URL, IKABOT_HOST_ID and IKABOT_HOST_TOKEN are required.")
    client = docker.from_env()
    machine_id = str(client.info().get("ID") or "")
    if not machine_id:
        raise SystemExit("Docker engine ID is unavailable.")
    request("POST", "/api/agent/supervisor/heartbeat/", json={
        "machine_id": machine_id, "version": VERSION, "image": IMAGE,
    })
    logger.info("Enrolled host=%s engine=%s", HOST_ID, machine_id)
    last_heartbeat = 0.0
    while _running:
        try:
            now = time.monotonic()
            if now - last_heartbeat >= 60:
                request("POST", "/api/agent/supervisor/heartbeat/", json={
                    "machine_id": machine_id, "version": VERSION, "image": IMAGE,
                })
                last_heartbeat = now
            update = request("GET", "/api/agent/supervisor/updates/next/").get("update")
            if update:
                update_id = update["id"]
                try:
                    name = replace_agent(client, update["node_id"], update["target_image"])
                    request("POST", f"/api/agent/supervisor/updates/{update_id}/status/", json={
                        "status": "succeeded", "message": f"Container {name} atualizado e validado.",
                    })
                except Exception as exc:
                    logger.exception("Update failed with rollback")
                    request("POST", f"/api/agent/supervisor/updates/{update_id}/status/", json={
                        "status": "failed", "message": f"Rollback executado: {exc}",
                    })
        except Exception:
            logger.exception("Supervisor cycle failed")
        for _ in range(POLL_SECONDS):
            if not _running:
                break
            time.sleep(1)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run()
