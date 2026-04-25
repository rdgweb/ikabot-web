"""
Service: sync game accounts from discover_characters results.

Called when a Job with action_code=101 (discover_characters) finishes
successfully. Creates or updates GameAccount records under the parent
Account.
"""

import logging
from typing import Any

from apps.accounts.models import Account, GameAccount

logger = logging.getLogger(__name__)


def sync_game_accounts(account_id: str, characters_data: list[dict[str, Any]]) -> int:
    """
    Create/update GameAccount records from discover_characters output.

    Parameters
    ----------
    account_id : str
        UUID of the parent (lobby) Account.
    characters_data : list[dict]
        List of character dicts as returned by the agent, each containing:
          - lobby_account_id (int)
          - server_id (str, e.g. "s61-br")
          - server_language (str, e.g. "br")
          - server_number (int, e.g. 61)
          - name (str, player name)
          - account_group (str, e.g. "br_61")
          - blocked (bool)

    Returns
    -------
    int
        Number of GameAccount records created or updated.
    """
    try:
        account = Account.objects.get(pk=account_id)
    except Account.DoesNotExist:
        logger.error("sync_game_accounts: Account %s not found", account_id)
        return 0

    if not characters_data:
        logger.warning(
            "sync_game_accounts: empty characters_data for account %s",
            account_id,
        )
        return 0

    count = 0
    seen_keys = set()

    for char in characters_data:
        lobby_account_id = char.get("lobby_account_id")
        server_id = char.get("server_id", "")

        if not lobby_account_id or not server_id:
            logger.warning(
                "sync_game_accounts: skipping character with missing "
                "lobby_account_id or server_id: %s",
                char,
            )
            continue

        seen_keys.add((lobby_account_id, server_id))

        defaults = {
            "server_language": char.get("server_language", ""),
            "server_number": char.get("server_number", 0),
            "name": char.get("name", ""),
            "account_group": char.get("account_group", ""),
            "blocked": char.get("blocked", False),
        }

        _, created = GameAccount.objects.update_or_create(
            account=account,
            lobby_account_id=lobby_account_id,
            server_id=server_id,
            defaults=defaults,
        )

        action = "created" if created else "updated"
        logger.info(
            "GameAccount %s: lobby_id=%s server=%s name=%s",
            action, lobby_account_id, server_id, char.get("name", ""),
        )
        count += 1

    # Mark game accounts not in the latest discovery as inactive
    if seen_keys:
        existing = GameAccount.objects.filter(account=account)
        for ga in existing:
            key = (ga.lobby_account_id, ga.server_id)
            if key not in seen_keys:
                ga.active = False
                ga.save(update_fields=["active", "updated_at"])
                logger.info(
                    "GameAccount deactivated (no longer in lobby): "
                    "lobby_id=%s server=%s",
                    ga.lobby_account_id, ga.server_id,
                )

    logger.info(
        "sync_game_accounts: processed %d characters for account %s",
        count, account_id,
    )
    return count


def create_discover_job(account: Account) -> "Job | None":
    """
    Create a discover_characters job (action_code=101) for a lobby Account.

    The job will be auto-enqueued by the jobs signal.

    Returns the created Job or None if the account has no node.
    """
    from apps.jobs.services.workflows import create_job_with_workflow

    if not account.node_id:
        logger.warning(
            "create_discover_job: account %s has no node, skipping",
            account.pk,
        )
        return None

    job = create_job_with_workflow(
        account=account,
        node=account.node,
        action_code=101,
        inputs={},
        status="queued",
    )
    logger.info(
        "discover_characters job %s created for account %s",
        job.pk, account.pk,
    )
    return job
