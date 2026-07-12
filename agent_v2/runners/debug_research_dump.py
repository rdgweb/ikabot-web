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

        # 2. Views candidatas da arvore completa de pesquisas
        research_ids: list[str] = []
        for view in ("researchOverview", "researchDetail"):
            try:
                resp = client._request(
                    "POST",
                    client._server_url,
                    data={
                        "view": view,
                        "cityId": city_id,
                        "backgroundView": "city",
                        "currentCityId": city_id,
                        "templateView": view,
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=GAME_AJAX_HEADERS,
                )
                _save(f"{view}.txt", resp.text)
                if view == "researchOverview":
                    research_ids = list(dict.fromkeys(re.findall(r"researchId[=\":]+(\d+)", resp.text)))
                    self.log(jid, "info", f"researchIds encontrados na overview: {research_ids[:40]}")
            except Exception as exc:
                self.log(jid, "warn", f"{view} falhou: {exc}")
            time.sleep(1.5)

        # 3. Detalhe de algumas pesquisas (onde ficam as exigencias)
        for research_id in research_ids[:12]:
            try:
                resp = client._request(
                    "POST",
                    client._server_url,
                    data={
                        "view": "researchDetail",
                        "cityId": city_id,
                        "researchId": research_id,
                        "backgroundView": "city",
                        "currentCityId": city_id,
                        "templateView": "researchDetail",
                        "actionRequest": client._action_request,
                        "ajax": "1",
                    },
                    headers=GAME_AJAX_HEADERS,
                )
                _save(f"researchDetail_{research_id}.txt", resp.text)
            except Exception as exc:
                self.log(jid, "warn", f"researchDetail {research_id} falhou: {exc}")
            time.sleep(1.2)

        self.save_game_client(ga_id, client)
        self.log(jid, "info", f"Dump completo: {len(captured)} arquivos em {DUMP_DIR}")
        return RunnerResult(success=True, data={"status": "dump_complete", "files": captured, "city_id": city_id})
