"""Diplomacy actions — read inbox and send/reply to messages.

API reference (captured 2026-04-16 from s78-br):

  Get inbox (AJAX GET):
    GET index.php?view=diplomacyAdvisor&ajax=1&cityId=<id>
    X-Requested-With: XMLHttpRequest
    Response: old format [["updateGlobalData", {...}], ["changeView", ["diplomacyAdvisor", "HTML"]], ...]
    HTML contains <tr id="messageNNNNN"> rows (header) + hidden <tr id="tbl_mailNNNNN"> (body)
    + <tr id="tbl_replyNNNNN"> with reply/treaty action buttons.

  Send/reply message (POST AJAX):
    action=Messages&function=send&actionRequest=<token>&ajax=1
    receiverId=<player_id>
    msgType=50        (regular message)
    content=<text>
    replyTo=<msg_id>  (optional — for replies)

  Accept cultural treaty:
    action=Messages&function=send&actionRequest=<token>&ajax=1
    receiverId=<sender_player_id>
    msgType=79

  Decline cultural treaty:
    action=Messages&function=send&actionRequest=<token>&ajax=1
    receiverId=<sender_player_id>
    msgType=80

Message types found in HTML:
    50 = regular message
    79 = accept cultural treaty
    80 = decline cultural treaty

Notes:
  - The response uses the OLD Ikariam format: [["commandName", data], ...]
    NOT the new {"type": N, "data": ...} format used by most actions.
  - Sending/treaty responses return [["reload", ...]] or similar — no content to parse.
  - Message IDs are integers (e.g. 342412).
  - Player IDs (receiverId) are integers (e.g. 104196).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction

logger = logging.getLogger(__name__)

# Regex patterns for parsing diplomacy HTML
# (Based on real HTML captured from Ikariam s78-br, 2026-04-17)
#
# Message row structure:
#   <tr id="messageNNNNN" class="entry [alt] [new]" ...>
#     <td class="smallright">...</td>
#     <td class="smallleft">...</td>
#     <td onclick="..."><a href="#"><span class='avatarName'>PlayerName</span></a></td>
#     <td class="subject" onclick="..."> Subject text </td>
#     <td title="Para a cidade do emissor">...</td>
#     <td onclick="...">  dd.mm.yyyy h:mm:ss </td>  ← date (no class, plain text only)
#   </tr>
#   <tr id="tbl_mailNNNNN" class="text invisible">
#     <td colspan="6" class="msgText">body text</td>
#   </tr>
#   <tr id="tbl_replyNNNNN" class="text invisible">
#     <td colspan="6" class="reply">
#       <!-- reply link: -->
#       <a href="?view=sendIKMessage&receiverId=X&replyTo=Y">Resposta</a>
#       <!-- OR treaty buttons: -->
#       <a href="?view=sendIKMessage&receiverId=X&msgType=79">Aceitar</a>
#       <a href="?view=sendIKMessage&receiverId=X&msgType=80">Negar</a>
#     </td>
#   </tr>

_MSG_ROW_RE = re.compile(r'<tr[^>]+id="message(\d+)"', re.IGNORECASE)

# Sender: avatarName span inside the message row
_MSG_FROM_RE = re.compile(
    r'<tr[^>]+id="message(\d+)"[\s\S]{0,3000}?'
    r"<span class='avatarName'>([^<]+)</span>",
    re.IGNORECASE,
)

# Subject: td with class="subject"
_MSG_SUBJECT_RE = re.compile(
    r'<tr[^>]+id="message(\d+)"[\s\S]{0,3000}?'
    r'<td class="subject"[^>]*>\s*([^<\n]+)',
    re.IGNORECASE,
)

# Date: last <td onclick> with a bare date string (dd.mm.yyyy h:mm:ss), no child tags
_MSG_DATE_RE = re.compile(
    r'<tr[^>]+id="message(\d+)"[\s\S]{0,4000}?'
    r'<td[^>]*onclick[^>]*>\s*(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2})\s*</td>',
    re.IGNORECASE,
)

# Is-unread marker: "new" CSS class on the row
_MSG_UNREAD_RE = re.compile(
    r'<tr[^>]+id="message(\d+)"[^>]*class="[^"]*\bnew\b',
    re.IGNORECASE,
)

# Body: td with class="msgText" inside tbl_mail row
_MSG_BODY_RE = re.compile(
    r'<tr[^>]+id="tbl_mail(\d+)"[\s\S]*?'
    r'<td[^>]*class="[^"]*msgText[^"]*"[^>]*>([\s\S]*?)</td>',
    re.IGNORECASE,
)

# Reply href: view=sendIKMessage with replyTo param → regular reply
# Groups: (msg_id, receiver_id, reply_to_msg_id)
_REPLY_HREF_RE = re.compile(
    r'<tr[^>]+id="tbl_reply(\d+)"[\s\S]{0,2000}?'
    r'view=sendIKMessage&receiverId=(\d+)&replyTo=(\d+)',
    re.IGNORECASE,
)

# Action buttons: view=sendIKMessage with msgType != 50 (treaty accept/decline etc.)
# Groups: (msg_id, receiver_id, msg_type)
# NOTE: for treaties both 79 (accept) and 80 (decline) appear in the same tbl_reply block
_ACTION_BUTTON_RE = re.compile(
    r'<tr[^>]+id="tbl_reply(\d+)"[\s\S]{0,3000}?'
    r'view=sendIKMessage&receiverId=(\d+)&msgType=(\d+)',
    re.IGNORECASE,
)
# Strip HTML tags for message body
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# HTML entity decoding helpers
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&nbsp;": " ",
}


def _strip_html(html: str) -> str:
    text = _HTML_TAG_RE.sub("", html)
    for entity, char in _HTML_ENTITIES.items():
        text = text.replace(entity, char)
    return text.strip()


class DiplomacyInboxAction(BaseAction):
    """Read the diplomacy advisor inbox and return parsed messages.

    Uses GET (not POST AJAX) because the diplomacy view is a page-based
    AJAX response in the old Ikariam format.
    """

    def execute(self, city_id: int | str, **kwargs: Any) -> dict[str, Any]:
        """Fetch the diplomacy inbox for the given city.

        Args:
            city_id: Any valid city ID for the account (required to load the view).

        Returns:
            Dict with keys:
                "messages": list of message dicts, each containing:
                    - id (str): message ID
                    - sender (str): player name
                    - subject (str): subject line
                    - body (str): plain-text message body
                    - date (str): date string from the game
                    - unread (bool): True if message has the "new" CSS class
                    - receiver_id (str): player ID to use for replies
                    - reply_to (str): message ID to use as replyTo param
                    - actions (list): available action buttons, each {"msg_type": int, "receiver_id": str}
                      e.g. [{"msg_type": 79, "receiver_id": "104196"}, {"msg_type": 80, "receiver_id": "104196"}]
                      Empty list = regular message (reply-only)
        """
        html = self._fetch_inbox_html(city_id)
        if not html:
            return {"messages": [], "raw_html_length": 0}

        messages = self._parse_messages(html)
        return {"messages": messages, "raw_html_length": len(html)}

    def _fetch_inbox_html(self, city_id: int | str) -> str:
        """GET the diplomacyAdvisor AJAX view and extract HTML."""
        params = {
            "view": "diplomacyAdvisor",
            "ajax": "1",
            "cityId": str(city_id),
        }
        headers = dict(GAME_AJAX_HEADERS)
        headers["Accept"] = "*/*"

        resp = self.client._request(
            "GET",
            self.client._server_url,
            params=params,
            headers=headers,
        )

        raw = resp.text
        if not raw.strip().startswith("["):
            logger.warning("Diplomacy inbox: unexpected non-JSON response (%d chars)", len(raw))
            return ""

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Diplomacy inbox: JSON parse error: %s", exc)
            return ""

        # Old format: [["changeView", ["diplomacyAdvisor", "<HTML>"]], ...]
        # Find the changeView entry with diplomacyAdvisor HTML
        for entry in data:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            cmd = entry[0]
            payload = entry[1]
            if cmd == "changeView" and isinstance(payload, (list, tuple)) and len(payload) >= 2:
                html = payload[1]
                if isinstance(html, str) and "message" in html.lower():
                    return html

        logger.warning("Diplomacy inbox: changeView entry not found in response")
        return ""

    def _parse_messages(self, html: str) -> list[dict[str, Any]]:
        """Parse the diplomacy HTML into structured message dicts."""
        # Collect all message IDs that appear as rows
        msg_ids = [m.group(1) for m in _MSG_ROW_RE.finditer(html)]
        if not msg_ids:
            return []

        # Build lookup maps for each parsed attribute
        unread_ids = {m.group(1) for m in _MSG_UNREAD_RE.finditer(html)}

        from_map: dict[str, str] = {}
        for m in _MSG_FROM_RE.finditer(html):
            from_map[m.group(1)] = _strip_html(m.group(2))

        date_map: dict[str, str] = {}
        for m in _MSG_DATE_RE.finditer(html):
            date_map[m.group(1)] = _strip_html(m.group(2))

        subject_map: dict[str, str] = {}
        for m in _MSG_SUBJECT_RE.finditer(html):
            subject_map[m.group(1)] = _strip_html(m.group(2))

        body_map: dict[str, str] = {}
        for m in _MSG_BODY_RE.finditer(html):
            body_map[m.group(1)] = _strip_html(m.group(2))

        # Reply href: receiverId + replyTo for regular messages
        # Groups: (msg_id, receiver_id, reply_to_msg_id)
        reply_receiver_map: dict[str, str] = {}
        reply_to_map: dict[str, str] = {}
        for m in _REPLY_HREF_RE.finditer(html):
            reply_receiver_map[m.group(1)] = m.group(2)
            reply_to_map[m.group(1)] = m.group(3)

        # Action buttons: collect all non-regular actions per message
        # Groups: (msg_id, receiver_id, msg_type) — href order: receiverId before msgType
        # A single tbl_reply block can have multiple buttons (e.g. 79=accept, 80=decline)
        actions_map: dict[str, list[dict]] = {}
        for m in _ACTION_BUTTON_RE.finditer(html):
            msg_id = m.group(1)
            receiver_id = m.group(2)
            msg_type = int(m.group(3))
            if msg_id not in actions_map:
                actions_map[msg_id] = []
            # avoid duplicates
            entry = {"msg_type": msg_type, "receiver_id": receiver_id}
            if entry not in actions_map[msg_id]:
                actions_map[msg_id].append(entry)

        messages = []
        for msg_id in msg_ids:
            messages.append({
                "id": msg_id,
                "sender": from_map.get(msg_id, ""),
                "subject": subject_map.get(msg_id, ""),
                "body": body_map.get(msg_id, ""),
                "date": date_map.get(msg_id, ""),
                "unread": msg_id in unread_ids,
                "receiver_id": reply_receiver_map.get(msg_id, ""),
                "reply_to": reply_to_map.get(msg_id, msg_id),
                "actions": actions_map.get(msg_id, []),
            })

        return messages


class DiplomacySendAction(BaseAction):
    """Send a message, reply, or treaty response.

    Uses the standard AJAX POST path. The old-format response is benign
    (the json_parser skips list entries without raising errors).
    """

    def execute(
        self,
        receiver_id: int | str,
        msg_type: int = 50,
        content: str = "",
        reply_to: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a diplomacy message or treaty response.

        Args:
            receiver_id: Target player ID.
            msg_type: 50=regular, 79=accept treaty, 80=decline treaty.
            content: Message text (required for msgType=50).
            reply_to: Original message ID (optional, for replies).

        Returns:
            Parsed AJAX response (usually empty for this old-format action).
        """
        msg_type_str = str(msg_type)
        if msg_type_str in ("50", "60") and not content:
            raise ActionError(f"content is required for msgType={msg_type_str}", action="Messages&function=send")

        params: dict[str, Any] = {
            "receiverId": str(receiver_id),
            "msgType": str(msg_type),
            "closeView": "1",
        }
        if content:
            params["content"] = content
        if reply_to is not None:
            params["replyTo"] = str(reply_to)

        logger.info(
            "Diplomacy send: receiverId=%s msgType=%s replyTo=%s",
            receiver_id, msg_type, reply_to,
        )
        return self._ajax_request("Messages&function=send", params)
