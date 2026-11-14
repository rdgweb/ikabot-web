# N-186 — novas cidades em rotinas recorrentes

O primeiro despacho após instalar esta versão registra as cidades conhecidas. Quando
um despacho posterior observa uma cidade nova no snapshot, o Telegram da conta recebe
os botões **Incluir cidade** e **Não incluir**. O aceite vale para os próximos despachos;
não modifica o job que já está executando. Sem resposta, a seleção fica como estava.

Cobertura:

- Manter tropas/frotas com a mesma meta em todas as cidades (`selected_uniform`).
- Alerta de vinho, com agente **0.1.159 ou posterior**; versões anteriores não recebem
  uma promessa de confirmação que seus runners ainda não conseguem cumprir.
- Doação recorrente com a mesma configuração em todas as cidades, por destino de
  doação. O aceite cria uma rotina para a nova cidade, sem copiar saldos acumulados,
  recuperação ou progresso da cidade usada como referência.
- Rotinas recorrentes com o campo múltiplo `cities`, como distribuição de recursos.

Seleções parciais, execuções únicas e metas personalizadas por cidade não são
expandidas: é preciso configurar a nova cidade no formulário nesses casos. Cidades
que já existiam antes do primeiro registro não geram uma solicitação retroativa.

As respostas são persistidas por rotina/cidade, inclusive recusas. A mesma cidade
não gera outra pergunta ao desaparecer e reaparecer no snapshot. O callback verifica
a organização, o chat atual da conta, a existência da cidade e a rotina ainda ativa.
Alterações manuais da seleção prevalecem sobre aprovações anteriores.

Validação: `apps.jobs.tests_city_expansion` (SQLite e MariaDB no CI) e
`agent_v2/tests/test_alert_wine_runner.py`, sem mensagens reais nem ações no jogo.
Migração `jobs.0017` cria somente as tabelas de escopos e propostas; não altera
contas, nós, jobs existentes nem o histórico de jogo durante a migração.
