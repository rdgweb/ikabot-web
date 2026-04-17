"""
Diplomacy runners.

Action codes:
    30  DiplomacyCheckRunner  — verifica inbox e notifica via Telegram (recorrente)
    31  DiplomacySendRunner   — envia mensagem/resposta/tratado (pontual, criado pelo webhook)
"""

from __future__ import annotations

import logging
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

# IDs de mensagens já vistos são guardados no checkpoint state com esta chave
_SEEN_KEY = "diplomacy_seen_ids"
# TTL máximo de IDs vistos (evitar crescimento infinito)
_SEEN_MAX = 500

# Labels para tipos de ação conhecidos (msg_type → label do botão)
_ACTION_LABELS: dict[int, str] = {
    79: "✅ Aceitar",
    80: "❌ Recusar",
}
# Subject hint para tipos conhecidos (para formatar o texto do Telegram)
_ACTION_SUBJECT: dict[int, str] = {
    79: "Tratado Cultural — Aceitar",
    80: "Tratado Cultural — Recusar",
}


def _build_message_text(msg: dict) -> str:
    """Formata uma mensagem do inbox para enviar via Telegram (HTML)."""
    sender = msg.get("sender") or "Desconhecido"
    subject = msg.get("subject") or "(sem assunto)"
    body = msg.get("body") or ""
    date = msg.get("date") or ""

    lines = [
        "📨 <b>Nova mensagem de diplomacia</b>",
        f"<b>De:</b> {sender}",
        f"<b>Assunto:</b> {subject}",
    ]
    if date:
        lines.append(f"<b>Data:</b> {date}")
    if body:
        lines.append(f"\n{body}")

    return "\n".join(lines)


def _build_action_text(msg: dict) -> str:
    """Formata uma mensagem com botões de ação para o Telegram."""
    sender = msg.get("sender") or "Desconhecido"
    subject = msg.get("subject") or "(sem assunto)"
    body = msg.get("body") or ""
    date = msg.get("date") or ""
    actions: list[dict] = msg.get("actions") or []

    # Tenta inferir tipo da mensagem pelos tipos de ação disponíveis
    action_types = {a["msg_type"] for a in actions}
    if {79, 80}.issubset(action_types) or 79 in action_types:
        header = "🤝 <b>Pedido de Tratado Cultural</b>"
    else:
        header = "📋 <b>Mensagem requer ação</b>"

    lines = [header, f"<b>De:</b> {sender}", f"<b>Assunto:</b> {subject}"]
    if date:
        lines.append(f"<b>Data:</b> {date}")
    if body:
        lines.append(f"\n{body}")
    lines.append("\nUse os botões abaixo para responder.")
    return "\n".join(lines)


def _build_inline_keyboard(actions: list[dict], game_account_id: str) -> dict:
    """Monta o inline_keyboard com um botão por ação disponível."""
    buttons = []
    for action in actions:
        msg_type = action["msg_type"]
        receiver_id = action["receiver_id"]
        label = _ACTION_LABELS.get(msg_type, f"Ação {msg_type}")
        callback_data = f"dip_action:{msg_type}:{receiver_id}:{game_account_id}"
        buttons.append({"text": label, "callback_data": callback_data})
    # Agrupar em linhas de 2
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return {"inline_keyboard": rows}


