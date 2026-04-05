"""
Signals for the accounts app.

1. Auto-trigger discover_characters when an Account is created or its
   gf_token_enc is updated.
2. Sync GameAccount records when a discover_characters job (action_code=101)
   finishes successfully.
"""

import json
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="accounts.Account")
def auto_discover_on_account_save(sender, instance, created, **kwargs):
    """
    When a lobby Account is created (or its gf_token changes),
    auto-create a discover_characters job.
    """
    from apps.accounts.services.discover_service import create_discover_job

    if created:
        # Always discover on first creation
        if instance.gf_token_enc:
            create_discover_job(instance)
            logger.info(
                "Auto-discover triggered for new account %s", instance.pk
            )
        return

    # On update, only re-discover if gf_token_enc changed.
    # We check update_fields if available; if the save didn't specify
    # update_fields we skip to avoid infinite loops.
    update_fields = kwargs.get("update_fields")
    if update_fields and "gf_token_enc" in update_fields:
        if instance.gf_token_enc:
            create_discover_job(instance)
            logger.info(
                "Auto-discover triggered for updated gf_token on account %s",
                instance.pk,
            )


@receiver(post_save, sender="jobs.Job")
def sync_game_accounts_on_discover_complete(sender, instance, created, **kwargs):
    """
    When a discover_characters job (action_code=101) finishes successfully,
    parse its result and sync GameAccount records.
    """
    if created:
        return

    # Only act on finished discover_characters jobs
    if instance.action_code != 101:
        return
    if instance.status != "finished":
        return

    # Parse the result from the job logs or inputs_json.
    # The agent stores the discover result in the last JobLog with
    # level='info' containing JSON characters data.
    # Convention: the result is stored as a JobLog with message starting
    # with "DISCOVER_RESULT:" followed by JSON.
    from apps.accounts.services.discover_service import sync_game_accounts

    result_log = (
        instance.logs.filter(
            level="info",
            message__startswith="DISCOVER_RESULT:",
        )
        .order_by("-created_at")
        .first()
    )

    if not result_log:
        logger.warning(
            "discover_characters job %s finished but no DISCOVER_RESULT log found",
            instance.pk,
        )
        return

    try:
        raw = result_log.message.removeprefix("DISCOVER_RESULT:").strip()
        characters_data = json.loads(raw)
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.error(
            "Failed to parse discover result for job %s: %s",
            instance.pk, exc,
        )
        return

    count = sync_game_accounts(
        account_id=str(instance.account_id),
        characters_data=characters_data,
    )
    logger.info(
        "Synced %d game accounts from discover job %s",
        count, instance.pk,
    )
