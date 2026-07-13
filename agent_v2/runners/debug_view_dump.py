"""Debug runner: captura generica de views do jogo. Action code: 9004.

Ferramenta de exploracao (contas autorizadas apenas): recebe uma lista de
views/params, faz cada request e salva a resposta bruta em /tmp/view_dump/
dentro do container do agent, com excertos no JobLog.

Inputs:
    captures: [{"name": "cinema", "params": {"view": "cinema"}}, ...]
              (cityId/currentCityId/actionRequest/ajax sao adicionados
              automaticamente quando ausentes)
    city_id:  opcional; default = primeira cidade do snapshot
"""

from __future__ import annotations

import os
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult

DUMP_DIR = "/tmp/view_dump"


@register_runner(9004)
class DebugViewDumpRunner(BaseRunner):
    """Dump generico de views para analise de markup."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        cinema_claim_test = bool(inputs.get("cinema_claim_test"))
        captures = [c for c in (inputs.get("captures") or []) if isinstance(c, dict) and c.get("params")]
        if not captures and not cinema_claim_test:
            self.log(jid, "error", "Nenhuma captura configurada (inputs.captures)")
            return RunnerResult(success=False, data={"error": "missing_captures"})

        os.makedirs(DUMP_DIR, exist_ok=True)

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)

        city_id = str(inputs.get("city_id") or "").strip()
        if not city_id:
            snapshot = self.hub.get_snapshot(game_account_id=ga_id)
            cities = (snapshot or {}).get("cities") or []
            city_id = str((cities[0] or {}).get("id") or "") if cities else ""
        if not city_id:
            self.log(jid, "error", "Nenhuma cidade disponivel")
            return RunnerResult(success=False, data={"error": "missing_city"})
        self.log(jid, "info", f"Cidade base: {city_id}")

        saved: list[str] = []
        for capture in captures[:15]:
            name = str(capture.get("name") or capture["params"].get("view") or "capture").strip()
            params = dict(capture["params"])
            params.setdefault("backgroundView", "city")
            params.setdefault("currentCityId", city_id)
            params.setdefault("cityId", city_id)
            params.setdefault("actionRequest", client._action_request)
            params.setdefault("ajax", "1")
            # actionRequest sempre atualizado
            params["actionRequest"] = client._action_request
            try:
                resp = client._request("POST", client._server_url, data=params, headers=GAME_AJAX_HEADERS)
                path = os.path.join(DUMP_DIR, f"{name}.txt")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(resp.text)
                saved.append(name)
                self.log(jid, "info", f"Capturado {name} ({len(resp.text)} chars)")
                # atualizar actionRequest da resposta quando presente
                import re as _re
                m = _re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', resp.text)
                if m:
                    client._action_request = m.group(1)
            except Exception as exc:
                self.log(jid, "warn", f"Captura {name} falhou: {exc}")
            time.sleep(1.5)

        if cinema_claim_test:
            import json as _json
            from game_client.constants import GAME_AJAX_HEADERS as _H
            # requestBonus 51 (resource) com videoId real, resposta crua
            st0 = client.get_cinema_state(int(city_id))
            vid0 = int(st0.get("video_id") or 0)
            raw = client._request("POST", client._server_url, data={
                "view": "noViewChange", "action": "AdVideoRewardAction", "function": "requestBonus",
                "bonusId": "51", "videoId": str(vid0), "cityId": city_id, "backgroundView": "city",
                "currentCityId": city_id, "actionRequest": client._action_request, "ajax": "1",
            }, headers=_H)
            self.log(jid, "info", f"RAW requestBonus 51 (video {vid0}): {raw.text[:900]}")
            state = client.get_cinema_state(int(city_id))
            self.log(jid, "info", f"Cinema state: video_id={state.get('video_id')} | rewards={_json.dumps(state.get('rewards'), ensure_ascii=False)[:600]}")
            # testa o favour (53) que tem cooldown observavel, senao o primeiro available
            target = next((r for r in state.get("rewards") or [] if r.get("bonus_id") == 53 and r.get("available")), None)
            target = target or next((r for r in state.get("rewards") or [] if r.get("available")), None)
            if target:
                vid = int(state.get("video_id") or 0)
                bid = int(target["bonus_id"])
                result = client.claim_cinema_bonus(int(city_id), bid, vid)
                self.log(jid, "info", f"Claim {target['key']} (bonusId={bid}, videoId={vid}): {_json.dumps(result, ensure_ascii=False)[:900]}")
                time.sleep(2)
                after = client.get_cinema_state(int(city_id))
                after_target = next((r for r in after.get("rewards") or [] if r.get("bonus_id") == bid), {})
                self.log(jid, "info", f"Apos claim bonus {bid}: available={after_target.get('available')} | next_available={after_target.get('next_available')}")
            else:
                self.log(jid, "info", "Nenhuma recompensa available para testar claim.")

        self.save_game_client(ga_id, client)
        self.log(jid, "info", f"Dump completo: {saved}")
        return RunnerResult(success=True, data={"status": "dump_complete", "files": saved, "city_id": city_id})
