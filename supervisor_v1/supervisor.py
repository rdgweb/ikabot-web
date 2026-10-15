"""Minimal, outbound-only Docker host supervisor for Ikabot agents."""

import logging
import os
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

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


def hub_url_allowed(url: str) -> bool:
    """HTTPS always; plain HTTP only to a service of the same compose (a name without dots, like "hub").

    That traffic never leaves the machine; anything that crosses a network must be HTTPS.
    """
    parts = urlsplit(url or "")
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and bool(parts.hostname) and "." not in parts.hostname and parts.hostname != "localhost"


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


# --- Platform agents (N-131): one container per customer's GameForge account, created and removed here.
PLATFORM_LABEL = "com.ikabot.platform-agent"
NAME_PATTERN = re.compile(r"^ikahub-plat-[a-z0-9]{8,32}$")
ALLOWED_ENV = {"HUB_URL", "AGENT_NODE_ID", "AGENT_TOKEN", "AGENT_TRANSPORT", "AGENT_NAME", "AGENT_REQUIRE_PROXY"}
MAX_MEMORY = 512 * 1024 * 1024
MAX_NANO_CPUS = 1_000_000_000


def platform_containers(client, node_id: str):
    """Only containers this supervisor created as platform agents for that node — never anything else."""
    found = []
    for container in client.containers.list(all=True, filters={"label": [f"{PLATFORM_LABEL}=true", f"com.ikabot.node-id={node_id}"]}):
        labels = container.attrs.get("Config", {}).get("Labels") or {}
        if labels.get(PLATFORM_LABEL) == "true" and labels.get("com.ikabot.host-id") == HOST_ID and labels.get("com.ikabot.node-id") == node_id:
            found.append(container)
    return found


def validate_platform_request(job: dict) -> None:
    validate_image(job.get("image", ""))
    if not NAME_PATTERN.match(job.get("container_name", "")):
        raise RuntimeError("Invalid container name for a platform agent.")
    environment = job.get("environment") or {}
    if set(environment) - ALLOWED_ENV:
        raise RuntimeError("Unexpected environment for a platform agent.")
    if urlsplit(environment.get("HUB_URL", "")).scheme != "https":
        raise RuntimeError("A platform agent talks to the hub over HTTPS only.")
    if environment.get("AGENT_REQUIRE_PROXY") != "1" or environment.get("AGENT_NODE_ID") != job.get("node_id"):
        raise RuntimeError("A platform agent must require a proxy and belong to its node.")


def create_platform_agent(client, job: dict) -> str:
    validate_platform_request(job)
    node_id = job["node_id"]
    existing = platform_containers(client, node_id)
    if existing:
        container = existing[0]
        container.reload()
        if container.status != "running":
            container.start()
        return container.name
    client.images.pull(job["image"])
    memory = min(int(job.get("memory_bytes") or 268_435_456), MAX_MEMORY)
    nano_cpus = min(int(job.get("nano_cpus") or 500_000_000), MAX_NANO_CPUS)
    created = client.api.create_container(
        image=job["image"],
        name=job["container_name"],
        environment=[f"{key}={value}" for key, value in (job.get("environment") or {}).items()],
        labels={
            "com.ikabot.managed": "true", PLATFORM_LABEL: "true", "com.ikabot.host-id": HOST_ID,
            "com.ikabot.node-id": node_id, "com.ikabot.component": "agent",
        },
        host_config=client.api.create_host_config(
            restart_policy={"Name": "unless-stopped"},
            network_mode="bridge",                         # outside the internal network: the hub only by HTTPS
            mem_limit=memory, nano_cpus=nano_cpus, pids_limit=256,
            security_opt=["no-new-privileges:true"], cap_drop=["ALL"],
            log_config={"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        ),
    )
    container = client.containers.get(created["Id"])
    container.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        container.reload()
        if container.status == "running":
            time.sleep(5)
            container.reload()
            if container.status == "running":
                return container.name
        time.sleep(1)
    status = container.status
    container.remove(force=True)
    raise RuntimeError(f"Platform agent ended in state {status}.")


def remove_platform_agent(client, node_id: str) -> int:
    removed = 0
    for container in platform_containers(client, node_id):
        container.remove(force=True)
        removed += 1
    return removed


def run_provision(client) -> None:
    job = request("GET", "/api/agent/supervisor/provision/next/").get("provision")
    if not job:
        return
    try:
        if job.get("action") == "create":
            name = create_platform_agent(client, job)
            message = f"Agente {name} rodando."
        elif job.get("action") == "remove":
            message = f"{remove_platform_agent(client, job['node_id'])} container(s) removido(s)."
        else:
            raise RuntimeError("Unknown provisioning action.")
        request("POST", f"/api/agent/supervisor/provision/{job['id']}/status/", json={"status": "succeeded", "message": message})
    except Exception as exc:
        logger.exception("Provisioning failed")
        request("POST", f"/api/agent/supervisor/provision/{job['id']}/status/", json={"status": "failed", "message": str(exc)[:500]})


def run():
    if not hub_url_allowed(HUB_URL) or not HOST_ID or not HOST_TOKEN:
        raise SystemExit("HUB_URL (HTTPS, or http://<service> inside the same compose), IKABOT_HOST_ID and IKABOT_HOST_TOKEN are required.")
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
            run_provision(client)
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
