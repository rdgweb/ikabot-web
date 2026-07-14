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

        fetch_home = bool(inputs.get("fetch_home"))
        cinema_claim_test = bool(inputs.get("cinema_claim_test"))
        rename_probe = inputs.get("rename_probe")  # {"city_id":..,"name":..}
        captures = [c for c in (inputs.get("captures") or []) if isinstance(c, dict) and c.get("params")]
        if not captures and not cinema_claim_test and not fetch_home and not inputs.get("fetch_assets") and not rename_probe:
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

        for asset_url in (inputs.get("fetch_assets") or []):
            try:
                full = "https:" + asset_url if asset_url.startswith("//") else (
                    asset_url if asset_url.startswith("http") else client._server_url.rsplit("/", 1)[0] + "/" + asset_url.lstrip("/")
                )
                resp = client.session.get(full, timeout=30)
                import os as _os
                fname = _os.path.basename(asset_url.split("?")[0]) or "asset.bin"
                with open(os.path.join(DUMP_DIR, fname), "wb") as fh:
                    fh.write(resp.content)
                self.log(jid, "info", f"Asset {fname}: {len(resp.content)} bytes")
            except Exception as exc:
                self.log(jid, "warn", f"Asset {asset_url} falhou: {exc}")
            time.sleep(0.8)

        if fetch_home:
            import re as _re
            home = client.session.get(client._server_url, timeout=30).text
            with open(os.path.join(DUMP_DIR, "home.html"), "w", encoding="utf-8") as fh:
                fh.write(home)
            css_links = _re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', home)
            self.log(jid, "info", f"home CSS links: {css_links[:20]}")
            for css_url in css_links[:8]:
                full = css_url if css_url.startswith("http") else ("https:" + css_url if css_url.startswith("//") else client._server_url.rsplit("/", 1)[0] + "/" + css_url.lstrip("/"))
                try:
                    css = client.session.get(full, timeout=30).text
                    fname = "css_" + _re.sub(r"[^a-zA-Z0-9]", "_", css_url)[-40:] + ".css"
                    with open(os.path.join(DUMP_DIR, fname), "w", encoding="utf-8") as fh:
                        fh.write(css)
                    hits = css.count("itemIcon") + css.count("crystalBonus")
                    self.log(jid, "info", f"CSS {fname}: {len(css)} chars | itemIcon/bonus hits={hits}")
                except Exception as exc:
                    self.log(jid, "warn", f"CSS {css_url} falhou: {exc}")
                time.sleep(0.8)

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

        if rename_probe:
            from game_client.constants import GAME_AJAX_HEADERS as _H
            from services.resource_transport import change_current_city as _ccc
            rc_id = int(rename_probe.get("city_id") or city_id)
            rc_name = str(rename_probe.get("name") or "")
            _ccc(client, rc_id)
            # 1. abre o townHall e captura o actionRequest fresco
            th = client._request("POST", client._server_url, data={
                "view": "townHall", "cityId": rc_id, "position": "0",
                "backgroundView": "city", "currentCityId": rc_id,
                "templateView": "townHall", "actionRequest": client._action_request, "ajax": "1",
            }, headers=_H)
            import re as _re
            mt = _re.search(r'"actionRequest"\s*:\s*"([a-f0-9]{32})"', th.text)
            if mt:
                client._action_request = mt.group(1)
            self.log(jid, "info", f"townHall aberto, actionRequest={client._action_request[:10]}...")
            # 2. rename com o token fresco
            rn = client._request("POST", client._server_url, data={
                "action": "CityScreen", "function": "rename", "cityId": rc_id, "position": "0",
                "name": rc_name, "backgroundView": "city", "currentCityId": rc_id,
                "templateView": "townHall", "actionRequest": client._action_request, "ajax": "1",
            }, headers=_H)
            with open(os.path.join(DUMP_DIR, "rename_resp.txt"), "w", encoding="utf-8") as fh:
                fh.write(rn.text)
            self.log(jid, "info", f"rename resp salvo ({len(rn.text)} chars) | inicio: {rn.text[:300]}")
            # revert opcional: renomeia de volta para o nome informado em revert_to
            revert_to = str(rename_probe.get("revert_to") or "")
            if revert_to:
                time.sleep(1.5)
                rn2 = client._request("POST", client._server_url, data={
                    "action": "CityScreen", "function": "rename", "cityId": rc_id, "position": "0",
                    "name": revert_to, "backgroundView": "city", "currentCityId": rc_id,
                    "templateView": "townHall", "actionRequest": client._action_request, "ajax": "1",
                }, headers=_H)
                self.log(jid, "info", f"revertido para '{revert_to}'")
            self.save_game_client(ga_id, client)
            return RunnerResult(success=True, data={"status": "rename_probe"})

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
