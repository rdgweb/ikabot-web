"""N-91: execution logs that can be read: lines with fields, levels, images and links, split by cycle."""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot

from .models import Job, JobLog, Workflow, WorkflowRun
from .services.log_format import LogContext, build_entries, format_log, human_duration, level_counts

JOB_UUID = "0b54e6c2-1111-4222-8333-444455556666"


def _fields(fmt):
    return {field["label"]: field["value"] for field in fmt["fields"]}


class LogFormatTests(TestCase):
    def setUp(self):
        self.context = LogContext({"69176": "Alexa Trops", "39272": "Hell"})
        self.now = timezone.now()

    def _format(self, message, level="info"):
        return format_log(level, message, context=self.context, moment=self.now)

    def test_durations_read_as_time(self):
        self.assertEqual(
            [human_duration(value) for value in (32, 746, 3912, 86400, 87286, 0, -5)],
            ["32s", "12min 26s", "1h 5min", "1d", "1d", "0s", "0s"],
        )
        self.assertEqual(human_duration("abc"), "abc")

    def test_key_value_line_becomes_title_and_named_fields(self):
        fmt = self._format(
            "Loop de doacao: 69176 (69176) | destino=floresta | metodo=2 | valor=25 | carry_over=0 | intervalo=1440min | jitter_max=60min"
        )
        self.assertEqual(fmt["title"], "Loop de doação: Alexa Trops")
        self.assertEqual(
            _fields(fmt),
            {"Destino": "floresta", "Método": "2", "Valor": "25", "Sobra acumulada": "0", "Intervalo": "1d", "Variação máx.": "1h"},
        )
        self.assertEqual(fmt["family"], "donation")
        self.assertTrue(fmt["image_url"].endswith("forestershouse.png"))
        self.assertIn("doacao", fmt["raw"])  # o que o agente gravou continua disponivel, intacto

    def test_numbers_and_city_ids_are_shown_the_way_people_read_them(self):
        fmt = self._format("Ciclo concluido: cidade=69176 | destino=floresta | doado=9,264 | falta_agora=979,428 | carry_over=0 | proximo_em=87286s")
        fields = _fields(fmt)
        self.assertEqual(fmt["title"], "Ciclo concluído")
        self.assertEqual((fields["Cidade"], fields["Doado"], fields["Falta agora"], fields["Próximo em"]), ("Alexa Trops", "9.264", "979.428", "1d"))
        city = next(field for field in fmt["fields"] if field["label"] == "Cidade")
        self.assertEqual((city["kind"], city["title"]), ("city", "cidade 69176"))

    def test_executor_lines_in_english_are_translated(self):
        started = self._format("Job started on ikabot-agent-rdg")
        self.assertEqual((started["title"], _fields(started), started["family"]), ("Execução iniciada", {"Agente": "ikabot-agent-rdg"}, "start"))

        again = self._format("Rescheduled in 87286s")
        when = timezone.localtime(self.now + timedelta(seconds=87286)).strftime("%d/%m %H:%M")
        self.assertEqual((again["title"], _fields(again)), ("Próxima execução agendada", {"Daqui a": "1d", "Quando": when}))

        login = self._format("Login: s78-br | rdg*** | proxy 1/3")
        self.assertEqual((login["title"], _fields(login)), ("Login no jogo", {"Servidor": "s78-br", "Conta": "rdg***", "Proxy": "1/3"}))
        self.assertEqual(self._format("Login OK")["title"], "Login concluído")
        self.assertEqual(self._format("Reutilizando sessao cached via proxy 1/3")["title"], "Sessão reaproveitada, sem novo login")
        self.assertEqual(self._format(f"Collecting daily login bonus for account {JOB_UUID}")["title"], "Coletando o bônus diário de login")

    def test_arrow_between_two_named_values_is_two_fields(self):
        fmt = self._format(f"[ShipAvail:mercante] usando ETA conhecido da cadeia = 540s | origem=Reflexo -> destino=Alexa Trops | job={JOB_UUID}")
        fields = _fields(fmt)
        self.assertEqual((fields["Origem"], fields["Destino"]), ("Reflexo", "Alexa Trops"))
        self.assertEqual([tag["text"] for tag in fmt["tags"]], ["ShipAvail:mercante"])
        self.assertTrue(fmt["title"].startswith("Usando ETA"))
        self.assertIn("9min", fmt["title"])

    def test_jobs_and_market_orders_become_links(self):
        fmt = self._format(f"Monitor criado | job={JOB_UUID}")
        link = next(field for field in fmt["fields"] if field["label"] == "Job")
        self.assertEqual((link["value"], link["kind"], link["href"]), ("0b54e6c2", "link", reverse("jobs:job-detail", args=[JOB_UUID])))

        order = self._format(f"[Order {JOB_UUID}] Cleanup aplicado em Valhala | bo=7 | Enxofre total=0 | modo=clear")
        self.assertEqual(order["tags"], [{"text": "Ordem 0b54e6c2", "href": reverse("market:order-detail", args=[JOB_UUID])}])
        self.assertEqual(order["title"], "Cleanup aplicado em Valhala")

    def test_resources_get_their_icon(self):
        plan = self._format("Planejamento wood: cidades=9 | estoque_total=11,645,461 | necessidade_total=33,035 | disponivel_total=32,728")
        self.assertEqual((plan["title"], plan["family"]), ("Planejamento Madeira", "plan"))  # "disponivel" nao e "nivel"
        self.assertTrue(plan["image_url"].endswith("icon_wood.png"))
        self.assertEqual(_fields(plan)["Estoque total"], "11.645.461")

        field = self._format("Remessa | recurso=marble | quantidade=20,000")["fields"][0]
        self.assertEqual((field["value"], field["kind"]), ("Mármore", "resource"))
        self.assertTrue(field["icon_url"].endswith("icon_marble.png"))

    def test_levels_are_normalised_and_problems_keep_the_problem_icon(self):
        warn = self._format("Sem capacidade imediata; reagendando em 1200s", level="warning")
        self.assertEqual((warn["level"], warn["level_label"], warn["icon"], warn["image_url"]), ("warn", "Aviso", "bi-exclamation-triangle-fill", ""))
        self.assertIn("20min", warn["title"])
        error = self._format("Compra no mercado falhou: a oferta esta a 21 de ouro por unidade", level="error")
        self.assertEqual((error["level"], error["icon"]), ("error", "bi-x-circle-fill"))
        self.assertEqual(self._format("qualquer coisa", level="trace")["level"], "info")
        self.assertEqual(self._format("", level=None)["title"], "")

    def test_titles_keep_city_names_as_written(self):
        fmt = self._format("lll1lll (mercado nv 20, alcance 10): Madeira: 24 oferta(s), a partir de 21 de ouro | Vinho: 2 oferta(s), a partir de 12 de ouro")
        self.assertTrue(fmt["title"].startswith("lll1lll (mercado nv 20"))
        self.assertEqual(self._format("ETA transporte: fila=746s | carregamento=766s | viagem=2400s | total=3912s")["title"], "Tempo estimado do transporte")

    def test_fields_listed_in_parentheses_or_after_a_colon(self):
        market = self._format(
            "Mercado interno indisponivel para sulfur (cidade=Polis, falta=3039, pedido=10400, "
            "motivo=ouro abaixo do market_min_gold, detalhe=ouro atual 163.291; reservado 145.600)",
            level="warn",
        )
        self.assertEqual(market["title"], "Mercado interno indisponível para Enxofre")
        self.assertEqual(_fields(market), {
            "Cidade": "Polis", "Falta": "3.039", "Pedido": "10.400",
            "Motivo": "ouro abaixo do ouro mínimo do mercado", "Detalhe": "ouro atual 163.291; reservado 145.600",
        })

        wine = self._format("Cidades em risco de vinho: TheTower (1366.9h, felicidade=23), NoDrive (sem ETA, felicidade=23)")
        self.assertEqual((wine["title"], _fields(wine)), (
            "Cidades em risco de vinho", {"TheTower": "1366.9h, felicidade 23", "NoDrive": "sem ETA, felicidade 23"},
        ))

        status = self._format("Status concluído: 9 cidades, ouro=63,497,766")
        self.assertEqual(status["title"], "Status concluído")
        self.assertEqual([(field["label"], field["value"], field["kind"]) for field in status["fields"]], [("", "9 cidades", "text"), ("Ouro", "63.497.766", "resource")])

        # frase comum com virgula e parenteses continua sendo uma frase
        prose = self._format("Compra no mercado falhou: a oferta esta a 21 de ouro por unidade, acima do preco maximo aceito (1)", level="error")
        self.assertEqual((prose["title"], prose["fields"]), ("Compra no mercado falhou: a oferta está a 21 de ouro por unidade, acima do preço máximo aceito (1)", []))

    def test_english_and_network_errors_are_said_in_portuguese(self):
        self.assertEqual(self._format("Login failed: boom", level="error")["title"], "Login falhou: boom")
        self.assertEqual(self._format("Starting login for account 0b54e6c2")["title"], "Iniciando login")
        done = self._format(f"[Order {JOB_UUID}] Order marked as completed")
        self.assertEqual((done["title"], done["tags"][0]["text"]), ("Ordem marcada como concluída", "Ordem 0b54e6c2"))
        network = self._format(
            "Erro de rede transitório (ReadTimeout): HTTPSConnectionPool(host='s78-br.ikariam.gameforge.com', port=443): "
            "Read timed out. (read timeout=30). Reagendando em 5min.", level="warn",
        )
        self.assertEqual(network["title"], "Erro de rede transitório (ReadTimeout): sem resposta de s78-br.ikariam.gameforge.com em 30s. Reagendando em 5min.")
        self.assertEqual(network["fields"], [])
        tasks = self._format("Daily tasks: cidade=Okolnir | favor=2500/2500 | tarefas=1/8 | coletaveis=1")
        self.assertEqual((tasks["title"], _fields(tasks)), ("Tarefas diárias", {"Cidade": "Okolnir", "Favor": "2500/2500", "Tarefas": "1/8", "Coletáveis": "1"}))
        retry = self._format("Erro no loop do santuario: gods_not_researched | retry_em=10800s", level="error")
        self.assertEqual((retry["title"], _fields(retry)), ("Erro no loop do santuário: deuses ainda não pesquisados", {"Nova tentativa em": "3h"}))

    def test_accents_follow_the_word_endings_and_leave_other_words_alone(self):
        self.assertEqual(self._format("Previsao de compra | fila=0s | ida=1698s | estoque_base_sulfur=0")["title"], "Previsão de compra")
        self.assertIn("Estoque base enxofre", _fields(self._format("Previsao de compra | estoque_base_sulfur=0")))
        self.assertEqual(
            self._format("Polis sem recurso suficiente para Residencia do Governador e sem ETA local confiavel")["title"],
            "Polis sem recurso suficiente para Residência do Governador e sem ETA local confiável",
        )
        self.assertEqual(self._format("travel level marvel distribuição")["title"], "travel level marvel distribuição")
        self.assertEqual(self._format("Guard de ouro: saldo=1,099,291 | gastavel=999,291 | max_unidades=111032")["fields"][-1]["value"], "111.032")

    def test_known_cities_lose_their_code(self):
        buy = self._format(f"[Order {JOB_UUID}] Compra interna: Cidade 39272 -> Cidade 69176 | Enxofre x10400")
        self.assertEqual(buy["title"], "Compra interna: Hell → Alexa Trops")
        self.assertEqual([(field["label"], field["value"], field["kind"]) for field in buy["fields"]], [("Enxofre", "10.400", "resource")])
        self.assertEqual(
            self._format("Remessa criada para cobrir ate 2 niveis: Hell (39272) -> Alexa Trops (69176)")["title"],
            "Remessa criada para cobrir até 2 níveis: Hell → Alexa Trops",
        )
        self.assertEqual(self._format("Colonia fundada detectada: Nova (12345)")["title"], "Colônia fundada detectada: Nova (12345)")
        inbox = self._format("Verificando inbox de diplomacia (city_id=39272)")
        self.assertEqual((inbox["title"], _fields(inbox)), ("Verificando a caixa de entrada de diplomacia", {"Cidade": "Hell"}))
        # nome e codigo da mesma cidade na mesma linha: aparece uma vez so
        for line in ("cidade=Hell | city_id=39272", "cidade=Polis | city_id=99999"):
            step = self._format(f"Executando etapa | {line} | predio=Pirotecnico | nivel=24->25 | estimado=madeira=47.643 | mármore=42.948")
            self.assertEqual(
                [(field["label"], field["value"]) for field in step["fields"]],
                [("Cidade", line.split(" | ")[0].split("=")[1]), ("Prédio", "Pirotécnico"), ("Nível", "24→25"), ("Estimado", "madeira 47.643"), ("Mármore", "42.948")],
            )

    def test_job_created_by_a_line_becomes_a_link(self):
        fmt = self._format(f"Retry manual solicitado; novo job imediato criado: {JOB_UUID}")
        self.assertEqual(fmt["title"], "Nova tentativa manual solicitada; novo job imediato criado")
        self.assertEqual([(field["label"], field["value"], field["href"]) for field in fmt["fields"]], [("Job", "0b54e6c2", reverse("jobs:job-detail", args=[JOB_UUID]))])
        waiting = self._format("Monitor de compra reagendado em 1800s | fase=stock_arrived | motivo=aguardando movimento/confirmacao")
        self.assertEqual((waiting["title"], _fields(waiting)), ("Monitor de compra reagendado em 30min", {"Fase": "estoque chegou", "Motivo": "aguardando movimento/confirmação"}))

    def test_entries_mark_long_pauses_and_a_second_run(self):
        job = _Fixture().job(1006, "finished")
        base = timezone.now() - timedelta(hours=2)
        lines = [
            ("info", "Job started on agent-a", 0), ("debug", "Buscando cidade 1/9 (id=1)...", 2), ("info", "Aguardando barcos", 2),
            ("warn", "Sem capacidade imediata; reagendando em 1200s", 600), ("info", "Job started on agent-a", 1800), ("error", "Falhou", 1801),
        ]
        logs = [_log(job, level, message, base + timedelta(seconds=offset)) for level, message, offset in lines]
        entries = build_entries(logs)
        self.assertEqual([entry["delta"] for entry in entries], ["", "2s", "", "9min 58s", "20min", "1s"])
        self.assertEqual([bool(entry["gap"]) for entry in entries], [False, False, False, True, True, False])
        self.assertEqual([entry["restart"] for entry in entries], [False, False, False, False, True, False])
        self.assertEqual(
            [(item["level"], item["count"]) for item in level_counts(entries)],
            [("error", 1), ("warn", 1), ("info", 3), ("debug", 1)],
        )


