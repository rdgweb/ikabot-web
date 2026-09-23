"""Runner Reorganizar Edificios (ac=1302, N-50).

Troca edificios de posicao numa cidade, do mesmo jeito que o "Reorganizar
edificios" do jogo. Inputs: city_id, swaps = [[de, para], ...] na ordem em
que o usuario arrastou (o hub monta a lista; copiar layout tambem gera swaps).

As posicoes sao relidas do jogo na hora e cada troca e validada com as
regras do proprio jogo; trocas invalidas sao puladas e registradas. Depois
de salvar, relê a cidade para conferir o resultado e atualiza o snapshot.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.runner_registry import register_runner
from game_client.actions.building_positions import plan_building_moves
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

MAX_SWAPS = 100

REJECTION_LABELS = {
    "posicao_invalida": "posicao invalida",
    "formato_invalido": "troca em formato invalido",
    "origem_vazia": "nao ha edificio na origem",
    "destino_nao_aceita": "o destino nao aceita esse edificio",
    "origem_nao_aceita": "a origem nao aceita o edificio do destino",
    "ocupado": "edificio ocupado",
    "em_construcao": "edificio em construcao",
    "na_fila_de_construcao": "edificio na fila de construcao",
    "posicao_bloqueada": "posicao bloqueada",
}


def _parse_swaps(raw: Any) -> list[list[int]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    swaps: list[list[int]] = []
    for pair in raw if isinstance(raw, list) else []:
        try:
            swaps.append([int(pair[0]), int(pair[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return swaps[:MAX_SWAPS]


def _building_name(raw: Any) -> str:
    name = str(raw or "").replace("constructionSite", "").strip()
    return "empty" if (not name or "buildingGround" in name) else name


def _patch_city_buildings(buildings: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Snapshot position entries rewritten from what the game returned after saving."""
    by_position = {
        int(entry.get("position")): dict(entry)
        for entry in buildings
        if isinstance(entry, dict) and str(entry.get("position", "")).strip().lstrip("-").isdigit()
    }
    patched: list[dict[str, Any]] = []
    for index, pos in enumerate(fresh):
        if not isinstance(pos, dict):
            continue
        entry = by_position.get(index, {"position": index, "is_upgrading": False})
        raw = str(pos.get("building") or "")
        name = _building_name(raw)
        entry.update({
            "position": index,
            "building": name,
            "level": int(pos.get("level") or 0) if name != "empty" else 0,
            "building_id": pos.get("buildingId"),
            "ground_id": pos.get("groundId"),
            "allowed": [int(b) for b in (pos.get("allowedBuildings") or []) if str(b).lstrip("-").isdigit()],
        })
        if name == "empty" and "buildingGround" in raw:
            entry["type"] = raw.replace("buildingGround", "").strip() or entry.get("type", "")
        patched.append(entry)
    return patched


@register_runner(1302)
class ReorderBuildingsRunner(BaseRunner):
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
            self.log(jid, "warn", f"Falha ao refletir o novo layout no snapshot: {exc}")

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        city_id = str(inputs.get("city_id") or "").strip()
        swaps = _parse_swaps(inputs.get("swaps"))
        if not city_id or not swaps:
            self.log(jid, "error", "city_id e swaps obrigatorios")
            return RunnerResult(success=False, data={"error": "missing_inputs"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            positions = client.get_building_positions(int(city_id))
            plan = plan_building_moves(positions, swaps)

            for item in plan["rejected"]:
                label = REJECTION_LABELS.get(item.get("reason"), item.get("reason"))
                self.log(jid, "warn", f"Troca {item.get('swap')} ignorada: {label}")
            if not plan["moves"]:
                self.save_game_client(ga_id, client)
                self.log(jid, "info", "Nenhuma troca valida para salvar; a cidade nao foi alterada.")
                return RunnerResult(success=True, data={"status": "nothing_to_do", "rejected": plan["rejected"]})

            self.log(jid, "info", f"Salvando {len(plan['applied'])} troca(s) na cidade {city_id} ({len(plan['moves'])} movimento(s))")
            feedbacks = client.save_building_positions(int(city_id), plan["moves"])
            for fb in feedbacks:
                text = str(fb.get("text") or "").strip()
                if text:
                    self.log(jid, "info", f"Jogo: {text}")

            fresh = client.get_building_positions(int(city_id))
            expected = [p.get("buildingId") for p in plan["positions"]]
            actual = [p.get("buildingId") if isinstance(p, dict) else None for p in fresh]
            self._reflect_in_snapshot(jid, aid, ga_id, city_id, fresh)
            self.save_game_client(ga_id, client)

            if actual != expected:
                self.log(jid, "error", "O jogo nao ficou com o layout esperado apos salvar; snapshot atualizado com o layout real.")
                return RunnerResult(success=False, data={"error": "layout_mismatch", "expected": expected, "actual": actual})

            self.log(jid, "info", f"Edificios reorganizados: {len(plan['applied'])} troca(s) aplicada(s), {len(plan['rejected'])} ignorada(s).")
            return RunnerResult(success=True, data={
                "status": "reordered", "city_id": city_id,
                "applied": plan["applied"], "rejected": plan["rejected"],
            })
        except Exception as exc:
            logger.exception("ReorderBuildingsRunner failed for job %s", jid)
            self.log(jid, "error", f"Falha ao reorganizar edificios: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
