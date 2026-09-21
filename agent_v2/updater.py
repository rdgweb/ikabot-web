"""Scoped host supervisor that applies Hub-approved agent image updates."""

import logging
import os
import signal
import time
from datetime import datetime, timezone

import docker

from core.config import settings
from core.hub_client import HubClient


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s updater %(message)s",
)
logger = logging.getLogger(__name__)
_running = True


def _stop(*_args):
    global _running
    _running = False


def _env_map(container) -> dict[str, str]:
    result = {}
    for item in container.attrs.get("Config", {}).get("Env") or []:
        key, _, value = item.partition("=")
        result[key] = value
    return result


def _validate_target(container, target_name: str) -> None:
    if container.name == os.environ.get("HOSTNAME", "") or container.id.startswith(os.environ.get("HOSTNAME", "-")):
        raise RuntimeError("O supervisor nao pode atualizar a si mesmo.")
    target_node = _env_map(container).get("AGENT_NODE_ID", "")
    if target_node != settings.agent_node_id:
        raise RuntimeError(
            f"Conteiner {target_name} pertence ao no {target_node or 'desconhecido'}, nao a {settings.agent_node_id}."
        )


def _host_config(api, attrs: dict):
    host = attrs.get("HostConfig", {})
    kwargs = {
        "restart_policy": host.get("RestartPolicy") or {"Name": "unless-stopped"},
        "network_mode": host.get("NetworkMode") or "default",
        "binds": host.get("Binds") or None,
        "port_bindings": host.get("PortBindings") or None,
        "privileged": bool(host.get("Privileged", False)),
        "read_only": bool(host.get("ReadonlyRootfs", False)),
    }
    return api.create_host_config(**kwargs)


def _replacement_environment(config: dict, target_image: str) -> list[str]:
    values = _env_map_from_list(config.get("Env") or [])
    values["AGENT_IMAGE"] = target_image
    # VERSION inside the new image is authoritative; an old override would make
    # the heartbeat incorrectly report the previous release.
    values.pop("AGENT_VERSION", None)
    return [f"{key}={value}" for key, value in values.items()]


def _env_map_from_list(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        key, _, value = item.partition("=")
        result[key] = value
    return result


def replace_container(client, target_name: str, target_image: str) -> None:
    """Replace exactly one validated container and restore it on failure."""
    old = client.containers.get(target_name)
    old.reload()
    _validate_target(old, target_name)
    attrs = old.attrs
    config = attrs.get("Config", {})
    backup_name = f"{target_name}-rollback-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    replacement = None

    logger.info("Pulling %s for %s", target_image, target_name)
    client.images.pull(target_image)
    try:
        old.stop(timeout=30)
        old.rename(backup_name)
        response = client.api.create_container(
            image=target_image,
            name=target_name,
            command=config.get("Cmd"),
            entrypoint=config.get("Entrypoint"),
            environment=_replacement_environment(config, target_image),
            labels=config.get("Labels") or {},
            user=config.get("User") or None,
            working_dir=config.get("WorkingDir") or None,
            host_config=_host_config(client.api, attrs),
        )
        replacement = client.containers.get(response["Id"])
        replacement.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            replacement.reload()
            if replacement.status == "running":
                time.sleep(3)
                replacement.reload()
                if replacement.status == "running":
                    old.remove(force=True)
                    logger.info("Container %s is running with %s", target_name, target_image)
                    return
            time.sleep(1)
        raise RuntimeError(f"Novo conteiner terminou com estado {replacement.status}.")
    except Exception:
        logger.exception("Update failed; restoring %s", target_name)
        if replacement is not None:
            try:
                replacement.remove(force=True)
            except Exception:
                logger.exception("Could not remove failed replacement")
        old.reload()
        old.rename(target_name)
        old.start()
        raise


def run() -> None:
    target_name = settings.agent_target_container.strip()
    if not settings.agent_node_id or not settings.agent_token or not target_name:
        raise SystemExit("AGENT_NODE_ID, AGENT_TOKEN e AGENT_TARGET_CONTAINER sao obrigatorios.")

    client = docker.from_env()
    target = client.containers.get(target_name)
    _validate_target(target, target_name)
    hub = HubClient()
    interval = max(10, settings.updater_poll_seconds)
    logger.info("Supervisor ativo para no=%s container=%s", settings.agent_node_id, target_name)

    while _running:
        try:
            payload = hub.poll_update(target_name)
            update = payload.get("update")
            if update:
                update_id = update["id"]
                image = update["target_image"]
                try:
                    hub.report_update(update_id, "running", f"Baixando e aplicando {image}.")
                    replace_container(client, target_name, image)
                    hub.report_update(update_id, "succeeded", f"Conteiner atualizado para {image}.")
                except Exception as exc:
                    try:
                        hub.report_update(update_id, "failed", f"Rollback executado: {exc}")
                    except Exception:
                        logger.exception("Could not report failed update")
        except Exception:
            logger.exception("Updater poll failed")

        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run()
