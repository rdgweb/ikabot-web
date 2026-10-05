"""Runner Demolir Edificios (ac=1303, N-57).

Um job por cidade. Inputs: city_id, city_name, dry_run e
items = [{position, building, level, levels, complete, name}], onde level e o
nivel que o usuario viu no painel, levels quantos niveis tirar e complete
pede a demolicao completa (com captcha).

Demolir nao tem volta, entao:
  - as posicoes sao relidas do jogo antes de cada item e depois de cada nivel;
  - so se demole se o edificio e o nivel forem exatamente os esperados
    (demolish_refusal); qualquer diferenca pula o item;
  - se o jogo nao ficar no nivel esperado depois de um passo, o job para;
  - dry_run abre a tela de confirmacao do jogo e nunca envia a demolicao;
  - o job nunca e reexecutado sozinho: rodar de novo encontra niveis
    diferentes dos esperados e nao demole nada.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.demolish import (
    DemolishBuildingAction,
    building_name,
    demolish_refusal,
    position_level,
)
from runners.base import BaseRunner, RunnerResult
from runners.building_positions import _patch_city_buildings

logger = logging.getLogger(__name__)

MAX_ITEMS = 25
MAX_CAPTCHA_ATTEMPTS = 3

REFUSAL_LABELS = {
    "posicao_vazia": "nao ha edificio nessa posicao",
    "edificio_diferente": "o edificio na posicao nao e o que foi escolhido",
    "nao_demolivel": "esse edificio nao pode ser demolido",
    "nivel_diferente": "o nivel no jogo e diferente do que estava no painel",
    "em_obra": "edificio em obra ou na fila de construcao",
    "ocupado": "edificio ocupado",
    "posicao_invalida": "posicao invalida",
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_items(raw: Any) -> list[dict[str, Any]]:
    """Normalised demolition items; malformed entries are dropped."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        position = _to_int(entry.get("position"), -1)
        building = str(entry.get("building") or "").strip()
        level = _to_int(entry.get("level"), 0)
        complete = bool(entry.get("complete"))
        levels = level if complete else _to_int(entry.get("levels"), 0)
        if position < 0 or position in seen or not building or level < 1 or not (1 <= levels <= level):
            continue
        seen.add(position)
        items.append({
            "position": position, "building": building, "level": level,
            "levels": levels, "complete": complete,
            "name": str(entry.get("name") or building),
        })
    return items[:MAX_ITEMS]


def step_outcome(pos: Any, building: str, level_before: int) -> str:
    """What one demolished level left behind: "ok", "gone" (building destroyed) or "unexpected"."""
    name = building_name((pos or {}).get("building") if isinstance(pos, dict) else "")
    if name == "empty":
        return "gone" if level_before <= 1 else "unexpected"
    if name == building and position_level(pos) == level_before - 1:
        return "ok"
    return "unexpected"


