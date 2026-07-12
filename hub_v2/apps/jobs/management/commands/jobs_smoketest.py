from __future__ import annotations

import traceback

from django.core.management.base import BaseCommand

from apps.accounts.models import GameAccount
from apps.jobs.forms import JobCreateForm
from apps.profiles.services import _resolve_all_cities, _city_select_fields
from core.contracts import ACTION_CATALOG


class Command(BaseCommand):
    help = (
        "Smoketest de criacao de jobs: para cada action_code do catalog, "
        "instancia o JobCreateForm com POST vazio e reporta se explode (ERROR) "
        "ou passa validacao normal (OK ou VALIDATION_ERRORS). Nao cria job real."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--game-account",
            dest="ga_id",
            default="",
            help="UUID do GameAccount usado no teste. Default: primeiro ativo.",
        )
        parser.add_argument(
            "--verbose-errors",
            action="store_true",
            help="Mostra stacktrace completo dos ERROR.",
        )

    def handle(self, *args, **options):
        ga_id = str(options.get("ga_id") or "").strip()
        verbose = bool(options.get("verbose_errors"))

        ga = None
        if ga_id:
            ga = GameAccount.objects.filter(pk=ga_id).first()
        if ga is None:
            ga = GameAccount.objects.select_related("account").first()
        if ga is None:
            self.stderr.write(self.style.ERROR("Nenhum GameAccount encontrado no banco."))
            return

        self.stdout.write(f"Usando GameAccount: {ga.name} ({ga.pk})")
        self.stdout.write("")

        cities: list = []

        ok_codes: list[int] = []
        validation_codes: list[tuple[int, str, list[str]]] = []
        error_codes: list[tuple[int, str, str]] = []

        for code in sorted(ACTION_CATALOG.keys()):
            meta = ACTION_CATALOG[code]
            name = meta.get("name", f"Acao #{code}")
            try:
                form = JobCreateForm(
                    data={"action_code": str(code)},
                    action_code=code,
                    game_account=ga,
                    cities=cities,
                )
                is_valid = form.is_valid()
                if is_valid:
                    ok_codes.append(code)
                else:
                    field_errors = list(form.errors.keys())
                    validation_codes.append((code, name, field_errors))
            except Exception as exc:
                summary = f"{type(exc).__name__}: {exc}"
                error_codes.append((code, name, summary))
                if verbose:
                    self.stdout.write(self.style.ERROR(f"--- traceback ac={code} {name} ---"))
                    self.stdout.write(traceback.format_exc())

        self.stdout.write(self.style.SUCCESS(f"=== {len(ok_codes)} OK (form valido com POST vazio) ==="))
        for code in ok_codes:
            self.stdout.write(f"  ac={code} {ACTION_CATALOG[code].get('name')}")

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                f"=== {len(validation_codes)} VALIDATION_ERRORS (form roda, retorna erros de campo - normal) ==="
            )
        )
        for code, name, fields in validation_codes:
            self.stdout.write(f"  ac={code} {name} -> fields: {', '.join(fields) or '(non_field)'}")

        self.stdout.write("")
        self.stdout.write(self.style.ERROR(f"=== {len(error_codes)} ERROR (form explode - BUG) ==="))
        for code, name, summary in error_codes:
            self.stdout.write(f"  ac={code} {name} -> {summary}")

        self.stdout.write("")
        total = len(ACTION_CATALOG)
        self.stdout.write(
            f"Total: {total} action_codes | OK: {len(ok_codes)} | VALIDATION: {len(validation_codes)} | ERROR: {len(error_codes)}"
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== SIMULACAO PATH DE PRESET (mode=all) ==="))
        self.stdout.write("Verifica se preset expande cidades para a key CORRETA do catalog (bug historico: hardcoded cities).")
        self.stdout.write("")
        city_ids = _resolve_all_cities(ga) or []
        preset_ok: list[int] = []
        preset_bugs: list[tuple[int, str, str]] = []
        for code in sorted(ACTION_CATALOG.keys()):
            meta = ACTION_CATALOG[code]
            name = meta.get("name", f"Acao #{code}")
            city_fields = _city_select_fields(code)
            if not city_fields:
                continue
            multi = next((f for f in city_fields if f.get("multiple")), None)
            single = next((f for f in city_fields if not f.get("multiple")), None)
            expected_key = multi["key"] if multi else (single["key"] if single else None)
            expected_kind = "list" if multi else "singular (fanout)"
            if expected_key is None:
                continue
            self.stdout.write(f"  ac={code} {name} -> expected key='{expected_key}' ({expected_kind})")
            preset_ok.append(code)
        self.stdout.write("")
        self.stdout.write(f"Total actions com city_select: {len(preset_ok)}")
        if preset_bugs:
            self.stdout.write(self.style.ERROR(f"Bugs de preset: {len(preset_bugs)}"))
            for code, name, summary in preset_bugs:
                self.stdout.write(f"  ac={code} {name} -> {summary}")
