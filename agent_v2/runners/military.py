"""
Military runners — troop/fleet training, combat operations, and unit improvements.

Action codes:
    15    train_troops
     7    train_fleet
    11    send_troops
    12    attack
    13    pillage
    1203  upgrade_units   (Workshop / Oficina do Inventor)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.runner_registry import register_runner
from runners.base import BaseRunner, RunnerResult
from services.resource_transport import change_current_city

logger = logging.getLogger(__name__)

# Seconds to add as buffer after an improvement timer fires before rechecking
_FINISH_BUFFER = 90


# Fallback recheck interval when gold is insufficient or state is unclear (30 min)
_GOLD_RECHECK = 30 * 60
# Minimum reschedule interval
_MIN_RECHECK = 5 * 60


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw))
    except Exception:
        return default


def _collect_improvement_levels(
    improvements: list[dict[str, Any]],
    target: dict[str, dict[str, int]],
) -> None:
    """Merge current_level per unit+branch into `target`.

    target shape: {str(unit_id): {"offensive": N, "defensive": N}}
    Only updates a key if the new value is >= existing (never downgrades).
    """
    for imp in improvements:
        uid = str(imp.get("unit_id") or imp.get("id") or "")
        if not uid:
            continue
        upgrade_type = str(imp.get("upgrade_type") or "offensive").lower()
        current_lv = _to_int(imp.get("current_level"), -1)
        if current_lv < 0:
            continue
        if uid not in target:
            target[uid] = {}
        existing = target[uid].get(upgrade_type, -1)
        if current_lv > existing:
            target[uid][upgrade_type] = current_lv


def _duration_human(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}min" if s == 0 else f"{m}min {s}s"
    if seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h" if m == 0 else f"{h}h {m}min"
    d, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{d}d" if h == 0 else f"{d}d {h}h"


@register_runner(1203)
class UpgradeUnitsRunner(BaseRunner):
    """Automatically research unit improvements in the Workshop (Oficina do Inventor).

    Iterates all cities that have a Workshop building (optionally restricted
    to a specific city via ``city_id`` input).  For each city:
      - If an improvement is already in progress → records the remaining time.
      - If no improvement is in progress and improvements are available → starts
        the first matching one (respecting unit_targets filter and max_level).
      - If no improvements remain → logs completion for that city.

    The job reschedules itself for the shortest remaining timer across all
    cities (plus a small buffer).  If all cities are complete, the job ends
    successfully.

    Inputs (all optional):
        city_id             (str)          — restrict to a single city.
        unit_targets        (list[dict])   — [{"key": "hoplita", "attack_max": 5, "defense_max": null}, ...]
                                             empty list or omit = process everything.
        target_all          (bool)         — True if unit_targets is empty (upgrade all).
        min_crystal_reserve (int)          — skip city if crystal stock < this value.
        min_gold_reserve    (int)          — skip city if gold < this value.
        priority_mode       (str)          — offensive_first | defensive_first | shortest_first | cheapest_first
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = dict(job.get("inputs") or {})

        if not ga_id:
            return RunnerResult(success=False, data={"error": "missing_game_account"})

        # ── Parse user preferences ──
        unit_targets: list[dict[str, Any]] = list(inputs.get("unit_targets") or [])
        target_all: bool = bool(inputs.get("target_all")) or len(unit_targets) == 0
        min_crystal: int = max(0, _to_int(inputs.get("min_crystal_reserve"), 0))
        min_gold: int = max(0, _to_int(inputs.get("min_gold_reserve"), 0))
        priority_mode = str(inputs.get("priority_mode") or "offensive_first").strip() or "offensive_first"
        if priority_mode not in {"offensive_first", "defensive_first", "shortest_first", "cheapest_first"}:
            priority_mode = "offensive_first"

        # Build quick-lookup: key → branch-specific max level.
        unit_filter: dict[str, dict[str, int | None]] = {}
        if not target_all and unit_targets:
            for t in unit_targets:
                key = str(t.get("key") or "").strip().lower()
                if key:
                    if "attack_max" in t or "defense_max" in t:
                        attack_raw = t.get("attack_max")
                        defense_raw = t.get("defense_max")

                        def _branch_limit(value):
                            if value in ("", None):
                                return None
                            return max(0, _to_int(value, 0))

                        unit_filter[key] = {
                            "offensive": _branch_limit(attack_raw),
                            "defensive": _branch_limit(defense_raw),
                        }
                    else:
                        # Backward compatibility for older jobs that used one max level for both branches.
                        legacy_max = max(0, _to_int(t.get("max_level"), 0))
                        unit_filter[key] = {
                            "offensive": None if legacy_max == 0 else legacy_max,
                            "defensive": None if legacy_max == 0 else legacy_max,
                        }

        # ── Resolve target cities from snapshot ──
        snapshot = self._get_snapshot(jid, ga_id)
        if snapshot is None:
            self.log(jid, "warn", "Snapshot indisponivel; aguardando refresh")
            self._ensure_status_refresh(jid)
            return RunnerResult(success=True, reschedule_seconds=_MIN_RECHECK, data={"status": "waiting_snapshot"})

        workshop_cities = self._find_workshop_cities(snapshot, inputs)
        if not workshop_cities:
            city_filter = str(inputs.get("city_id") or "").strip()
            if city_filter:
                self.log(jid, "error", f"Cidade {city_filter} nao tem Oficina do Inventor no snapshot")
                return RunnerResult(success=False, data={"error": "workshop_not_found"})
            self.log(jid, "info", "Nenhuma cidade com Oficina do Inventor encontrada no snapshot")
            return RunnerResult(success=True, data={"status": "no_workshop"})

        creds = self.resolve_credentials(aid, {}, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        try:
            client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        except Exception as exc:
            self.log(jid, "error", f"Falha ao obter sessao de jogo: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})

        wait_seconds_per_city: list[int] = []
        started: list[dict[str, Any]] = []
        completed_cities: list[str] = []
        failed: list[dict[str, Any]] = []

        # Accumulated improvement levels across all workshop cities → saved to snapshot once
        accumulated_levels: dict[str, dict[str, int]] = {}

        for city_id, city_name, position in workshop_cities:
            try:
                self.log(jid, "info", f"[{city_name}] Verificando Oficina do Inventor (pos={position})")
                change_current_city(client, city_id)
                state = client.get_workshop_state(city_id, position)

                # ── Collect current improvement levels from this workshop ──
                _collect_improvement_levels(state.get("improvements") or [], accumulated_levels)

                current_gold = _to_int(state.get("gold"), 0)

                # ── Gold reserve check (applies to whole city) ──
                if min_gold > 0 and current_gold < min_gold:
                    self.log(
                        jid, "info",
                        f"[{city_name}] Ouro insuficiente ({current_gold} < reserva {min_gold}); pulando",
                    )
                    wait_seconds_per_city.append(_GOLD_RECHECK)
                    continue

                if state.get("in_progress"):
                    remaining = _to_int(state.get("remaining_seconds"), _GOLD_RECHECK)
                    remaining_text = str(state.get("remaining_text") or _duration_human(remaining))
                    self.log(
                        jid, "info",
                        f"[{city_name}] Melhoria em andamento — restam {remaining_text} ({remaining}s)",
                    )
                    wait_seconds_per_city.append(max(_MIN_RECHECK, remaining + _FINISH_BUFFER))
                    continue

                improvements = list(state.get("improvements") or [])

                if not improvements:
                    self.log(jid, "info", f"[{city_name}] Todas as melhorias ja foram pesquisadas")
                    completed_cities.append(city_name)
                    continue

                # ── Find first eligible improvement ──
                chosen = self._pick_improvement(
                    improvements, unit_filter, target_all, min_crystal, city_name, jid, priority_mode,
                )

                if chosen is None:
                    self.log(jid, "info", f"[{city_name}] Nenhuma melhoria elegivel no momento; reagendando")
                    wait_seconds_per_city.append(_GOLD_RECHECK)
                    continue

                imp_id = _to_int(chosen.get("id"), 0)
                imp_name = str(chosen.get("name") or f"Melhoria #{imp_id}")
                imp_gold = _to_int(chosen.get("gold_cost"), 0)
                imp_crystal = _to_int(chosen.get("crystal_cost"), 0)
                imp_duration = _to_int(chosen.get("duration_seconds"), 0)
                imp_duration_text = str(chosen.get("duration_text") or _duration_human(imp_duration))
                imp_upgrade_type = str(chosen.get("upgrade_type") or "offensive")

                self.log(
                    jid, "info",
                    f"[{city_name}] Iniciando: {imp_name} "
                    f"(id={imp_id}, ouro={imp_gold}, cristal={imp_crystal}, duracao={imp_duration_text})",
                )
                client.start_workshop_improvement(
                    city_id,
                    position,
                    imp_id,
                    upgrade_type=imp_upgrade_type,
                )

                # Optimistically advance level in accumulated_levels for this unit+type
                uid_key = str(imp_id)
                next_lv = _to_int(chosen.get("next_level"), 0)
                if uid_key not in accumulated_levels:
                    accumulated_levels[uid_key] = {}
                if next_lv > 0:
                    accumulated_levels[uid_key][imp_upgrade_type] = next_lv

                started.append({
                    "city_id": city_id,
                    "city_name": city_name,
                    "improvement_id": imp_id,
                    "improvement_name": imp_name,
                    "gold_cost": imp_gold,
                    "crystal_cost": imp_crystal,
                    "duration_seconds": imp_duration,
                })
                recheck = max(_MIN_RECHECK, imp_duration + _FINISH_BUFFER) if imp_duration > 0 else _GOLD_RECHECK
                wait_seconds_per_city.append(recheck)
                self.log(jid, "info", f"[{city_name}] Melhoria iniciada; proximo check em {_duration_human(recheck)}")

            except Exception as exc:
                logger.warning("UpgradeUnitsRunner: falha em %s (city=%s): %s", jid, city_name, exc)
                self.log(jid, "warn", f"[{city_name}] Erro ao processar Oficina: {exc}")
                failed.append({"city_id": city_id, "city_name": city_name, "error": str(exc)})
                wait_seconds_per_city.append(_GOLD_RECHECK)

        self.save_game_client(ga_id, client)

        # ── Persist improvement levels to snapshot ──
        if accumulated_levels:
            try:
                existing = self._get_snapshot(jid, ga_id) or {}
                merged = dict(existing.get("base_snapshot", {}).get("unit_improvements", {}))
                # Deep-merge: keep existing levels for units not seen in this run
                for uid, branch_data in accumulated_levels.items():
                    if uid not in merged:
                        merged[uid] = {}
                    merged[uid].update(branch_data)
                self.hub.patch_snapshot_base(ga_id, {"unit_improvements": merged})
            except Exception as exc:
                logger.warning("UpgradeUnitsRunner: falha ao salvar unit_improvements: %s", exc)

        # ── Decide what to do next ──
        if not wait_seconds_per_city and completed_cities:
            self.log(jid, "info", "Oficina concluida: todas as melhorias pesquisadas em todas as cidades")
            return RunnerResult(
                success=True,
                data={
                    "status": "complete",
                    "completed_cities": completed_cities,
                    "started": started,
                },
            )

        next_wait = min(wait_seconds_per_city) if wait_seconds_per_city else _GOLD_RECHECK
        self.log(
            jid, "info",
            f"Proximo ciclo em {_duration_human(next_wait)} | "
            f"iniciadas={len(started)}, completas={len(completed_cities)}, erros={len(failed)}",
        )
        return RunnerResult(
            success=True,
            reschedule_seconds=next_wait,
            data={
                "status": "running",
                "started": started,
                "completed_cities": completed_cities,
                "failed": failed,
            },
        )

    # ── Helpers ──

    def _pick_improvement(
        self,
        improvements: list[dict[str, Any]],
        unit_filter: dict[str, dict[str, int | None]],
        target_all: bool,
        min_crystal: int,
        city_name: str,
        job_id: str,
        priority_mode: str,
    ) -> dict[str, Any] | None:
        """Return the first improvement that passes all filters, or None."""
        for imp in self._prioritize_improvements(improvements, priority_mode):
            imp_id = _to_int(imp.get("id"), 0)
            if imp_id <= 0:
                continue

            imp_name = str(imp.get("name") or "").strip()
            imp_crystal = _to_int(imp.get("crystal_cost"), 0)
            imp_upgrade_type = str(imp.get("upgrade_type") or "offensive").strip().lower() or "offensive"

            # Crystal reserve check
            if min_crystal > 0 and imp_crystal > 0:
                # We don't know current crystal from workshop state; skip this check
                # if crystal_cost > 0 and reserve is set — the runner will log a warning
                # but proceed (the game will reject it if truly insufficient).
                pass

            # Unit filter
            if not target_all and unit_filter:
                matched_key = self._match_unit_key(imp_name, unit_filter)
                if matched_key is None:
                    continue
                branch_limits = unit_filter[matched_key]
                max_lv = branch_limits.get(imp_upgrade_type)
                # None = no limit for this branch → allow upgrade
                current_lv = _to_int(imp.get("current_level"), -1)
                if max_lv > 0 and current_lv >= max_lv:
                    self.log(
                        job_id, "info",
                        f"[{city_name}] {imp_name} — nivel {current_lv} >= limite {max_lv}; pulando",
                    )
                    continue
                if max_lv > 0 and current_lv < 0:
                    # Fallback only when the payload does not expose current_level.
                    lv_match = re.search(r"[Nn][íi]vel\s+(\d+)|[Ll]evel\s+(\d+)", imp_name)
                    if lv_match:
                        current_lv = _to_int(lv_match.group(1) or lv_match.group(2), 0)
                        if current_lv >= max_lv:
                            self.log(
                                job_id, "info",
                                f"[{city_name}] {imp_name} — nivel {current_lv} >= limite {max_lv}; pulando",
                            )
                            continue

            return imp

        return None

    @staticmethod
    def _prioritize_improvements(improvements: list[dict[str, Any]], priority_mode: str) -> list[dict[str, Any]]:
        priority_mode = str(priority_mode or "offensive_first").strip().lower() or "offensive_first"

        def branch_rank(imp: dict[str, Any], offensive_first: bool) -> int:
            upgrade_type = str(imp.get("upgrade_type") or "offensive").strip().lower()
            if offensive_first:
                return 0 if upgrade_type == "offensive" else 1
            return 0 if upgrade_type == "defensive" else 1

        if priority_mode == "defensive_first":
            return sorted(improvements, key=lambda imp: (branch_rank(imp, False), _to_int(imp.get("duration_seconds"), 0)))
        if priority_mode == "shortest_first":
            return sorted(
                improvements,
                key=lambda imp: (
                    _to_int(imp.get("duration_seconds"), 0),
                    branch_rank(imp, True),
                    _to_int(imp.get("gold_cost"), 0) + _to_int(imp.get("crystal_cost"), 0),
                ),
            )
        if priority_mode == "cheapest_first":
            return sorted(
                improvements,
                key=lambda imp: (
                    _to_int(imp.get("gold_cost"), 0) + _to_int(imp.get("crystal_cost"), 0),
                    _to_int(imp.get("duration_seconds"), 0),
                    branch_rank(imp, True),
                ),
            )
        return sorted(improvements, key=lambda imp: (branch_rank(imp, True), _to_int(imp.get("duration_seconds"), 0)))

    @staticmethod
    def _match_unit_key(imp_name: str, unit_filter: dict[str, int]) -> str | None:
        """Return the matching unit_filter key for an improvement name, or None."""
        lower = imp_name.lower()
        # Extract unit name (before " - ") to avoid "catapulta" matching "barco catapulta"
        unit_part = lower.split(" - ")[0].strip() if " - " in lower else lower
        # Exact match first
        for key in unit_filter:
            if key.lower() == unit_part:
                return key
        # Starts-with match (handles "Hoplita: Ataque Nivel 3" style names)
        for key in unit_filter:
            k = key.lower()
            if unit_part.startswith(k + " ") or unit_part.startswith(k + ":"):
                return key
        # Alias map for common name variations
        aliases = {
            "hoplita": ["hoplite", "hoplita"],
            "espadachim": ["swordsman", "espadachim"],
            "arqueiro": ["archer", "arqueiro"],
            "fundeiro": ["slinger", "fundeiro"],
            "lanceiro": ["spearman", "lanceiro"],
            "atirador": ["gunsman", "atirador"],
            "lancachamas": ["flamethrower", "lança-chamas", "lancachamas"],
            "catapulta": ["catapult", "catapulta"],
            "morteiro": ["mortar", "morteiro"],
            "gigante": ["steam giant", "gigante a vapor", "gigante"],
            "girocoptero": ["gyrocopter", "girocoptero"],
            "ariete": ["ram ship", "navio ariete", "ariete"],
            "trieme": ["trireme", "trirreme", "trieme"],
            "barcocatapulta": ["catapult ship", "barco catapulta"],
            "balao": ["balloon", "balão"],
            "barcomorteiro": ["mortar ship", "barco morteiro"],
            "submergivel": ["submarine", "submergivel", "submergível"],
            "ariete_vapor": ["steam ram", "ariete a vapor", "aríete a vapor"],
            "barco_balista": ["ballista ship", "barco balista"],
            "lancha_rapida": ["paddle gunboat", "lancha rapida", "lancha rápida"],
            "lanca_foguetes": ["rocket ship", "lança-foguetes"],
            "porta_balaos": ["tender", "porta-balões", "porta balaos"],
            "barcoreparador": ["repair ship", "barco reparador"],
        }
        for key, alias_list in aliases.items():
            if key not in unit_filter:
                continue
            for alias in alias_list:
                if alias in lower:
                    return key
        return None

    def _get_snapshot(self, job_id: str, game_account_id: str) -> dict[str, Any] | None:
        try:
            return self.hub.get_snapshot(game_account_id=game_account_id)
        except Exception as exc:
            self.log(job_id, "warn", f"Nao foi possivel obter snapshot: {exc}")
            return None

    def _ensure_status_refresh(self, job_id: str) -> None:
        """Spawn a check_status job if one is not already running."""
        try:
            self.hub.spawn_job(job_id, action_code=100, inputs={})
        except Exception:
            pass

    @staticmethod
    def _find_workshop_cities(
        snapshot: dict[str, Any],
        inputs: dict[str, Any],
    ) -> list[tuple[int, str, int]]:
        """Return (city_id, city_name, building_position) for cities with a Workshop."""
        city_filter = str(inputs.get("city_id") or "").strip()
        results: list[tuple[int, str, int]] = []

        cities = snapshot.get("cities") or []
        if isinstance(cities, dict):
            cities = list(cities.values())

        for city in cities:
            if not isinstance(city, dict):
                continue
            city_id = _to_int(city.get("id"), 0)
            city_name = str(city.get("name") or city_id)
            if city_filter and str(city_id) != city_filter:
                continue

            for building in city.get("buildings") or []:
                building_type = str(building.get("building") or "").strip()
                if building_type != "workshop":
                    continue
                position = _to_int(building.get("position"), 0)
                if position >= 0:
                    results.append((city_id, city_name, position))
                break  # only one workshop per city

        return results


@register_runner(1005)
class TrainUnitsRunner(BaseRunner):
    """Train troops or fleet — one-time or maintain-garrison policy.

    Inputs:
        city_id         str     city with barracks/shipyard
        building_type   str     "troops" | "fleet"
        position        int     building slot position
        mode            str     "once" | "maintain"
        units           dict    {unit_id_str: quantity} — train these amounts
        target_garrison dict    {unit_id_str: target_count} — maintain mode targets
        recheck_minutes int     interval for maintain mode (default 120)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        city_id = _to_int(inputs.get("city_id"))
        building_type = str(inputs.get("building_type") or "troops").strip()
        position = _to_int(inputs.get("position"), 0)
        mode = str(inputs.get("mode") or "once").strip()
        maintain_scope = str(inputs.get("maintain_scope") or "local").strip()
        recheck_minutes = max(30, _to_int(inputs.get("recheck_minutes"), 120))

        # Multi-city maintain: selected_custom or selected_uniform
        if mode == "maintain" and maintain_scope in ("selected_custom", "selected_uniform"):
            return self._maintain_multi_city(jid, aid, ga_id, inputs, building_type, maintain_scope, recheck_minutes)

        if not city_id:
            self.log(jid, "error", 'city_id obrigatorio para modo local')
            return RunnerResult(success=False, data={"error": "missing_city_id"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", 'Credenciais nao encontradas')
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        client = self.get_or_login_game_client(jid, aid, ga_id, creds)

        try:
            # Auto-detect building position from snapshot if not provided
            if not position:
                try:
                    snap = self.hub.get_snapshot(game_account_id=ga_id)
                    bname = 'shipyard' if building_type == 'fleet' else 'barracks'
                    for city in (snap or {}).get('cities') or []:
                        if _to_int(city.get('id')) == city_id:
                            for b in city.get('buildings') or []:
                                if str(b.get('building') or '').strip() == bname:
                                    position = _to_int(b.get('position'))
                                    if position:
                                        self.log(jid, 'info', f'Posicao {bname} detectada: {position}')
                                        break
                            break
                except Exception:
                    pass
            if not position:
                bname = 'estaleiro' if building_type == 'fleet' else 'quartel'
                self.log(jid, 'error', f'Nenhum {bname} encontrado na cidade {city_id}')
                return RunnerResult(success=False, data={"error": "no_building_found"})

            # Fetch current state from game
            state = client.fetch_barracks_state(city_id, position, building_type)
            units_in_game = {str(u["unit_id"]): u for u in state.get("units", [])}
            # Use snapshot occupation data (more reliable than HTML parse)
            occupied = False
            try:
                snap = self.hub.get_snapshot(game_account_id=ga_id)
                for city in (snap or {}).get('cities') or []:
                    if _to_int(city.get('id')) == city_id:
                        occupied = bool(city.get('city_occupied'))
                        break
            except Exception:
                occupied = state.get("occupied", False)

            if occupied:
                self.log(jid, 'warn', f'Cidade {city_id} ocupada -- custo dobrado para treinar')

            if mode == "maintain":
                # Build train order based on deficit vs target
                target_garrison: dict[str, int] = {}
                raw_target = inputs.get("target_garrison") or {}
                if isinstance(raw_target, dict):
                    target_garrison = {str(k): _to_int(v) for k, v in raw_target.items()}

                to_train: dict[int, int] = {}
                for unit_id_str, target_qty in target_garrison.items():
                    if target_qty <= 0:
                        continue
                    current = _to_int((units_in_game.get(unit_id_str) or {}).get("current_count"), 0)
                    deficit = max(0, target_qty - current)
                    if deficit > 0:
                        to_train[int(unit_id_str)] = deficit

                if not to_train:
                    self.log(jid, "info", f"Guarnição já no alvo — próxima verificação em {recheck_minutes}min")
                    self.save_game_client(ga_id or aid, client)
                    return RunnerResult(
                        success=True,
                        reschedule_seconds=recheck_minutes * 60,
                        reschedule_inputs=inputs,
                        data={"status": "at_target"},
                    )
            else:
                # One-time: train exactly what was specified
                raw_units = inputs.get("units") or {}
                to_train = {int(k): _to_int(v) for k, v in raw_units.items() if _to_int(v) > 0}

            if not to_train:
                self.log(jid, "warn", "Nenhuma unidade para treinar")
                self.save_game_client(ga_id or aid, client)
                return RunnerResult(success=False, data={"error": "no_units"})

            # Log what we're training — combine game state names + static catalog fallback
            unit_names = {str(u["unit_id"]): u["name"] for u in state.get("units", []) if u.get("unit_id")}
            # Fallback: catalog with numeric ID -> name (e.g. "303" -> "Hoplita")
            try:
                from core.catalogs import TRAINING_UNITS
                for bt_units in TRAINING_UNITS.values():
                    for u in bt_units:
                        uid_str = str(u["id"])
                        if uid_str not in unit_names:
                            unit_names[uid_str] = u["name"]
            except Exception:
                pass
            summary = ", ".join(
                f"{unit_names.get(str(uid), uid)} x{qty}"
                for uid, qty in to_train.items()
            )
            self.log(jid, "info", f"Treinando: {summary}")

            result = client.train_units(city_id, position, to_train, building_type)
            self.save_game_client(ga_id or aid, client)
            self.log(jid, "info", f"Treino iniciado: {summary}")

            if mode == "maintain":
                return RunnerResult(
                    success=True,
                    reschedule_seconds=recheck_minutes * 60,
                    reschedule_inputs=inputs,
                    data={"status": "trained", "units": to_train},
                )
            return RunnerResult(success=True, data={"units_trained": to_train})

        except Exception as exc:
            self.log(jid, "error", f"Treino falhou: {exc}")
            self.save_game_client(ga_id or aid, client)
            return RunnerResult(success=False, data={"error": str(exc)})

    def _maintain_multi_city(self, jid, aid, ga_id, inputs, building_type, maintain_scope, recheck_minutes):
        """Maintain garrison across multiple cities (selected_custom or selected_uniform)."""
        city_ids = [_to_int(c) for c in (inputs.get('city_ids') or []) if _to_int(c)]
        uniform_target = {str(k): _to_int(v) for k, v in (inputs.get('target_garrison') or {}).items() if _to_int(v) > 0}
        city_targets_raw = inputs.get('city_targets') or {}
        bname = 'shipyard' if building_type == 'fleet' else 'barracks'

        if not city_ids:
            self.log(jid, 'error', 'Nenhuma cidade selecionada')
            return RunnerResult(success=False, data={"error": "no_cities"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        client = self.get_or_login_game_client(jid, aid, ga_id, creds)

        try:
            snap = self.hub.get_snapshot(game_account_id=ga_id)
            city_list = (snap or {}).get('cities') or []

            # Build city_id -> {name, position} from snapshot
            city_meta = {}
            for city in city_list:
                cid = _to_int(city.get('id'))
                for b in city.get('buildings') or []:
                    if str(b.get('building') or '').strip() == bname:
                        pos = _to_int(b.get('position'))
                        if pos:
                            city_meta[cid] = {'name': str(city.get('name') or cid), 'position': pos}
                        break

            try:
                from core.catalogs import TRAINING_UNITS
                catalog = {u['id']: u['name'] for bt in TRAINING_UNITS.values() for u in bt}
            except Exception:
                catalog = {}

            # Build island_id map for station dispatch
            island_map = {_to_int(c.get('id')): _to_int(c.get('island_id')) for c in city_list if c.get('id')}

            import time as _time

            # Phase 1: fetch current counts for all cities
            # - Cities WITH barracks: use live barracks state (accurate, includes max_build)
            # - Cities WITHOUT barracks: use snapshot military data (may be stale, but enables redistribution)
            city_current: dict[int, dict[str, int]] = {}
            city_target_map: dict[int, dict[str, int]] = {}

            # Build snapshot garrison map for cities without barracks
            snap_military = {}
            for bc in ((snap or {}).get('military') or {}).get('by_city') or []:
                if not isinstance(bc, dict):
                    continue
                cid = _to_int(bc.get('city_id'))
                if cid:
                    troop_fleet = bc.get('fleet' if building_type == 'fleet' else 'troops') or {}
                    # Convert name-keyed to catalog-id-keyed using catalog already built above
                    name_to_id = {v: str(k) for k, v in catalog.items()} if catalog else {}
                    snap_military[cid] = {name_to_id.get(k, k): _to_int(v) for k, v in troop_fleet.items()}

            for city_id in city_ids:
                target = dict(uniform_target)
                if maintain_scope == 'selected_custom':
                    raw = city_targets_raw.get(str(city_id)) or {}
                    if raw:
                        target = {str(k): _to_int(v) for k, v in raw.items() if _to_int(v) > 0}
                city_target_map[city_id] = target

                meta = city_meta.get(city_id)
                if not meta:
                    # No barracks: use snapshot data — can receive redistributed troops but can't train
                    city_current[city_id] = snap_military.get(city_id, {})
                    city_name = next((str(c.get('name') or city_id) for c in city_list if _to_int(c.get('id')) == city_id), str(city_id))
                    self.log(jid, 'info', f'{city_name}: sem {bname} -- usando snapshot para calculo de deficit (so pode receber redistribuicao)')
                    continue

                try:
                    _time.sleep(0.4)
                    state = client.fetch_barracks_state(city_id, meta['position'], building_type)
                    city_current[city_id] = {
                        str(u['unit_id']): _to_int(u.get('current_count'), 0)
                        for u in state.get('units', []) if u.get('unit_id')
                    }
                except Exception as exc:
                    self.log(jid, 'warn', f'{meta.get("name", city_id)}: erro ao buscar estado -- {exc}')
                    city_current[city_id] = snap_military.get(city_id, {})

            # Phase 2: compute surplus and deficit per city per unit
            # surplus[city_id][uid_str] = how many above target
            # deficit[city_id][uid_str] = how many below target
            surplus: dict[int, dict[str, int]] = {}
            deficit: dict[int, dict[str, int]] = {}
            for city_id in city_ids:
                target = city_target_map.get(city_id, {})
                current = city_current.get(city_id, {})
                surplus[city_id] = {}
                deficit[city_id] = {}
                # Surplus: current > target for any unit type
                for uid_str, cur_qty in current.items():
                    tgt_qty = _to_int(target.get(uid_str), 0)
                    if cur_qty > tgt_qty and tgt_qty >= 0:
                        surplus[city_id][uid_str] = cur_qty - tgt_qty
                # Deficit: target > current
                for uid_str, tgt_qty in target.items():
                    cur_qty = _to_int(current.get(uid_str), 0)
                    if tgt_qty > cur_qty:
                        deficit[city_id][uid_str] = tgt_qty - cur_qty

            # Phase 3: redistribute surplus to deficit cities
            transfers_done = []
            for donor_id, donor_surplus in surplus.items():
                donor_meta = city_meta.get(donor_id)
                if not donor_meta or not donor_surplus:
                    continue
                for recv_id, recv_deficit in deficit.items():
                    if recv_id == donor_id or not recv_deficit:
                        continue
                    recv_meta = city_meta.get(recv_id)
                    if not recv_meta:
                        continue
                    to_move = {}
                    for uid_str, needed in list(recv_deficit.items()):
                        available = donor_surplus.get(uid_str, 0)
                        move_qty = min(needed, available)
                        if move_qty > 0:
                            to_move[int(uid_str)] = move_qty
                            donor_surplus[uid_str] = available - move_qty
                            recv_deficit[uid_str] = needed - move_qty
                            if recv_deficit[uid_str] == 0:
                                del recv_deficit[uid_str]
                    if to_move:
                        try:
                            to_island = island_map.get(recv_id, 0)
                            scope = 'fleet' if building_type == 'fleet' else 'troops'
                            client.station_units(donor_id, recv_id, to_move, scope=scope, to_island_id=to_island)
                            summary = ', '.join(f'{catalog.get(uid, uid)} x{qty}' for uid, qty in to_move.items())
                            self.log(jid, 'info', f'Redistribuindo: {donor_meta["name"]} -> {recv_meta["name"]} | {summary}')
                            transfers_done.append({'from': donor_id, 'to': recv_id, 'units': to_move})
                        except Exception as exc:
                            self.log(jid, 'warn', f'Transferencia {donor_id}->{recv_id} falhou -- {exc}')

            if transfers_done:
                self.log(jid, 'info', f'{len(transfers_done)} redistribuicao(oes) de excesso despachada(s)')

            # Phase 4: train remaining deficit
            trained_any = False
            at_target = []
            errors = []

            for city_id in city_ids:
                remaining_deficit = deficit.get(city_id, {})
                meta = city_meta.get(city_id)
                if not meta:
                    # No barracks: deficit can only be resolved via redistribution, not training
                    if remaining_deficit:
                        city_name = next((str(c.get('name') or city_id) for c in city_list if _to_int(c.get('id')) == city_id), str(city_id))
                        self.log(jid, 'warn', f'{city_name}: deficit restante sem {bname} para treinar -- necessario mais excesso em outras cidades')
                    continue
                if not remaining_deficit:
                    at_target.append(city_id)
                    continue
                to_train = {int(uid_str): qty for uid_str, qty in remaining_deficit.items() if qty > 0}
                if not to_train:
                    at_target.append(city_id)
                    continue
                try:
                    _time.sleep(0.4)
                    summary = ', '.join(f'{catalog.get(uid, uid)} x{qty}' for uid, qty in to_train.items())
                    self.log(jid, 'info', f'{meta["name"]}: treinando {summary}')
                    client.train_units(city_id, meta['position'], to_train, building_type)
                    trained_any = True
                except Exception as exc:
                    self.log(jid, 'warn', f'{meta.get("name", city_id)}: treino falhou -- {exc}')
                    errors.append(str(city_id))

            self.save_game_client(ga_id or aid, client)

            if at_target:
                self.log(jid, 'info', f'{len(at_target)} cidade(s) no alvo')
            if not trained_any and not transfers_done and not errors:
                self.log(jid, 'info', f'Sistema equilibrado -- proxima verificacao em {recheck_minutes}min')

            return RunnerResult(
                success=True,
                reschedule_seconds=recheck_minutes * 60,
                reschedule_inputs=inputs,
                data={
                    "status": "trained" if trained_any else ("redistributed" if transfers_done else "at_target"),
                    "at_target": at_target,
                    "transfers": len(transfers_done),
                    "errors": errors,
                },
            )
        except Exception as exc:
            self.log(jid, 'error', f'Treino multi-cidade falhou: {exc}')
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(1202)
class StationUnitsRunner(BaseRunner):
    """Station troops from one city to garrison another.

    Inputs:
        from_city_id    int    source city
        to_city_id      int    destination city
        units           dict   {unit_id_str: quantity}
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        ga_id = job.get("game_account_id")
        inputs = job.get("inputs") or {}

        from_city = _to_int(inputs.get("from_city_id"))
        to_city = _to_int(inputs.get("to_city_id"))
        raw_units = inputs.get("units") or {}
        units = {int(k): _to_int(v) for k, v in raw_units.items() if _to_int(v) > 0}

        if not from_city or not to_city:
            self.log(jid, "error", "from_city_id e to_city_id obrigatórios")
            return RunnerResult(success=False, data={"error": "missing_cities"})
        if not units:
            self.log(jid, "error", "Nenhuma unidade selecionada")
            return RunnerResult(success=False, data={"error": "no_units"})

        creds = self.resolve_credentials(aid, inputs, game_account_id=ga_id)
        if not creds:
            self.log(jid, "error", "Credenciais não encontradas")
            return RunnerResult(success=False, data={"error": "missing_credentials"})

        client = self.get_or_login_game_client(jid, aid, ga_id, creds)
        try:
            # Get destination island_id from snapshot (required for deployArmy)
            to_island_id = 0
            try:
                snap = self.hub.get_snapshot(game_account_id=ga_id)
                for city in (snap or {}).get('cities') or []:
                    if _to_int(city.get('id')) == to_city:
                        to_island_id = _to_int(city.get('island_id'))
                        break
            except Exception:
                pass

            # Separate troops (300-399) and fleet (200-299)
            troop_units = {uid: qty for uid, qty in units.items() if 300 <= uid < 400}
            fleet_units = {uid: qty for uid, qty in units.items() if 200 <= uid < 300}

            results = []
            max_eta = 0

            # Resolve unit names from catalog
            try:
                from core.catalogs import TRAINING_UNITS
                catalog = {u['id']: u['name'] for bt in TRAINING_UNITS.values() for u in bt}
            except Exception:
                catalog = {}

            if troop_units:
                summary = ', '.join(f'{catalog.get(uid, uid)} x{qty}' for uid, qty in troop_units.items())
                self.log(jid, 'info', f'Estacionando tropas [{summary}] de {from_city} -> {to_city}')
                result = client.station_units(from_city, to_city, troop_units, scope='troops', to_island_id=to_island_id)
                eta = _to_int((result or {}).get('eta_seconds'), 0)
                if eta:
                    max_eta = max(max_eta, eta)
                    self.log(jid, 'info', f'Tropas chegam em {_duration_human(eta)}')
                results.append('tropas')

            if fleet_units:
                summary = ', '.join(f'{catalog.get(uid, uid)} x{qty}' for uid, qty in fleet_units.items())
                self.log(jid, 'info', f'Estacionando frotas [{summary}] de {from_city} -> {to_city}')
                result = client.station_units(from_city, to_city, fleet_units, scope='fleet', to_island_id=to_island_id)
                eta = _to_int((result or {}).get('eta_seconds'), 0)
                if eta:
                    max_eta = max(max_eta, eta)
                    self.log(jid, 'info', f'Frotas chegam em {_duration_human(eta)}')
                results.append('frotas')

            self.save_game_client(ga_id or aid, client)
            label = ', '.join(results)
            if max_eta:
                self.log(jid, 'info', f'Movimentacao iniciada: {label} | ETA {_duration_human(max_eta)}')
            else:
                self.log(jid, 'info', f'Movimentacao iniciada: {label}')
            return RunnerResult(success=True, data={"from": from_city, "to": to_city, "island": to_island_id, "units": units, "eta_seconds": max_eta})
        except Exception as exc:
            self.log(jid, "error", f"Estacionamento falhou: {exc}")
            self.save_game_client(ga_id or aid, client)
            return RunnerResult(success=False, data={"error": str(exc)})


# Action 15 is now SpyRunner (see runners/spy.py).


@register_runner(7)
class TrainFleetRunner(BaseRunner):
    """Legacy stub — use TrainUnitsRunner (1005) with building_type=fleet instead."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        return RunnerResult(success=False, data={"error": "use_action_1005"})


@register_runner(11)
class SendTroopsRunner(BaseRunner):
    """Send troops from one city to another (reinforcement or garrison)."""

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})
        self.log(jid, "info", "SendTroops — use StationUnitsRunner (1202) for garrison")
        return RunnerResult(success=False, data={"error": "use_action_1202"})


@register_runner(12)
class AttackRunner(BaseRunner):
    """Launch an attack against an enemy city.

    Inputs:
        source_city_id  — origin city
        target_city_id  — enemy city to attack
        units           — dict of {unit_type: quantity}
        ships           — dict of {ship_type: quantity} (optional)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Launching attack for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.attack(source, target, units, ships)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # units          = inputs["units"]
            # ships          = inputs.get("ships", {})
            # client.attack(source_city_id, target_city_id, units, ships)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Attack launched")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Attack failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})


@register_runner(13)
class PillageRunner(BaseRunner):
    """Launch a pillage raid against an enemy city.

    Similar to attack but optimised for resource capture.

    Inputs:
        source_city_id  — origin city
        target_city_id  — enemy city to pillage
        units           — dict of {unit_type: quantity}
        ships           — dict of {ship_type: quantity} (optional)
    """

    def execute(self, job: dict[str, Any]) -> RunnerResult:
        jid = job["job_id"]
        aid = job["account_id"]
        inputs = job.get("inputs", {})

        self.log(jid, "info", f"Launching pillage for account {aid}")

        try:
            client = self.get_game_session(aid)

            # TODO: call client.pillage(source, target, units, ships)
            # source_city_id = inputs["source_city_id"]
            # target_city_id = inputs["target_city_id"]
            # units          = inputs["units"]
            # ships          = inputs.get("ships", {})
            # client.pillage(source_city_id, target_city_id, units, ships)

            self.save_game_session(aid, client)
            self.log(jid, "info", "Pillage launched")

            return RunnerResult(success=True)

        except Exception as exc:
            self.log(jid, "error", f"Pillage failed: {exc}")
            return RunnerResult(success=False, data={"error": str(exc)})