@register_runner(1303)
class DemolishBuildingsRunner(BaseRunner):
    def _reflect_in_snapshot(self, jid: str, aid: str, ga_id: str, city_id: str, fresh: list[dict[str, Any]]) -> None:
        try:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id) or {}
            cities = [dict(c) for c in (snapshot.get("cities") or []) if isinstance(c, dict)]
            for city in cities:
                if str(city.get("id") or "") == str(city_id):
                    city["buildings"] = _patch_city_buildings(city.get("buildings") or [], fresh)
            self.hub.update_snapshot(
                aid,
                {
                    "base_snapshot": snapshot.get("base_snapshot") or {},
                    "cities": cities,
                    "military": snapshot.get("military") or {},
                    "source_job_id": jid,
                },
                game_account_id=ga_id,
            )
        except Exception as exc:
            self.log(jid, "warn", f"Falha ao refletir a demolicao no snapshot: {exc}")

    def _log_feedback(self, jid: str, feedbacks: list[dict[str, Any]]) -> None:
        for fb in feedbacks:
            text = str(fb.get("text") or "").strip()
            if text:
                self.log(jid, "info", f"Jogo: {text}")

    def _solve_captcha(self, jid: str, ga_id: str, city_id: str, item: dict[str, Any], image: bytes) -> str:
        result = self.hub.create_captcha_challenge(
            "pirate",
            base64.b64encode(image).decode("ascii"),
            game_account_id=ga_id or "",
            display_type="demolish",
            extra_data={"context": "demolish_building", "city_id": str(city_id), "position": item["position"]},
        )
        solution = str(result.get("solution") or "").strip().upper()
        if not solution and result.get("challenge_id"):
            self.log(jid, "info", f"Aguardando resolucao manual do captcha #{result['challenge_id']}")
            solution = self.hub.poll_captcha_solution(result["challenge_id"], timeout_sec=120, interval=10).strip().upper()
        return solution

    def _demolish_levels(self, jid, client, action, city_id: str, item: dict[str, Any], positions: list) -> tuple[str, list]:
        """Take item["levels"] levels off, one by one, checking the game after each."""
        position, building = item["position"], item["building"]
        current = item["level"]
        target = current - item["levels"]
        while current > target:
            self._log_feedback(jid, action.one_level(city_id, position, current))
            positions = client.get_building_positions(int(city_id))
            outcome = step_outcome(positions[position] if position < len(positions) else None, building, current)
            if outcome == "unexpected":
                self.log(
                    jid, "error",
                    f"{item['name']} (posicao {position}): o jogo nao ficou no nivel {current - 1} depois de demolir o nivel {current}. Parando.",
                )
                return "unexpected", positions
            current -= 1
            if outcome == "gone":
                self.log(jid, "info", f"{item['name']} (posicao {position}): edificio removido, posicao livre.")
                return "done", positions
            self.log(jid, "info", f"{item['name']} (posicao {position}): agora no nivel {current}.")
        return "done", positions

    def _demolish_complete(self, jid, client, action, ga_id: str, city_id: str, item: dict[str, Any], positions: list) -> tuple[str, list]:
        position, building, level = item["position"], item["building"], item["level"]
        for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
            view = action.confirmation(city_id, position, level, complete=True)
            image = view.get("captcha_image")
            if not image:
                self.log(jid, "warn", f"{item['name']}: a tela de demolicao completa veio sem a imagem do captcha (tentativa {attempt}).")
                continue
            try:
                solution = self._solve_captcha(jid, ga_id, city_id, item, image)
            except Exception as exc:
                self.log(jid, "warn", f"{item['name']}: falha ao resolver o captcha (tentativa {attempt}): {exc}")
                continue
            if not solution:
                self.log(jid, "warn", f"{item['name']}: captcha sem solucao (tentativa {attempt}).")
                continue
            self._log_feedback(jid, action.complete(city_id, position, level, solution))
            positions = client.get_building_positions(int(city_id))
            after = positions[position] if position < len(positions) else None
            if building_name((after or {}).get("building") if isinstance(after, dict) else "") == "empty":
                self.log(jid, "info", f"{item['name']} (posicao {position}): demolido completamente, posicao livre.")
                return "done", positions
            if demolish_refusal(after, building, level):
                self.log(jid, "error", f"{item['name']} (posicao {position}): estado inesperado depois da demolicao completa. Parando.")
                return "unexpected", positions
            self.log(jid, "warn", f"{item['name']}: o jogo nao aceitou o captcha (tentativa {attempt}).")
        return "captcha_failed", positions

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        city_id = str(inputs.get("city_id") or "").strip()
        items = parse_items(inputs.get("items"))
        dry_run = bool(inputs.get("dry_run"))
        if not city_id or not items:
            self.log(jid, "error", "city_id e items obrigatorios")
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        results: list[dict[str, Any]] = []
        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            action = DemolishBuildingAction(client)
            positions = client.get_building_positions(int(city_id))
            if dry_run:
                self.log(jid, "info", "SIMULACAO: nada sera demolido; so confiro as travas e abro a tela de confirmacao do jogo.")

            stopped = False
            for item in items:
                position = item["position"]
                label = f"{item['name']} (posicao {position}, nivel {item['level']})"
                pos = positions[position] if position < len(positions) else None
                reason = "posicao_invalida" if pos is None else demolish_refusal(pos, item["building"], item["level"])
                if reason:
                    self.log(jid, "warn", f"{label} NAO demolido: {REFUSAL_LABELS.get(reason, reason)}.")
                    results.append({"position": position, "status": "skipped", "reason": reason})
                    continue

                what = "demolir completamente" if item["complete"] else f"tirar {item['levels']} nivel(is), ate o nivel {item['level'] - item['levels']}"
                if dry_run:
                    view = action.confirmation(city_id, position, item["level"], complete=item["complete"])
                    extra = ""
                    if item["complete"]:
                        extra = " Captcha: imagem recebida." if view.get("captcha_image") else " Captcha: SEM imagem na tela."
                    seen = "tela de confirmacao aberta" if view.get("window") else "o jogo NAO abriu a tela de confirmacao"
                    self.log(jid, "info", f"SIMULACAO {label}: passaria nas travas para {what}; {seen}.{extra}")
                    results.append({"position": position, "status": "dry_run", "window": bool(view.get("window"))})
                    continue

                self.log(jid, "info", f"{label}: {what}.")
                if item["complete"]:
                    status, positions = self._demolish_complete(jid, client, action, ga_id, city_id, item, positions)
                else:
                    status, positions = self._demolish_levels(jid, client, action, city_id, item, positions)
                results.append({"position": position, "status": status})
                if status == "unexpected":
                    stopped = True
                    break

            if not dry_run:
                self._reflect_in_snapshot(jid, aid, ga_id, city_id, positions)
            self.save_game_client(ga_id, client)

            done = sum(1 for r in results if r["status"] == "done")
            skipped = sum(1 for r in results if r["status"] == "skipped")
            failed = sum(1 for r in results if r["status"] in ("unexpected", "captcha_failed"))
            if dry_run:
                self.log(jid, "info", f"Simulacao concluida: {len(results) - skipped} item(ns) passariam, {skipped} seriam pulados. Nada foi demolido.")
                return RunnerResult(success=True, data={"status": "dry_run", "city_id": city_id, "results": results})
            self.log(jid, "info" if not failed else "error", f"Demolicao: {done} concluido(s), {skipped} pulado(s), {failed} com falha.")
            return RunnerResult(
                success=not failed and not stopped,
                data={"status": "demolished" if done else "nothing_done", "city_id": city_id, "results": results},
            )
        except Exception as exc:
            logger.exception("DemolishBuildingsRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao demolir (o que ja foi demolido permanece): {exc}")
            return RunnerResult(success=False, data={"error": str(exc), "results": results})
