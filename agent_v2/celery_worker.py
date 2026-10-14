"""Celery worker entry point with agent registration and heartbeat."""

from __future__ import annotations

import logging
import signal
import sys

from celery.signals import worker_process_init

from celery_app import app
from core.config import settings
from core.heartbeat import HeartbeatThread
from core.hub_client import HubClient
from core.logging import setup_logging
from tasks import configure_worker_runtime

logger = logging.getLogger(__name__)

_proxy_url = ""


@worker_process_init.connect
def _configure_child_process(**kwargs):
    configure_worker_runtime(_proxy_url)


def main():
    global _proxy_url

    setup_logging()
    logger.info("ikabot-agent celery worker v%s starting...", settings.agent_version)
    logger.info("Hub: %s", settings.hub_url)
    logger.info("Node: %s", settings.agent_node_id)

    hub = HubClient()

    try:
        result = hub.register()
        logger.info("Registered as node: %s", result.get("node_name", "unknown"))
    except Exception as exc:
        logger.error("Failed to register with hub: %s", exc)
        sys.exit(1)

    config = {}
    try:
        config = hub.get_config()
        _proxy_url = config.get("proxy", "")
    except Exception as exc:
        logger.warning("Failed to fetch config for proxy: %s", exc)
        _proxy_url = ""

    # N-106 3b-2: an agent over HTTPS never touches the broker (the env decides, else the hub)
    transport = (settings.agent_transport or config.get("transport") or "redis").strip().lower()
    if transport == "https":
        _run_over_https(hub)
        return

    configure_worker_runtime(_proxy_url)

    heartbeat = HeartbeatThread(hub, proxy_url=_proxy_url)
    heartbeat.start()

    # N-72: answers the hub's manual-control requests with the same sessions the jobs use
    # (the worker pool is threads, so this process holds them).
    import tasks
    from services.manual_control import start_manual_control

    start_manual_control(sessions=tasks._sessions, hub=hub, proxy_url=lambda: heartbeat.proxy_url or _proxy_url)

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        heartbeat.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    argv = [
        "worker",
        "--loglevel",
        settings.log_level.lower(),
        "--pool=threads",
        f"--concurrency={settings.max_parallel}",
        "-Q",
        settings.agent_node_id,
        "-n",
        f"{settings.agent_name}@%h",
    ]
    app.worker_main(argv)


def _run_over_https(hub) -> None:
    import threading

    import https_worker
    import tasks
    from services.manual_control import start_manual_control

    logger.info("Transport: HTTPS only (no broker)")
    configure_worker_runtime(_proxy_url)
    heartbeat = HeartbeatThread(hub, proxy_url=_proxy_url)
    heartbeat.start()
    current_proxy = lambda: heartbeat.proxy_url or _proxy_url  # noqa: E731
    channel = https_worker.HttpsManualChannel(HubClient())     # its own connection to the hub
    start_manual_control(sessions=tasks._sessions, hub=hub, proxy_url=current_proxy, channel=channel)

    stop = threading.Event()

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        heartbeat.stop()
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    https_worker.run(hub=HubClient(), sessions=tasks._sessions, proxy_url=current_proxy,
                     max_parallel=settings.max_parallel, channel=channel, stop=stop)


if __name__ == "__main__":
    main()