@register_runner(30)
class DiplomacyCheckRunner(BaseRunner):
    """Verifica o inbox de diplomacia e envia notificações via Telegram.

    Inputs esperados (definidos no catálogo):
        city_id          — ID de qualquer cidade do jogador (necessário para GET)
        interval_minutes — Intervalo de recheck em minutos (padrão 60)
        notify_telegram  — Enviar notificações pelo Telegram (padrão True)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        city_id = inputs.get("city_id")
        if not city_id:
            return RunnerResult(success=False, data={"error": "city_id é obrigatório"})

        interval_minutes = max(5, int(inputs.get("interval_minutes") or 60))
        notify_telegram = bool(inputs.get("notify_telegram", True))

        # Carregar IDs já vistos do estado salvo
        recovery = inputs.get("__recovery") if isinstance(inputs.get("__recovery"), dict) else {}
        seen_ids: set[str] = set(recovery.get(_SEEN_KEY) or [])

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            self.log(jid, "info", f"Verificando inbox de diplomacia (city_id={city_id})")

            result = client.get_diplomacy_inbox(city_id)
            messages = result.get("messages", [])
            self.log(jid, "info", f"Inbox: {len(messages)} mensagens encontradas")

            # Salvar todas as mensagens no hub (upsert)
            if messages:
                hub_messages = [
                    {
                        "game_msg_id": m["id"],
                        "sender": m.get("sender") or "",
                        "subject": m.get("subject") or "",
                        "body": m.get("body") or "",
                        "game_date": m.get("date") or "",
                        "receiver_id": m.get("receiver_id") or "",
                        "reply_to": m.get("reply_to") or "",
                        "actions": m.get("actions") or [],
                    }
                    for m in messages
                ]
                try:
                    save_result = self.hub.save_diplomacy_messages(ga_id, hub_messages)
                    self.log(jid, "info",
                             f"Mensagens salvas no hub: {save_result.get('saved', 0)} "
                             f"({save_result.get('new_count', 0)} novas)")
                except Exception as exc:
                    self.log(jid, "warning", f"Falha ao salvar mensagens no hub: {exc}")

            # Notificar apenas mensagens novas não lidas
            new_unread = [m for m in messages if m.get("unread") and m["id"] not in seen_ids]

            if not new_unread:
                self.log(jid, "info", "Nenhuma mensagem nova não lida")
            else:
                self.log(jid, "info", f"{len(new_unread)} mensagem(ns) nova(s) não lida(s)")

            for msg in new_unread:
                seen_ids.add(msg["id"])
                if notify_telegram:
                    self._notify_message(jid, aid, ga_id, msg, job)

            # Também marcar mensagens lidas como vistas para não notificar depois
            for msg in messages:
                seen_ids.add(msg["id"])

            # Manter tamanho do conjunto controlado
            if len(seen_ids) > _SEEN_MAX:
                seen_ids = set(list(seen_ids)[-_SEEN_MAX:])

            self.save_game_client(ga_id, client)

            return RunnerResult(
                success=True,
                reschedule_seconds=interval_minutes * 60,
                reschedule_inputs={
                    **inputs,
                    "__recovery": {_SEEN_KEY: list(seen_ids)},
                },
                data={
                    "total_messages": len(messages),
                    "new_unread": len(new_unread),
                },
            )

        except Exception as exc:
            self.log(jid, "error", f"Erro ao verificar diplomacia: {exc}")
            return RunnerResult(
                success=False,
                reschedule_seconds=interval_minutes * 60,
                reschedule_inputs={
                    **inputs,
                    "__recovery": {_SEEN_KEY: list(seen_ids)},
                },
                data={"error": str(exc)},
            )

    def _notify_message(
        self,
        job_id: str,
        account_id: str,
        game_account_id: str,
        msg: dict,
        job: dict,
    ) -> None:
        """Envia notificação Telegram para uma mensagem nova."""
        try:
            actions: list[dict] = msg.get("actions") or []
            msg_id = msg.get("id", "")
            receiver_id = msg.get("receiver_id", "")

            if actions:
                # Mensagem com botões inline para aceitar/recusar/etc.
                text = _build_action_text(msg)
                reply_markup = _build_inline_keyboard(actions, game_account_id)
            else:
                # Mensagem regular — instrução de resposta via /replyto
                text = _build_message_text(msg)
                if receiver_id:
                    reply_ref = f"{msg_id}:{receiver_id}:{game_account_id}"
                    text += f"\n\n<i>Para responder: <code>/replyto {reply_ref} &lt;texto&gt;</code></i>"
                reply_markup = None

            agent_name = str(job.get("agent") or "")
            self.hub.send_notification(
                event="diplomacy_message",
                game_account_id=game_account_id,
                account_id=account_id,
                title="Nova mensagem de diplomacia",
                body=text,
                agent_name=agent_name,
                metadata={"reply_markup": reply_markup} if reply_markup else {},
            )
        except Exception:
            logger.exception("Falha ao enviar notificação de diplomacia")


@register_runner(31)
class DiplomacySendRunner(BaseRunner):
    """Envia uma mensagem, resposta ou resposta de tratado na diplomacia.

    Criado pelo webhook do Telegram quando o usuário clica em botão inline
    ou envia o comando /replyto.

    Inputs esperados (definidos no catálogo):
        city_id      — ID de qualquer cidade do jogador (para obter actionRequest)
        receiver_id  — ID do jogador destinatário
        msg_type     — 50=mensagem, 79=aceitar tratado, 80=recusar tratado (ou outro)
        content      — Texto da mensagem (obrigatório para msg_type=50)
        reply_to     — ID da mensagem original (opcional, para respostas)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        city_id = inputs.get("city_id")
        receiver_id = inputs.get("receiver_id")
        msg_type = int(inputs.get("msg_type") or 50)
        content = inputs.get("content") or ""
        reply_to = inputs.get("reply_to")

        if not city_id:
            return RunnerResult(success=False, data={"error": "city_id é obrigatório"})
        if not receiver_id:
            return RunnerResult(success=False, data={"error": "receiver_id é obrigatório"})
        if msg_type == 50 and not content:
            return RunnerResult(success=False, data={"error": "content é obrigatório para msg_type=50"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)

            if msg_type == 79:
                self.log(jid, "info", f"Aceitando tratado cultural de player_id={receiver_id}")
                client.accept_treaty(receiver_id)
                action_label = "Tratado aceito"
            elif msg_type == 80:
                self.log(jid, "info", f"Recusando tratado cultural de player_id={receiver_id}")
                client.decline_treaty(receiver_id)
                action_label = "Tratado recusado"
            else:
                self.log(jid, "info", f"Enviando mensagem para player_id={receiver_id} (replyTo={reply_to})")
                client.send_diplomacy_message(
                    receiver_id=receiver_id,
                    content=content,
                    reply_to=reply_to,
                )
                action_label = "Mensagem enviada"

            self.save_game_client(ga_id, client)
            self.log(jid, "info", action_label)

            return RunnerResult(success=True, data={"action": action_label})

        except Exception as exc:
            self.log(jid, "error", f"Erro ao enviar diplomacia: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
