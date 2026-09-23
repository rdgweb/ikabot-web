"""Reorganizar edificios -- trocar posicoes na cidade (N-50).

Fluxo real do jogo (capturado 2026-09-22 na HAVIT s78, lendo o JS do jogo;
nenhum save foi enviado na captura):
  1. view=updateGlobalData&backgroundView=city&currentCityId=C&ajax=1
     -> updateGlobalData[1].backgroundData.position[0..24]
        {buildingId, building, level, isBusy, groundId, allowedBuildings, ...}
  2. view=saveCityBuildingsPositions (tela com o botao Salvar, aberta ao
     entrar no modo "Reorganizar edificios")
  3. action=SaveBuildingPositions&cityId=C&backgroundView=city&currentCityId=C
     &actionRequest=T&ajax=1, corpo moves[i][from], moves[i][to], cityId

plan_building_moves() porta swapPositionsIfPossible (regras) e
logSuccessfulMoves (montagem da lista de moves) do JS do jogo.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..constants import GAME_AJAX_HEADERS
from ..exceptions import ActionError
from .base_action import BaseAction


def _swap_rejection(positions: list[dict[str, Any]], frm: int, to: int) -> str:
    """Why the game would refuse moving the building at `frm` onto `to` ("" = allowed)."""
    total = len(positions)
    if frm == to or not (0 <= frm < total) or not (0 <= to < total):
        return "posicao_invalida"
    a, b = positions[frm], positions[to]
    if a.get("buildingId") is None:
        return "origem_vazia"
    b_has_building = b.get("buildingId") is not None
    if a.get("buildingId") not in (b.get("allowedBuildings") or []):
        return "destino_nao_aceita"
    if b_has_building and b.get("buildingId") not in (a.get("allowedBuildings") or []):
        return "origem_nao_aceita"
    if a.get("isBusy") or b.get("isBusy"):
        return "ocupado"
    if a.get("completed") or b.get("completed"):
        return "em_construcao"
    if a.get("inConstructionList") or b.get("inConstructionList"):
        return "na_fila_de_construcao"
    if "locked" in str(b.get("building") or "").lower():
        return "posicao_bloqueada"
    return ""


def _log_successful_move(moves: list[dict[str, int]], frm: int, to: int, b_has_building: bool) -> None:
    """Port of the game's logSuccessfulMoves: chained swaps rewrite earlier moves."""
    previous_a = previous_b = False
    for move in moves:
        if move["to"] == frm:
            previous_a = True
            move["to"] = to
            continue
        if b_has_building and move["to"] == to:
            previous_b = True
            move["to"] = frm
    if not previous_a:
        moves.append({"from": frm, "to": to})
    if b_has_building and not previous_b:
        moves.append({"from": to, "to": frm})


def plan_building_moves(positions: list[dict[str, Any]], swaps: Iterable[Any]) -> dict[str, Any]:
    """Apply `swaps` ([from, to] pairs, in order) with the game's own rules.

    Returns moves (what SaveBuildingPositions expects), the swaps applied and
    rejected (with the reason), and the resulting position list.
    """
    current = [dict(p) if isinstance(p, dict) else {} for p in positions]
    moves: list[dict[str, int]] = []
    applied: list[list[int]] = []
    rejected: list[dict[str, Any]] = []
    for pair in swaps or []:
        try:
            frm, to = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            rejected.append({"swap": pair, "reason": "formato_invalido"})
            continue
        reason = _swap_rejection(current, frm, to)
        if reason:
            rejected.append({"swap": [frm, to], "reason": reason})
            continue
        b_has_building = current[to].get("buildingId") is not None
        # The game swaps the whole position objects (swapPositionsIfPossible).
        current[frm], current[to] = current[to], current[frm]
        _log_successful_move(moves, frm, to, b_has_building)
        applied.append([frm, to])
    return {"moves": moves, "applied": applied, "rejected": rejected, "positions": current}


def moves_payload(city_id: int | str, moves: list[dict[str, int]]) -> dict[str, str]:
    """Form fields exactly as jQuery serialises {moves: [...], cityId} in the game."""
    data: dict[str, str] = {}
    for index, move in enumerate(moves):
        data[f"moves[{index}][from]"] = str(int(move["from"]))
        data[f"moves[{index}][to]"] = str(int(move["to"]))
    data["cityId"] = str(city_id)
    return data


class BuildingPositionsAction(BaseAction):
    def _refresh_action_request(self, payload: Any) -> None:
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                token = str(item[1].get("actionRequest") or "").strip()
                if token:
                    self.client._action_request = token

    def fetch_positions(self, city_id: int | str) -> list[dict[str, Any]]:
        """Current 25 positions of `city_id`, straight from the game."""
        try:
            from services.resource_transport import change_current_city
            change_current_city(self.client, int(city_id))
        except Exception:
            pass
        resp = self.client._request(
            "POST", self.client._server_url,
            data={
                "view": "updateGlobalData", "backgroundView": "city", "currentCityId": str(city_id),
                "actionRequest": self.client._action_request, "ajax": "1",
            },
            headers=GAME_AJAX_HEADERS,
        )
        try:
            payload = resp.json()
        except Exception as exc:
            raise ActionError(f"Resposta invalida ao ler posicoes: {exc}", action="building_positions")
        self._refresh_action_request(payload)
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                background = item[1].get("backgroundData") or {}
                if str(background.get("id") or "") != str(city_id):
                    raise ActionError(
                        f"O jogo devolveu a cidade {background.get('id')} em vez de {city_id}",
                        action="building_positions",
                    )
                positions = background.get("position")
                if isinstance(positions, list) and positions:
                    return positions
        raise ActionError("Posicoes da cidade nao encontradas na resposta", action="building_positions")

    def save(self, city_id: int | str, moves: list[dict[str, int]]) -> list[dict[str, Any]]:
        """Send the moves the way the game does. Returns the game's feedback messages."""
        view = self.client._request(
            "POST", self.client._server_url,
            data={
                "view": "saveCityBuildingsPositions", "backgroundView": "city", "currentCityId": str(city_id),
                "actionRequest": self.client._action_request, "ajax": "1",
            },
            headers=GAME_AJAX_HEADERS,
        )
        try:
            self._refresh_action_request(view.json())
        except Exception:
            pass
        data = {
            "action": "SaveBuildingPositions",
            "backgroundView": "city",
            "currentCityId": str(city_id),
            "actionRequest": self.client._action_request,
            "ajax": "1",
            **moves_payload(city_id, moves),
        }
        resp = self.client._request("POST", self.client._server_url, data=data, headers=GAME_AJAX_HEADERS)
        feedbacks: list[dict[str, Any]] = []
        try:
            payload = resp.json()
        except Exception:
            return feedbacks
        self._refresh_action_request(payload)
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "provideFeedback" and isinstance(item[1], list):
                feedbacks.extend(entry for entry in item[1] if isinstance(entry, dict))
        return feedbacks