class _Fixture:
    """A user, a node, a lobby account and one game account with two cities."""

    def __init__(self):
        self.user = get_user_model().objects.create_user(username="logs", email="logs@example.com", password="secret123")
        self.node = Node.objects.create(name="node-logs")
        self.account = Account.objects.create(node=self.node, label="Lobby", email="logs-lobby@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=9, server_id="s9-br", server_language="br", server_number=9, name="Conta",
        )
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga,
            cities=[{"id": 69176, "name": "Alexa Trops"}, {"id": 39272, "name": "Hell"}],
        )

    def job(self, code, status, **extra):
        return Job.objects.create(
            account=self.account, game_account=self.ga, node=self.node, action_code=code,
            status=status, inputs_json=extra.pop("inputs_json", "{}"), timeout_sec=1800, **extra,
        )


def _log(job, level, message, moment):
    log = JobLog.objects.create(job=job, level=level, message=message)
    JobLog.objects.filter(pk=log.pk).update(created_at=moment)
    log.created_at = moment
    return log


class JobLogsPageTests(TestCase):
    def setUp(self):
        self.fx = _Fixture()
        self.client.force_login(self.fx.user)
        self.job = self.fx.job(1006, "finished", started_at=timezone.now() - timedelta(minutes=5))
        base = self.job.started_at
        for offset, (level, message) in enumerate([
            ("info", "Job started on ikabot-agent-rdg"),
            ("info", "Loop de doacao: 69176 (69176) | destino=floresta | metodo=2"),
            ("debug", "Snapshot de recursos atualizado: cidade=39272 | motivo=saida de transporte para Midgard"),
            ("warn", "Sem capacidade imediata; reagendando em 1200s"),
            ("error", "Falhou <script>alert(1)</script>"),
            ("info", "Rescheduled in 87286s"),
        ]):
            _log(self.job, level, message, base + timedelta(seconds=offset * 3))

    def _assert_log_block(self, html):
        self.assertNotIn("{#", html)
        self.assertEqual(re.findall(r'class="ikl-row" data-level="(\w+)"', html), ["info", "info", "debug", "warn", "error", "info"])
        chips = re.findall(r'data-ik-log-chip="(\w+)"[^>]*>\s*<i[^>]*></i>([^<]+)<b>(\d+)</b>', html)
        self.assertEqual([(level, label.strip(), count) for level, label, count in chips], [("error", "Erro", "1"), ("warn", "Aviso", "1"), ("info", "Info", "3"), ("debug", "Debug", "1")])
        self.assertIn('data-ik-log-chip="raw"', html)
        self.assertIn("Execução iniciada", html)
        self.assertIn("Loop de doação: Alexa Trops", html)
        self.assertIn("Próxima execução agendada", html)
        self.assertIn("icon_time.png", html)
        self.assertIn("6 linhas em 15s", html)
        # o texto original continua na pagina (botao "Texto original") e nada do agente vira HTML
        self.assertIn('<div class="ikl-raw">Loop de doacao: 69176 (69176) | destino=floresta | metodo=2</div>', html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_job_page_shows_the_readable_log_with_level_filters(self):
        response = self.client.get(reverse("jobs:job-detail", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self._assert_log_block(html)
        self.assertIn("window.ikLogs", html)          # o filtro por nivel vem junto com a pagina
        self.assertNotIn('hx-trigger="every 3s"', html)  # job terminado nao fica consultando

    def test_polled_partial_is_the_same_block(self):
        self.job.status = "running"
        self.job.save(update_fields=["status"])
        response = self.client.get(reverse("jobs:job-logs", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self._assert_log_block(html)
        self.assertIn('id="job-logs" class="ikl" data-ik-logs', html)
        self.assertIn('hx-trigger="every 3s"', html)

    def test_job_without_logs(self):
        waiting = self.fx.job(1006, "scheduled")
        html = self.client.get(reverse("jobs:job-logs", args=[waiting.pk])).content.decode()
        self.assertIn("Aguardando execução", html)
        self.assertNotIn("ikl-row", html)


class WorkflowLogsByCycleTests(TestCase):
    def setUp(self):
        self.fx = _Fixture()
        self.client.force_login(self.fx.user)
        self.workflow = Workflow.objects.create(
            account=self.fx.account, game_account=self.fx.ga, node=self.fx.node, workflow_type="distribute", category="resources", status="waiting",
        )
        self.base = timezone.now() - timedelta(days=2)

    def _cycle(self, sequence, jobs, *, start=None):
        """jobs: [(action_code, status, [(level, message), ...], inputs_json)]"""
        start = start or self.base + timedelta(hours=sequence)
        run = WorkflowRun.objects.create(workflow=self.workflow, sequence=sequence, status="finished")
        created = []
        for index, (code, status, lines, inputs) in enumerate(jobs):
            job_start = start + timedelta(minutes=index)
            job = self.fx.job(code, status, workflow=self.workflow, workflow_run=run, started_at=job_start if lines else None, inputs_json=inputs)
            for offset, (level, message) in enumerate(lines):
                _log(job, level, message, job_start + timedelta(seconds=offset * 2))
            created.append(job)
        return run, created

    def _html(self, **params):
        response = self.client.get(reverse("jobs:workflow-logs", args=[self.workflow.pk]), params)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("{#", html)
        return html

    def test_each_cycle_is_a_block_with_its_lines_in_order(self):
        self._cycle(1, [(3, "finished", [("info", "Job started on agent-a"), ("info", "Primeira do ciclo um"), ("info", "Rescheduled in 1800s")], "{}")])
        _run, (plan, send, _waiting) = self._cycle(2, [
            (3, "finished", [("info", "Job started on agent-a"), ("warn", "Plano gerou 12 rotas; selecionando ate 5 viaveis neste ciclo")], "{}"),
            (2, "error", [("info", "Job started on agent-a"), ("error", "Transporte falhou")], '{"from_city_name": "Hell"}'),
            (2, "scheduled", [], "{}"),
        ])
        html = self._html()

        self.assertEqual(re.findall(r"Ciclo #(\d+)</span>", html), ["2", "1"])        # mais recente primeiro
        self.assertEqual(html.count('<details class="ikl-cycle"'), 2)
        self.assertEqual(re.findall(r"fim do ciclo #(\d+)", html), ["2", "1"])
        first, second = html.split('<details class="ikl-cycle"')[1:]
        # ciclo 2: dois jobs com log (o agendado, sem log, nao vira bloco), o pior status manda
        self.assertIn("Com erro", first)
        self.assertIn("+ 1 job", first)
        self.assertIn("1 erro", first)
        self.assertIn("1 aviso", first)
        self.assertEqual(first.count('class="ikl-job"'), 2)
        self.assertIn("— Hell", first)
        self.assertEqual(
            re.findall(r'href="([^"]+)" class="link-sea"', first),
            [reverse("jobs:job-detail", args=[plan.pk]), reverse("jobs:job-detail", args=[send.pk])],
        )
        self.assertLess(first.index("Plano gerou 12 rotas; selecionando até 5 viáveis neste ciclo"), first.index("Transporte falhou"))
        self.assertNotIn("Primeira do ciclo um", first)
        # ciclo 1: so as linhas dele, na ordem em que aconteceram
        self.assertIn("Concluído", second)
        self.assertLess(second.index("Execução iniciada"), second.index("Primeira do ciclo um"))
        self.assertLess(second.index("Primeira do ciclo um"), second.index("Próxima execução agendada"))
        self.assertIn("3 linhas", second)
        # os filtros contam a pagina inteira
        chips = dict(re.findall(r'data-ik-log-chip="(error|warn|info|debug)"[^>]*>\s*<i[^>]*></i>[^<]+<b>(\d+)</b>', html))
        self.assertEqual(chips, {"error": "1", "warn": "1", "info": "5"})

    def test_pages_are_made_of_whole_cycles(self):
        for sequence in range(1, 13):
            self._cycle(sequence, [(3, "finished", [("info", "Job started on agent-a"), ("info", f"linha do ciclo {sequence}")], "{}")])
        self._cycle(13, [(3, "scheduled", [], "{}")])   # ainda nao rodou: nao e um ciclo com log

        first = self._html()
        self.assertEqual(re.findall(r"Ciclo #(\d+)</span>", first), [str(n) for n in range(12, 2, -1)])
        self.assertIn("12 ciclos com log · página 1 de 2", first)
        second = self._html(page=2)
        self.assertEqual(re.findall(r"Ciclo #(\d+)</span>", second), ["2", "1"])
        self.assertEqual(self._html(page=99), second)   # pagina alem do fim cai na ultima
        self.assertIn("Nenhum log encontrado", self.client.get(
            reverse("jobs:workflow-logs", args=[Workflow.objects.create(
                account=self.fx.account, game_account=self.fx.ga, node=self.fx.node, workflow_type="distribute", status="waiting",
            ).pk])
        ).content.decode())

    def test_very_long_job_shows_its_end_and_says_what_was_left_out(self):
        lines = [("info", "Job started on agent-a")] + [("info", f"passo {n}") for n in range(1, 161)]
        self._cycle(1, [(3, "finished", lines, "{}")])
        html = self._html()
        self.assertEqual(html.count('class="ikl-row"'), 150)
        self.assertIn("11 linhas anteriores não aparecem aqui", html)
        self.assertNotIn("passo 10<", html)
        self.assertIn("passo 160<", html)

    def test_workflow_page_loads_the_cycles_and_the_filter_script(self):
        html = self.client.get(reverse("jobs:workflow-detail", args=[self.workflow.pk])).content.decode()
        self.assertIn("Logs por ciclo", html)
        self.assertIn("window.ikLogs", html)
        self.assertNotIn("{#", html)
