"""
Telegram webhook endpoint.

Receives updates from Telegram, processes:
  - /start <6-digit-code>                     — account linking
  - /replyto <db_uuid> <text>                 — reply to regular diplomacy message
  - /replyto <db_uuid> yes [text]             — accept treaty/action (optional extra text)
  - /replyto <db_uuid> no [text]              — decline treaty/action (optional extra text)
"""

import json
import logging
import re

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.telegram.models import (
    TelegramBotConfig,
    TelegramAccountConfig,
    TelegramIncomingCommand,
)
from apps.telegram.services.bot_api import answer_callback_query, edit_message_text, send_message
from apps.telegram.services.linking import validate_link_code
from apps.market.models import ConstructionMarketIntervention
from apps.market.services import (
    approve_construction_market_intervention,
    refresh_intervention_message,
    reject_construction_market_intervention,
)

logger = logging.getLogger(__name__)


def _create_raid_job(
    target_city_id: str,
    island_id: str,
    ga_id: str,
    source_city_id: str = "",
) -> tuple[bool, str]:
    """Create ac=1008 (RaidCityRunner) job from Telegram callback.

    Lê reports mission 7 mais recente do alvo pra detectar warships defensoras
    (needs_blockade) e calcular units recomendadas.
    Returns (success, message).
    """
    from apps.accounts.models import GameAccount
    from apps.espionage.models import SpyReport
    from apps.jobs.services.workflows import create_job_with_workflow

    try:
        ga = GameAccount.objects.select_related("account").get(pk=ga_id)
    except GameAccount.DoesNotExist:
        return False, f"Conta {ga_id[:8]} não encontrada."

    node = ga.account.node
    if not node:
        return False, "Nenhum nó disponível para esta conta."

    # PT-BR → unit_id (terrestres) mapping
    _NAME_TO_ID = {
        "Fundeiro": 301, "Espadachim": 302, "Hoplita": 303, "Carabineiro": 304,
        "Morteiro": 305, "Catapulta": 306, "Aríete": 307, "Ariete": 307,
        "Gigante a Vapor": 308, "Balão-Bombardeiro": 309, "Balao-Bombardeiro": 309,
        "Cozinheiro": 310, "Médico": 311, "Medico": 311, "Girocóptero": 312,
        "Girocoptero": 312, "Arqueiro": 313, "Lanceiro": 315,
    }

    # Detectar warships na defesa via mission 7 + mapear tropas terrestres pra IDs
    needs_blockade = False
    enemy_units: dict[int, int] = {}  # {unit_id: qty} — só terrestres
    enemy_wall_level = 1
    try:
        r7 = SpyReport.objects.filter(
            target_city_id=target_city_id, mission_id=6,
            game_account__server_id=ga.server_id,
        ).order_by("-created_at").first()
        if r7 and r7.data_json:
            for cat in (r7.data_json.get("troops_data") or []):
                if not isinstance(cat, dict):
                    continue
                cat_type = (cat.get("category_type") or "").lower()
                cat_label = (cat.get("category") or "").lower()
                is_naval = "naval" in cat_type or "naval" in cat_label
                for u in (cat.get("units") or []):
                    name = str(u.get("name") or "").strip()
                    cnt = int(u.get("count") or 0)
                    if cnt <= 0:
                        continue
                    if is_naval:
                        needs_blockade = True
                    else:
                        uid = _NAME_TO_ID.get(name)
                        if uid:
                            enemy_units[uid] = enemy_units.get(uid, 0) + cnt
    except Exception as exc:
        logger.warning("Failed to read defender intel for %s: %s", target_city_id, exc)

    inputs: dict = {
        "target_city_id": target_city_id,
        "island_id": island_id,
        "mode": "land",
        "multi_trip": True,
        "max_trips": 50,
        "min_resources_to_continue": 5000,
        "needs_blockade": needs_blockade,
        "wall_level": enemy_wall_level,
    }
    if enemy_units:
        inputs["enemy_units"] = enemy_units
    if source_city_id:
        inputs["source_city_id"] = source_city_id

    # Pré-calcular força recomendada (mesmo simulador do alerta) e passar pro runner.
    # Evita que o runner recalcule com fórmula diferente e mande tropas erradas.
    if enemy_units and source_city_id:
        try:
            from apps.espionage.api.views import _parse_enemy_intel, _choose_raid_account
            enemy_intel = _parse_enemy_intel(target_city_id, "", ga.server_id)
            # Reusa _choose_raid_account com filtro = só GA escolhida → traz recommended
            # Simples: chama o recommend direto.
            from apps.espionage.services.battle_land import recommend_attack_force
            from apps.game.models import AccountSnapshot
            snap = AccountSnapshot.objects.filter(game_account=ga).first()
            available: dict[int, int] = {}
            if snap:
                military = snap.military or {}
                bc = military.get("by_city") if isinstance(military, dict) else None
                troops_raw = {}
                if isinstance(bc, list):
                    for c in bc:
                        if str(c.get("city_id")) == str(source_city_id):
                            troops_raw = c.get("troops") or {}
                            break
                for k, v in (troops_raw or {}).items():
                    try:
                        available[int(k)] = int(v)
                    except (TypeError, ValueError):
                        pass
            if available:
                # Upgrades próprios do atacante (mission 26 own, base_snapshot.unit_improvements)
                own_upgrades_raw = (snap.base_snapshot or {}).get("unit_improvements") if snap else None
                own_upgrades: dict = {}
                if isinstance(own_upgrades_raw, dict):
                    for k, v in own_upgrades_raw.items():
                        if str(k).isdigit() and isinstance(v, dict):
                            own_upgrades[int(k)] = v
                rec = recommend_attack_force(
                    available_units=available,
                    defender_units=enemy_units,
                    attacker_upgrades=own_upgrades,
                    defender_upgrades=enemy_intel.get("enemy_upgrades") or {},
                    town_hall_level=enemy_intel.get("city_level") or 1,
                    wall_level=enemy_wall_level,
                )
                rec_units = rec.get("recommended") or {}
                # Aplica reserva 25% (margem de segurança)
                rec_with_reserve = {}
                for uid, qty in rec_units.items():
                    have = available.get(uid, 0)
                    rec_with_reserve[str(uid)] = min(have, int(qty * 1.25))
                if rec_with_reserve:
                    inputs["recommended_units"] = rec_with_reserve
                inputs["city_level"] = enemy_intel.get("city_level") or 1
        except Exception as exc:
            logger.warning("Failed to precompute recommended_units: %s", exc)

    # Blockade: calcular frota mínima a partir do snapshot da source city
    if needs_blockade and source_city_id:
        try:
            from apps.game.models import AccountSnapshot
            from apps.espionage.api.views import _recommend_fleet_for_blockade
            snap = AccountSnapshot.objects.filter(game_account=ga).first()
            if snap:
                military = snap.military or {}
                bc = military.get("by_city") if isinstance(military, dict) else None
                fleet = {}
                if isinstance(bc, list):
                    for c in bc:
                        if str(c.get("city_id")) == str(source_city_id):
                            fleet = c.get("fleet") or {}
                            break
                elif isinstance(bc, dict):
                    fleet = (bc.get(str(source_city_id)) or {}).get("fleet") or {}
                rec_fleet = _recommend_fleet_for_blockade(fleet)
                if rec_fleet:
                    inputs["blockade_fleet_units"] = rec_fleet
        except Exception as exc:
            logger.warning("Failed to recommend blockade fleet: %s", exc)
    try:
        job = create_job_with_workflow(
            account=ga.account,
            game_account=ga,
            node=node,
            action_code=1008,
            inputs=inputs,
            status="queued",
            trigger_type="telegram_callback",
        )
        logger.info("RaidJob created %s ga=%s target=%s source=%s blockade=%s",
                    job.pk, ga_id, target_city_id, source_city_id, needs_blockade)
        return True, str(job.pk)
    except Exception as exc:
        logger.exception("Failed to create raid job: %s", exc)
        return False, str(exc)[:200]


