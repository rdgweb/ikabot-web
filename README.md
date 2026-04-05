# Central Ikabot (Web)

Central web para gerenciar contas do Ikariam com o motor real do ikabot, em containers isolados.

## O que ja esta pronto
- Login e protecao de API por token.
- Painel web em pt-BR com menu lateral.
- Cadastro de nos (com proxy), contas e perfis de automacao.
- Suporte opcional a `gf-token-production` por conta (fallback de login).
- Execucao de tarefas por perfil (modo facil) e manual (modo avancado).
- Logs por tarefa e relatorios por periodo.
- Integracao real com `python -m ikabot` no agente.

## Seguranca
Defina valores fortes no arquivo `.env` (use `.env.example` como base):
- `APP_SECRET`: chave de criptografia local.
- `ADMIN_PASSWORD`: senha do painel.
- `AGENT_TOKEN`: token interno entre agent e hub.

As senhas das contas ficam criptografadas no banco.

## Subir
```bash
docker compose up --build -d
```

Acesse `http://localhost:8080`.

## Uso rapido
1. Entre com usuario/senha de admin.
2. Aba `Contas e Nos`: cadastre no e conta.
3. Se necessario, preencha `GF Token` na conta para contornar bloqueios do login por senha.
4. Aba `Perfis de Automacao`: use o assistente de perfil (facil).
5. Aba `Executar Tarefas`: escolha conta + perfil e execute.
6. Aba `Relatorios`: veja status, top contas e falhas.

## Escalar agentes
```bash
docker compose up --scale agent=3 -d
```

## Observacoes
- Para fluxos muito especificos do ikabot, use o modo avancado com entradas extras (JSON).
- Cada conta roda com sessao isolada em `/data/sessions/<account_id>` no container `agent`.
- Um no pode executar varias contas; o isolamento de risco depende do proxy configurado no no.

## Reestruturacao (fonte unica)
- Mapa estrutural oficial: `shared/reports/RESTRUCTURACAO_FONTE_UNICA.md`
- Auditoria de ruido estrutural:
```bash
python tools/restructuring_audit.py
```
- Modo estrito (retorna erro se ainda houver ruido):
```bash
python tools/restructuring_audit.py --strict
```
