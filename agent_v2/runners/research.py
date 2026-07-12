"""Research runner - global research queue ordered by the lowest ETA."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult

logger = logging.getLogger(__name__)

RESEARCH_BRANCH_META = {
    "seafaring": {"label": "Navegacao Maritima", "fallback_name": "Futuro da Navegacao"},
    "economy": {"label": "Economia", "fallback_name": "Futuro Economico"},
    "knowledge": {"label": "Ciencia", "fallback_name": "Futuro Cientifico"},
    "military": {"label": "Militar", "fallback_name": "Futuro Belico"},
    "mythology": {"label": "Mitologia", "fallback_name": "Maximo atingido"},
}


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _snapshot_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


@register_runner(18)
class ResearchRunner(BaseRunner):
    """Monitor selected branches globally until they reach maximum."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            self.log(jid, "error", "game_account_id ausente para pesquisa")
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        selected_types = self._selected_branches(inputs)
        if not selected_types:
            self.log(jid, "error", "Nenhum ramo selecionado para pesquisa")
            return RunnerResult(success=False, data={"error": "missing_branches"})

        fallback_minutes = max(5, _to_int(inputs.get("fallback_interval_minutes"), 60))
        ready_margin_minutes = max(0, _to_int(inputs.get("ready_margin_minutes"), 10))
        fallback_seconds = fallback_minutes * 60
        ready_margin_seconds = ready_margin_minutes * 60

        snapshot = self._get_snapshot(jid, ga_id)
        city_snapshot = self._pick_research_city(snapshot)
        if not city_snapshot:
            self.log(jid, "error", "Nenhuma cidade com academia encontrada no snapshot")
            return RunnerResult(success=False, data={"error": "academy_not_found"})
        city_id = str(city_snapshot.get("id") or "").strip()

        try:
            creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
            if not creds:
                self.log(jid, "error", "Credenciais nao encontradas para pesquisa")
                return RunnerResult(success=False, data={"error": "missing_credentials"})

            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
            state = client.get_research_state(int(city_id))
            self._persist_research_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_snapshot=city_snapshot,
                state=state,
            )

            city_name = str(state.get("city_name") or (city_snapshot or {}).get("name") or city_id).strip()
            available = [
                item for item in (state.get("branches") or [])
                if str(item.get("research_type") or "").strip().lower() in selected_types
            ]
            if not available:
                self.log(jid, "error", "Nenhum dos ramos selecionados apareceu no advisor")
                return RunnerResult(success=False, data={"error": "research_branch_not_found"})

            pending = [item for item in available if not item.get("max_reached")]
            summary_parts = []
            for branch in available:
                branch_type = str(branch.get("research_type") or "").strip().lower()
                branch_label = str(branch.get("branch_name") or RESEARCH_BRANCH_META.get(branch_type, {}).get("label") or branch_type)
                next_name = str(branch.get("next_name") or RESEARCH_BRANCH_META.get(branch_type, {}).get("fallback_name") or "-")
                eta_human = self._duration_human(_to_int(branch.get("eta_seconds"), 0))
                summary_parts.append(f"{branch_label}: {next_name or '-'} ({eta_human or '0s'})")
            self.log(jid, "info", f"Pesquisa global: cidade={city_name} | ramos={'; '.join(summary_parts)}")

            if not pending:
                self.save_game_client(ga_id, client)
                self.log(jid, "info", "Pesquisa encerrada: todos os ramos selecionados chegaram ao maximo")
                return RunnerResult(
                    success=True,
                    data={
                        "status": "max_reached",
                        "city_id": int(city_id),
                        "city_name": city_name,
                        "selected_branches": selected_types,
                    },
                )

            pending.sort(key=lambda item: (_to_int(item.get("eta_seconds"), 0), str(item.get("branch_name") or "")))

            # Memoria de bloqueios (persistida nos inputs do ciclo): pesquisas ja
            # recusadas pelo jogo (requisito pendente) sao PULADAS sem tentar de
            # novo, ate o requisito mudar ou passar o periodo de reteste.
            now_ts = int(datetime.now(timezone.utc).timestamp())
            retest_seconds = 6 * 3600
            blocked_map = dict(inputs.get("__blocked_research") or {})

            def _is_blocked(item: dict[str, Any]) -> bool:
                rtype = str(item.get("research_type") or "").strip().lower()
                entry = blocked_map.get(rtype)
                if not isinstance(entry, dict):
                    return False
                if str(entry.get("next_name") or "") != str(item.get("next_name") or ""):
                    # a pesquisa do ramo mudou -> bloqueio antigo nao vale mais
                    blocked_map.pop(rtype, None)
                    return False
                if now_ts - _to_int(entry.get("blocked_at"), 0) >= retest_seconds:
                    return False  # hora de retestar
                return True

            ready_branches = []
            for item in pending:
                if not item.get("ready"):
                    continue
                if _is_blocked(item):
                    rtype = str(item.get("research_type") or "").strip().lower()
                    entry = blocked_map.get(rtype) or {}
                    self.log(
                        jid,
                        "info",
                        (
                            f"Pesquisa pulada (bloqueio conhecido): ramo={item.get('branch_name')} | "
                            f"pesquisa={item.get('next_name') or '-'} | motivo={entry.get('reason') or 'requisito pendente'}"
                        ),
                    )
                    continue
                ready_branches.append(item)

            # blocked_map e atualizado in-place daqui em diante; a mesma referencia
            # segue nos reschedule_inputs de todos os retornos abaixo.
            inputs["__blocked_research"] = blocked_map
            if not ready_branches:
                branch = pending[0]
                research_type = str(branch.get("research_type") or "").strip().lower()
                branch_label = str(branch.get("branch_name") or RESEARCH_BRANCH_META[research_type]["label"]).strip()
                next_name = str(branch.get("next_name") or RESEARCH_BRANCH_META[research_type]["fallback_name"]).strip()
                cost_text = str(branch.get("cost_text") or branch.get("cost") or "-").strip()
                eta_seconds = _to_int(branch.get("eta_seconds"), 0)
                eta_human = self._duration_human(eta_seconds)
                delay = max(fallback_seconds, eta_seconds + ready_margin_seconds if eta_seconds > 0 else fallback_seconds)
                self.save_game_client(ga_id, client)
                self.log(
                    jid,
                    "info",
                    f"Pesquisa aguardando pontos: ramo={branch_label} | pesquisa={next_name or '-'} | custo={cost_text} | eta={eta_human or '0s'} | proximo_em={delay}s",
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=delay,
                    reschedule_inputs=inputs,
                    data={
                        "status": "waiting_points",
                        "city_id": int(city_id),
                        "city_name": city_name,
                        "branch_name": branch_label,
                        "next_name": next_name,
                        "eta_seconds": eta_seconds,
                        "selected_branches": selected_types,
                    },
                )

            # Tenta os ramos prontos em ordem de ETA. A descoberta e VERIFICADA
            # comparando o next_name do ramo antes/depois: se nao avancou, o jogo
            # recusou (tipicamente requisito de predio pendente, ex CM nivel 5) —
            # nesse caso nao marca como concluida e tenta o proximo ramo.
            blocked: list[str] = []
            after_state = state
            for branch in ready_branches:
                research_type = str(branch.get("research_type") or "").strip().lower()
                branch_label = str(branch.get("branch_name") or RESEARCH_BRANCH_META[research_type]["label"]).strip()
                next_name = str(branch.get("next_name") or RESEARCH_BRANCH_META[research_type]["fallback_name"]).strip()
                before_next = str(branch.get("next_name") or "").strip()

                # Pre-check: le a arvore do ramo e os REQUISITOS exatos da
                # proxima pesquisa (predio/pesquisa). Se ha requisito nao
                # cumprido, pula SEM gastar a tentativa de descoberta.
                try:
                    details = client.get_research_branch_details(int(city_id), research_type)
                except Exception as exc:
                    details = {}
                    self.log(jid, "warn", f"Pre-check da arvore falhou para {branch_label}: {exc} — segue com verificacao pos-descoberta.")
                missing = [
                    f"{item['name']} ({item['detail']})" if item.get("detail") else str(item["name"])
                    for item in (details.get("preconditions") or [])
                    if not item.get("fulfilled")
                ]
                if missing:
                    reason = "requisito pendente: " + ", ".join(missing)
                    blocked.append(branch_label)
                    blocked_map[research_type] = {
                        "next_name": before_next,
                        "reason": reason,
                        "blocked_at": now_ts,
                    }
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Pesquisa bloqueada por requisito: ramo={branch_label} | pesquisa={next_name or '-'} | "
                            f"{reason}. Nao sera tentada; proximos ciclos pulam (reteste em {retest_seconds // 3600}h)."
                        ),
                    )
                    continue
                if details and not details.get("button_available") and details.get("points_missing_text"):
                    self.log(
                        jid,
                        "info",
                        f"Pesquisa sem pontos suficientes: ramo={branch_label} | {details.get('points_missing_text')}",
                    )
                    continue

                discover_result = client.discover_research(int(city_id), research_type)
                after_state = client.get_research_state(int(city_id))
                after_branch = next(
                    (item for item in after_state.get("branches") or [] if item.get("research_type") == research_type),
                    None,
                ) or {}
                after_next = str(after_branch.get("next_name") or "").strip()

                advanced = bool(after_branch.get("max_reached")) or (after_next and after_next != before_next)
                if not advanced:
                    reason = self._feedback_reason(discover_result) or "requisito pendente (ex. predio exigido)"
                    blocked.append(branch_label)
                    blocked_map[research_type] = {
                        "next_name": before_next,
                        "reason": reason,
                        "blocked_at": now_ts,
                    }
                    self.log(
                        jid,
                        "warn",
                        (
                            f"Pesquisa NAO avancou: ramo={branch_label} | pesquisa={next_name or '-'} — "
                            f"motivo do jogo: {reason}. Proximos ciclos pulam essa pesquisa "
                            f"(reteste em {retest_seconds // 3600}h). Tentando proximo ramo."
                        ),
                    )
                    continue

                blocked_map.pop(research_type, None)

                self.save_game_client(ga_id, client)
                self._persist_research_state(
                    job_id=jid,
                    account_id=aid,
                    game_account_id=ga_id,
                    snapshot=snapshot,
                    city_snapshot=city_snapshot,
                    state=after_state,
                )
                self.log(
                    jid,
                    "info",
                    (
                        f"Pesquisa concluida (verificada): ramo={branch_label} | descoberta={next_name or '-'} | "
                        f"proxima={(after_next or 'maximo atingido')}"
                    ),
                )
                return RunnerResult(
                    success=True,
                    reschedule_seconds=max(60, ready_margin_seconds or 60),
                    reschedule_inputs=inputs,
                    data={
                        "status": "discovered",
                        "city_id": int(city_id),
                        "city_name": city_name,
                        "branch_name": branch_label,
                        "discovered_name": next_name,
                        "next_name": after_next,
                        "selected_branches": selected_types,
                        "blocked_branches": blocked,
                    },
                )

            # Todos os ramos prontos foram recusados pelo jogo (requisitos pendentes)
            self.save_game_client(ga_id, client)
            self._persist_research_state(
                job_id=jid,
                account_id=aid,
                game_account_id=ga_id,
                snapshot=snapshot,
                city_snapshot=city_snapshot,
                state=after_state,
            )
            self.log(
                jid,
                "warn",
                (
                    f"Nenhuma pesquisa avancou: ramos recusados={', '.join(blocked)}. "
                    f"Provavel requisito de predio pendente (confira a academia). Reagendando em {fallback_seconds}s."
                ),
            )
            return RunnerResult(
                success=True,
                reschedule_seconds=fallback_seconds,
                reschedule_inputs=inputs,
                data={
                    "status": "blocked_requirements",
                    "city_id": int(city_id),
                    "city_name": city_name,
                    "blocked_branches": blocked,
                    "selected_branches": selected_types,
                },
            )
        except Exception as exc:
            logger.exception("ResearchRunner failed for job %s", jid)
            retry_seconds = fallback_seconds
            self.log(jid, "error", f"Falha ao pesquisar: {exc} | retry_em={retry_seconds}s")
            return RunnerResult(
                success=True,
                reschedule_seconds=retry_seconds,
                reschedule_inputs=inputs,
                data={"status": "retry", "error": str(exc), "retry_seconds": retry_seconds},
            )

    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel obter snapshot para pesquisa: {exc}")
            return None

    @staticmethod
    def _feedback_reason(discover_result: Any) -> str:
        """Extrai o texto de erro do provideFeedback do jogo (ex. exigencia de predio)."""
        if not isinstance(discover_result, dict):
            return ""
        texts = []
        for entry in discover_result.get("feedbacks") or []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            # type 10 = confirmacao de sucesso; qualquer outro tipo e aviso/erro
            if text and _to_int(entry.get("type"), 0) != 10:
                texts.append(text)
        return " | ".join(texts)

    @staticmethod
    def _selected_branches(inputs: dict[str, Any]) -> list[str]:
        selected = []
        for branch_type in RESEARCH_BRANCH_META:
            if bool(inputs.get(f"branch_{branch_type}")):
                selected.append(branch_type)
        return selected

    def _pick_research_city(self, snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        cities = list((snapshot or {}).get("cities") or [])
        last_city_id = str(((snapshot or {}).get("base_snapshot") or {}).get("research_city_id") or "").strip()
        candidates = []
        for city in cities:
            academy_level = 0
            has_academy = False
            for building in city.get("buildings") or []:
                if str(building.get("building") or "") == "academy":
                    has_academy = True
                    academy_level = _to_int(building.get("level"), 0)
                    break
            if not has_academy:
                continue
            candidates.append((city, academy_level))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                0 if str(item[0].get("id") or "") == last_city_id else 1,
                -item[1],
                str(item[0].get("name") or "").lower(),
            ),
        )
        return candidates[0][0]

    def _persist_research_state(
        self,
        *,
        job_id: str,
        account_id: str,
        game_account_id: str,
        snapshot: dict[str, Any] | None,
        city_snapshot: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> None:
        try:
            base_snapshot = dict((snapshot or {}).get("base_snapshot") or {})
            cities = [dict(city) for city in ((snapshot or {}).get("cities") or [])]
            military = (snapshot or {}).get("military") or {}
            now_iso = datetime.now(timezone.utc).isoformat()
            city_id = int(state.get("city_id") or (city_snapshot or {}).get("id") or 0)
            city_name = str(state.get("city_name") or (city_snapshot or {}).get("name") or city_id)

            payload = {
                "city_id": city_id,
                "city_name": city_name,
                "island_name": str(state.get("island_name") or ""),
                "current_research_type": str(state.get("current_research_type") or ""),
                "current_research_label": str(state.get("current_research_label") or ""),
                "branches": list(state.get("branches") or []),
                "updated_at": str(state.get("updated_at") or now_iso),
            }

            base_snapshot["research_state"] = payload
            base_snapshot["research_city_id"] = city_id
            base_snapshot["research_city_name"] = city_name
            base_snapshot["research_current_type"] = payload["current_research_type"]
            base_snapshot["research_updated_at"] = payload["updated_at"]

            self.hub.update_snapshot(
                account_id,
                {
                    "base_snapshot": base_snapshot,
                    "cities": cities,
                    "military": military,
                    "source_job_id": job_id,
                },
                game_account_id=game_account_id,
            )
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel persistir estado da pesquisa no snapshot: {exc}")

    @staticmethod
    def _duration_human(seconds: int) -> str:
        seconds = max(0, int(seconds or 0))
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            minutes, rem = divmod(seconds, 60)
            return f"{minutes}min" if rem == 0 else f"{minutes}min {rem}s"
        if seconds < 86400:
            hours, rem = divmod(seconds, 3600)
            minutes = rem // 60
            return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}min"
        days, rem = divmod(seconds, 86400)
        hours = rem // 3600
        return f"{days}d" if hours == 0 else f"{days}d {hours}h"