def _create_diplomacy_send_job_from_uuid(
    db_uuid: str,
    yes_no: str,
    extra_text: str,
) -> tuple[bool, str]:
    """Create a Job for action 31 (DiplomacySendRunner) using a DiplomacyMessage UUID.

    Looks up receiver_id, game_account, and available actions from the DB record.

    Args:
        db_uuid:    UUID of the DiplomacyMessage.
        yes_no:     "yes", "no", or "" (empty = regular text reply).
        extra_text: Text to include in the message (optional for yes/no, required for plain reply).

    Returns:
        (success: bool, error_msg: str)
    """
    from apps.accounts.models import GameAccount
    from apps.diplomacy.models import DiplomacyMessage
    from apps.jobs.services.workflows import create_job_with_workflow

    try:
        dm = DiplomacyMessage.objects.select_related(
            "game_account__account__node"
        ).get(pk=db_uuid)
    except DiplomacyMessage.DoesNotExist:
        logger.warning("DiplomacySend: DiplomacyMessage %s not found", db_uuid)
        return False, "Mensagem não encontrada. O ID pode ter expirado."

    ga = dm.game_account
    actions = dm.actions  # list of {"msg_type": int, "receiver_id": str}

    if yes_no == "yes":
        # Accept = action with lowest msg_type (79 for treaty, 102 for friend, etc.)
        sorted_actions = sorted(actions, key=lambda a: a["msg_type"])
        accept = sorted_actions[0] if sorted_actions else None
        if not accept:
            return False, "Esta mensagem não tem ação de aceitar disponível."
        msg_type = accept["msg_type"]
        receiver_id = accept["receiver_id"]

    elif yes_no == "no":
        # Decline = action with highest msg_type (80 for treaty, 103 for friend, etc.)
        sorted_actions = sorted(actions, key=lambda a: a["msg_type"])
        decline = sorted_actions[-1] if len(sorted_actions) > 1 else None
        if not decline:
            return False, "Esta mensagem não tem ação de recusar disponível."
        msg_type = decline["msg_type"]
        receiver_id = decline["receiver_id"]

    else:
        # Regular text reply
        if not extra_text:
            return False, "Forneça um texto para responder."
        if not dm.receiver_id:
            return False, "Esta mensagem não tem receiverId para resposta."
        msg_type = 50
        receiver_id = dm.receiver_id

    inputs: dict = {
        "receiver_id": int(receiver_id),
        "msg_type": msg_type,
    }
    if extra_text:
        inputs["content"] = extra_text
    if dm.reply_to_game_id:
        inputs["reply_to"] = int(dm.reply_to_game_id)

    create_job_with_workflow(
        account=ga.account,
        game_account=ga,
        node=ga.account.node,
        action_code=31,
        inputs=inputs,
        status="queued",
    )
    logger.info(
        "Created diplomacy_send job: ga=%s receiver=%s msg_type=%s uuid=%s",
        ga.pk, receiver_id, msg_type, db_uuid,
    )
    return True, ""


