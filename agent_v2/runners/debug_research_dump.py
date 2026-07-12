"""Debug runner: dump da arvore de pesquisas (views/HTML brutos). Action code: 9003.

Exploracao N-27: capturar como o jogo expõe, por pesquisa, o nome, os
requisitos (ex. Camara Municipal nivel 5) e o status (pesquisada ou nao).
Roda apenas em contas de exploracao autorizadas. Nao executa nenhuma acao
no jogo alem de leitura de telas.

Saida: arquivos em /tmp/research_dump/ dentro do container do agent
(researchAdvisor.json, researchOverview.txt, researchDetail_<id>.txt ...)
+ excertos no JobLog.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from core.runner_registry import register_runner
from game_client.constants import GAME_AJAX_HEADERS
from runners.base import BaseRunner, RunnerResult

DUMP_DIR = "/tmp/research_dump"


@register_runner(9003)
class DebugResearchDumpRunner(BaseRunner):
    """Dump das telas de pesquisa para analise de markup (N-27)."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        os.makedirs(DUMP_DIR, exist_ok=True)

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais nao encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})
        client = self.get_or_login_game_client(jid, aid, ga_id, creds)

        # Cidade com academia (via snapshot)
        snapshot = self.hub.get_snapshot(game_account_id=ga_id)
        city_id = ""
        for city in (snapshot or {}).get("cities") or []:
            for building in city.get("buildings") or []:
                if str(building.get("building") or "") == "academy":
                    city_id = str(city.get("id") or "")
                    break
            if city_id:
                break
        if not city_id:
            self.log(jid, "error", "Nenhuma cidade com academia no snapshot")
            return RunnerResult(success=False, data={"error": "academy_not_found"})
        self.log(jid, "info", f"Cidade com academia: {city_id}")

        captured: list[str] = []

        def _save(name: str, content: str) -> None:
            path = os.path.join(DUMP_DIR, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            captured.append(name)
            self.log(jid, "info", f"Capturado {name} ({len(content)} chars)")

        # 1. researchAdvisor (payload que ja usamos hoje) — referencia
        try:
            resp = client._request(
                "POST",
                client._server_url,
                data={
                    "view": "researchAdvisor",
                    "oldView": "updateGlobalData",
                    "cityId": city_id,
                    "backgroundView": "city",
                    "currentCityId": city_id,
                    "templateView": "researchAdvisor",
                    "actionRequest": client._action_request,
                    "ajax": "1",
                },
                headers=GAME_AJAX_HEADERS,
            )
            _save("researchAdvisor.json", resp.text)
        except Exception as exc:
            self.log(jid, "warn", f"researchAdvisor falhou: {exc}")

        time.sleep(1.5)

        # 2. Cada ramo via noViewChange&researchType — o load_js.params traz a
        # arvore (explored/gray/red) e o detalhe da pesquisa selecionada
        # (currResearchPrecond com as exigencias).
        pending_ids: list[str] = []
        for research_type in ("seafaring", "economy", "knowledge", "military", "mythology"):
            try:
                resp = client._request(
                    "POST",
                    client._server_url,
                    data={
                        "view": "noViewChange",
                        "researchType": research_type,
                        "oldView": "researchAdvisor",
                        "templateView": "researchAdvisor",
                        "cityId": city_id,
                        "backgroundView": "city",
                        "currentCityId": city_id,
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=GAME_AJAX_HEADERS,
                )
                _save(f"branch_{research_type}.json", resp.text)
                # researchIds das pesquisas NAO exploradas deste ramo.
                # O bloco de dados (params com currResearchType) pode vir em
                # updateTemplateData.load_js ou em updateViewScriptData — extrai
                # por regex do texto bruto para nao depender da estrutura.
                try:
                    m_params = re.search(r'"params":\s*"((?:[^"\\]|\\.)*currResearchType(?:[^"\\]|\\.)*)"', resp.text)
                    params = json.loads(json.loads(f'"{m_params.group(1)}"')) if m_params else {}
                    found = 0
                    for name, info in (params.get("currResearchType") or {}).items():
                        li_class = str((info or {}).get("liClass") or "")
                        if "explored" in li_class:
                            continue
                        m = re.search(r"researchId=(\d+)", str((info or {}).get("aHref") or ""))
                        if m:
                            pending_ids.append(m.group(1))
                            found += 1
                            self.log(jid, "info", f"[{research_type}] pendente: {name} | li={li_class} | id={m.group(1)}")
                    if not params:
                        self.log(jid, "warn", f"[{research_type}] sem bloco currResearchType na resposta")
                except Exception as exc:
                    self.log(jid, "warn", f"parse branch {research_type} falhou: {exc}")
            except Exception as exc:
                self.log(jid, "warn", f"branch {research_type} falhou: {exc}")
            time.sleep(1.5)

        # 3. Detalhe de cada pesquisa pendente (precond com exigencias)
        for research_id in list(dict.fromkeys(pending_ids))[:20]:
            try:
                resp = client._request(
                    "POST",
                    client._server_url,
                    data={
                        "view": "noViewChange",
                        "researchId": research_id,
                        "oldView": "researchAdvisor",
                        "templateView": "researchAdvisor",
                        "cityId": city_id,
                        "backgroundView": "city",
                        "currentCityId": city_id,
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=GAME_AJAX_HEADERS,
                )
                _save(f"pending_{research_id}.json", resp.text)
            except Exception as exc:
                self.log(jid, "warn", f"pending {research_id} falhou: {exc}")
            time.sleep(1.2)

        self.save_game_client(ga_id, client)
        self.log(jid, "info", f"Dump completo: {len(captured)} arquivos em {DUMP_DIR}")
        return RunnerResult(success=True, data={"status": "dump_complete", "files": captured, "city_id": city_id})