class TelegramWebhookView(APIView):
    """
    POST /api/telegram/webhook/<secret>/

    Receives Telegram Update JSON. Validates the webhook secret,
    processes all supported commands, and always returns 200 OK.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No auth — Telegram sends raw POSTs

    def post(self, request, secret):
        # Validate webhook secret
        try:
            bot_config = TelegramBotConfig.objects.get(pk=1)
        except TelegramBotConfig.DoesNotExist:
            logger.warning("Webhook called but no TelegramBotConfig exists.")
            return Response({"status": "ok"})

        if not bot_config.webhook_secret or secret != bot_config.webhook_secret:
            logger.warning("Webhook called with invalid secret.")
            return Response({"status": "ok"})

        data = request.data

        # ── Callback queries (inline keyboard buttons) ────────────────────────
        if data.get("callback_query"):
            cq = data["callback_query"]
            cq_id = str(cq.get("id", ""))
            cq_data = str(cq.get("data", ""))
            cq_msg = cq.get("message", {})
            cq_chat_id = str(cq_msg.get("chat", {}).get("id", ""))
            cq_message_id = cq_msg.get("message_id")
            cq_message_text = cq_msg.get("text", "")
            cq_user_id = str(cq.get("from", {}).get("id", ""))

            if cq_data.startswith("captcha_reply:"):
                challenge_id = cq_data[14:]
                _store_pending_captcha(cq_user_id, int(challenge_id))
                answer_callback_query(cq_id)
                send_message(
                    cq_chat_id,
                    f"🔑 <b>Digite o texto do captcha #{challenge_id}:</b>",
                    reply_markup={"force_reply": True, "selective": True},
                )
                return Response({"status": "ok"})

            if cq_data.startswith("cmi_approve:") or cq_data.startswith("cmi_reject:"):
                request_id = cq_data.split(":", 1)[1].strip()
                intervention = (
                    ConstructionMarketIntervention.objects
                    .select_related("account", "game_account", "node", "source_job")
                    .filter(pk=request_id)
                    .first()
                )
                if intervention is None:
                    answer_callback_query(cq_id, "Solicitação não encontrada.", show_alert=True)
                    return Response({"status": "ok"})

                decided_by = str(cq.get("from", {}).get("username") or cq.get("from", {}).get("first_name") or cq_user_id)
                if cq_data.startswith("cmi_approve:"):
                    ok, msg = approve_construction_market_intervention(intervention, decided_by=decided_by)
                    label = "✅ Venda aprovada" if ok else "❌ Aprovação falhou"
                    if ok:
                        refresh_intervention_message(intervention, decision_label="✅ Venda aprovada")
                    answer_callback_query(cq_id, msg[:200], show_alert=not ok)
                    send_message(cq_chat_id, label if ok else f"{label}: {msg}")
                else:
                    ok, msg = reject_construction_market_intervention(intervention, decided_by=decided_by)
                    label = "❌ Venda recusada" if ok else "⚠️ Recusa falhou"
                    if ok:
                        refresh_intervention_message(intervention, decision_label="❌ Venda recusada")
                    answer_callback_query(cq_id, msg[:200], show_alert=not ok)
                    send_message(cq_chat_id, label if ok else f"{label}: {msg}")
                return Response({"status": "ok"})

            if cq_data.startswith("accept:") or cq_data.startswith("decline:") or cq_data.startswith("reply:"):
                if cq_data.startswith("accept:"):
                    dm_uuid, yes_no = cq_data[7:], "yes"
                elif cq_data.startswith("decline:"):
                    dm_uuid, yes_no = cq_data[8:], "no"
                else:
                    dm_uuid, yes_no = cq_data[6:], "reply"

                # Guard: reject if already actioned
                if _dm_already_actioned(dm_uuid):
                    answer_callback_query(cq_id, "⚠️ Esta mensagem já foi respondida anteriormente.", show_alert=True)
                    return Response({"status": "ok"})

                from django.utils import timezone as tz
                now_str = tz.localtime().strftime("%d/%m %H:%M")

                if yes_no == "reply":
                    _store_pending_reply(cq_user_id, dm_uuid)
                    answer_callback_query(cq_id, "Escreva sua resposta")
                    send_message(
                        cq_chat_id,
                        "✏️ <b>Escreva sua resposta:</b>",
                        reply_markup={"force_reply": True, "selective": True},
                    )
                else:
                    ok, err = _create_diplomacy_send_job_from_uuid(dm_uuid, yes_no, "")
                    if ok:
                        label = "✅ Aceito" if yes_no == "yes" else "❌ Recusado"
                        answer_callback_query(cq_id, label)
                        _update_dm_status(dm_uuid, "action_taken")
                        # Edit original message to replace buttons with status line
                        if cq_message_id and cq_chat_id:
                            edited_text = f"{cq_message_text}\n\n<i>{label} · {now_str}</i>"
                            edit_message_text(cq_chat_id, cq_message_id, edited_text)
                    else:
                        answer_callback_query(cq_id, err[:200])
                        send_message(cq_chat_id, f"❌ {err}")
            elif cq_data.startswith("raid_skip:"):
                # raid_skip:{target_city_id}:{report_id} OR legacy raid_skip:{target_city_id}
                payload = cq_data[len("raid_skip:"):]
                parts = payload.split(":")
                target_city_id = parts[0] if parts else ""
                report_id      = parts[1] if len(parts) > 1 else ""
                # Mark this report as ignored so future alerts only re-fire if a newer
                # report comes in for this city.
                if target_city_id and report_id:
                    try:
                        from apps.espionage.models import RaidAlertSent
                        from apps.accounts.models import GameAccount
                        # find latest RaidAlertSent for this city across all GAs and mark ignored
                        for ras in RaidAlertSent.objects.filter(target_city_id=target_city_id):
                            ras.ignored_report_id = report_id
                            ras.save(update_fields=["ignored_report_id", "updated_at"])
                    except Exception:
                        pass
                answer_callback_query(cq_id, "✅ Alerta ignorado.")
                if cq_message_id and cq_chat_id:
                    edit_message_text(cq_chat_id, cq_message_id,
                                      f"{cq_message_text}\n\n<i>❌ Ignorado</i>")
                return Response({"status": "ok"})

            elif cq_data.startswith("raid_now:"):
                # raid_now:{target_city_id}:{island_id}:{ga_id}[:{source_city_id}]
                parts = cq_data[9:].split(":")
                if len(parts) >= 3:
                    target_city_id = parts[0]
                    island_id      = parts[1]
                    # ga_id contém - (UUID), pega o segmento certo
                    # formato: parts[2:] pode incluir source_city_id no fim se 5 segmentos+
                    if len(parts) >= 4 and parts[-1].isdigit():
                        source_city_id = parts[-1]
                        ga_id_str      = ":".join(parts[2:-1])
                    else:
                        source_city_id = ""
                        ga_id_str      = ":".join(parts[2:])

                    ok, msg = _create_raid_job(target_city_id, island_id, ga_id_str, source_city_id=source_city_id)
                    if ok:
                        answer_callback_query(cq_id, "🏴‍☠️ Raid criado!")
                        if cq_message_id and cq_chat_id:
                            edit_message_text(cq_chat_id, cq_message_id,
                                              f"{cq_message_text}\n\n<i>✅ Raid enviado</i>")
                    else:
                        answer_callback_query(cq_id, f"❌ {msg[:180]}", show_alert=True)
                else:
                    answer_callback_query(cq_id, "❌ Dados inválidos.", show_alert=True)
                return Response({"status": "ok"})

            else:
                answer_callback_query(cq_id)

            return Response({"status": "ok"})

        # ── Text message ───────────────────────────────────────────────────
        message = data.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        from_user = message.get("from", {})
        username = from_user.get("username", "")

        if not text or not chat_id:
            return Response({"status": "ok"})

        text_stripped = text.strip()
        user_id = str(from_user.get("id", ""))

        # Check if user has a pending captcha waiting (after tapping 🔑 Digitar resposta)
        pending_captcha_id = _get_pending_captcha(user_id)
        if pending_captcha_id:
            _clear_pending_captcha(user_id)
            from apps.captcha.models import CaptchaChallenge
            try:
                challenge = CaptchaChallenge.objects.get(pk=int(pending_captcha_id))
                answer = text_stripped.strip().upper()
                if challenge.is_pending:
                    challenge.mark_solved(answer, "telegram")
                    send_message(chat_id, f"✅ Captcha #{pending_captcha_id} resolvido: <code>{answer}</code>")
                else:
                    send_message(chat_id, f"❌ Captcha #{pending_captcha_id} já expirou ou foi resolvido.")
            except CaptchaChallenge.DoesNotExist:
                send_message(chat_id, f"❌ Captcha #{pending_captcha_id} não encontrado.")
            return Response({"status": "ok"})

        # Check if user has a pending reply waiting (after tapping ↩️ Responder)
        pending_uuid = _get_pending_reply(user_id)
        if pending_uuid:
            _clear_pending_reply(user_id)
            ok, err = _create_diplomacy_send_job_from_uuid(pending_uuid, "", text_stripped)
            if ok:
                _update_dm_status(pending_uuid, "replied")
                from django.utils import timezone as tz
                now_str = tz.localtime().strftime("%d/%m %H:%M")
                send_message(chat_id, f"↩️ <i>Respondido · {now_str}</i>\n\n{text_stripped}")
            else:
                send_message(chat_id, f"❌ {err}")
            return Response({"status": "ok"})
        link_command = TelegramIncomingCommand.command_for(
            TelegramIncomingCommand.COMMAND_LINK
        )
        reply_command = TelegramIncomingCommand.command_for(
            TelegramIncomingCommand.COMMAND_DIPLOMACY_REPLY
        )
        code_re = (
            re.compile(rf"^{re.escape(link_command)}\s+(\d{{6}})$")
            if link_command else None
        )
        replyto_re = (
            re.compile(
                rf"^{re.escape(reply_command)}\s+([\w-]{{36}})\s+([\s\S]+)$",
                re.IGNORECASE,
            )
            if reply_command else None
        )

        # Handle /start <code>
        match = code_re.match(text_stripped) if code_re else None
        if match:
            self._handle_start(match.group(1), chat_id, username)
            return Response({"status": "ok"})

        # Handle configured reply command: <command> <db_uuid> [yes|no] [text]
        match = replyto_re.match(text_stripped) if replyto_re else None
        if match:
            self._handle_replyto(match, chat_id)
            return Response({"status": "ok"})

        # Handle /captcha <id> <answer>
        captcha_re = re.compile(r"^/captcha\s+(\d+)\s+(\S+)$", re.IGNORECASE)
        match = captcha_re.match(text_stripped)
        if match:
            self._handle_captcha(match, chat_id)
            return Response({"status": "ok"})

        return Response({"status": "ok"})

    # ── Handlers ──────────────────────────────────────────────────────────

    def _handle_start(self, code: str, chat_id: str, username: str) -> None:
        config = validate_link_code(code, chat_id, username)

        if config:
            if isinstance(config, TelegramBotConfig):
                send_message(
                    chat_id,
                    "Vinculado com sucesso!\n\n"
                    "Este chat recebera <b>todas</b> as notificacoes "
                    "do ikabot hub.",
                )
                logger.info(
                    "Telegram GLOBAL linked -> chat %s (@%s)",
                    chat_id, username,
                )
            elif isinstance(config, TelegramAccountConfig):
                ga_name = (
                    config.game_account.name
                    or config.game_account.server_id
                )
                send_message(
                    chat_id,
                    f"Vinculado com sucesso!\n\n"
                    f"Subconta: <b>{ga_name}</b>\n"
                    f"Este chat recebera notificacoes desta subconta.",
                )
                logger.info(
                    "Telegram linked: GA %s -> chat %s (@%s)",
                    config.game_account_id, chat_id, username,
                )
        else:
            send_message(
                chat_id,
                "Codigo invalido ou expirado.\n\n"
                "Gere um novo codigo no painel e tente novamente.",
            )
            logger.info(
                "Invalid/expired link code attempted: %s from chat %s",
                code, chat_id,
            )

    def _handle_captcha(self, match: re.Match, chat_id: str) -> None:
        """Handle /captcha <id> <answer> — resolve a pending CaptchaChallenge."""
        from apps.captcha.models import CaptchaChallenge

        challenge_id = int(match.group(1))
        answer = match.group(2).strip().upper()

        try:
            challenge = CaptchaChallenge.objects.get(pk=challenge_id)
        except CaptchaChallenge.DoesNotExist:
            send_message(chat_id, "❌ Captcha não encontrado.")
            return

        if challenge.is_pending:
            challenge.mark_solved(answer, "telegram")
            send_message(chat_id, f"✅ Captcha #{challenge_id} resolvido!")
        else:
            send_message(chat_id, "❌ Captcha expirado ou já resolvido.")

    def _handle_replyto(self, match: re.Match, chat_id: str) -> None:
        """Handle /replyto <db_uuid> [yes|no] [text]."""
        db_uuid = match.group(1)
        rest = match.group(2).strip()

        # Parse yes/no keyword
        yes_no = ""
        extra_text = rest
        lower = rest.lower()
        if lower.startswith("yes") and (len(lower) == 3 or lower[3] in (" ", "\n")):
            yes_no = "yes"
            extra_text = rest[3:].strip()
        elif lower.startswith("no") and (len(lower) == 2 or lower[2] in (" ", "\n")):
            yes_no = "no"
            extra_text = rest[2:].strip()

        ok, error = _create_diplomacy_send_job_from_uuid(db_uuid, yes_no, extra_text)
        if ok:
            if yes_no == "yes":
                send_message(chat_id, "✅ Aceitar — job criado na fila.")
                _update_dm_status(db_uuid, "action_taken")
            elif yes_no == "no":
                send_message(chat_id, "❌ Recusar — job criado na fila.")
                _update_dm_status(db_uuid, "action_taken")
            else:
                send_message(chat_id, "↩️ Resposta enviada para o agente.")
                _update_dm_status(db_uuid, "replied")
        else:
            send_message(chat_id, f"❌ {error}")


# ── Module-level helpers ───────────────────────────────────────────────────────

_PENDING_REPLY_TTL = 300  # 5 minutes
_PENDING_CAPTCHA_TTL = 300


def _store_pending_captcha(user_id: str, challenge_id: int) -> None:
    from django.core.cache import cache
    cache.set(f"tg_pending_captcha:{user_id}", str(challenge_id), timeout=_PENDING_CAPTCHA_TTL)


def _get_pending_captcha(user_id: str) -> str | None:
    from django.core.cache import cache
    return cache.get(f"tg_pending_captcha:{user_id}")


def _clear_pending_captcha(user_id: str) -> None:
    from django.core.cache import cache
    cache.delete(f"tg_pending_captcha:{user_id}")


def _store_pending_reply(user_id: str, dm_uuid: str) -> None:
    from django.core.cache import cache
    cache.set(f"tg_pending_reply:{user_id}", dm_uuid, timeout=_PENDING_REPLY_TTL)


def _get_pending_reply(user_id: str) -> str | None:
    from django.core.cache import cache
    return cache.get(f"tg_pending_reply:{user_id}")


def _clear_pending_reply(user_id: str) -> None:
    from django.core.cache import cache
    cache.delete(f"tg_pending_reply:{user_id}")


def _update_dm_status(dm_uuid: str, status: str) -> None:
    try:
        from apps.diplomacy.models import DiplomacyMessage
        DiplomacyMessage.objects.filter(pk=dm_uuid).update(status=status)
    except Exception:
        pass


def _dm_already_actioned(dm_uuid: str) -> bool:
    try:
        from apps.diplomacy.models import DiplomacyMessage
        dm = DiplomacyMessage.objects.filter(pk=dm_uuid).only("status").first()
        return dm is not None and dm.status in ("action_taken", "replied")
    except Exception:
        return False
