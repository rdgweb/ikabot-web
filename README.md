# ikabot-web

Painel web e agente de automação para [Ikariam](https://www.ikariam.gameforge.com/), feito sobre o
projeto [ikabot](https://github.com/Ikabot-Collective/ikabot). Várias contas do jogo num painel só:
construção, envio de recursos, tropas, mercado entre as contas, alertas de ataque no Telegram e
mais, rodando 24 horas no seu próprio servidor.

*English summary at the end.*

## O que sobe

| Serviço | Para quê |
|---|---|
| `hub` | o painel, a API e a fila de tarefas (Django) |
| `agent` | executa as tarefas no jogo; dá para ter outros em outras máquinas |
| `ikabotapi` | gera o token de login e resolve captchas; sem ele o hub não entra no jogo |
| `mariadb`, `redis` | banco e fila |
| `gateway` | controle manual (jogar pelo painel); só funciona com domínio |
| `npm` *(opcional)* | Nginx Proxy Manager, para abrir o hub por um domínio, com ou sem HTTPS |
| `phpmyadmin` *(opcional)* | só para olhar as tabelas do banco; nada depende dele |
| `telegram` *(opcional)* | Telegram sem HTTPS: busca mensagens e botões em vez de esperar o webhook (o instalador liga quando não há HTTPS) |

## Requisitos

- Linux (VPS ou servidor) com Docker Engine e o plugin `docker compose` v2, **ou**
- Windows com Docker Desktop.
- 2 GB de memória livres e uns 5 GB de disco (a montagem do IkabotAPI baixa o Chromium).

## Instalar

Baixe o projeto e rode o instalador na pasta dele.

**Linux**

```bash
git clone https://github.com/rdgweb/ikabot-web.git
cd ikabot-web
./install.sh
```

**Windows** (PowerShell)

```powershell
git clone https://github.com/rdgweb/ikabot-web.git
cd ikabot-web
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O instalador:

1. confere o Docker;
2. cria o `.env` com senhas aleatórias (se já existir um, pergunta antes de mexer);
3. pergunta como o hub vai ser aberto, a porta, o usuário e a senha do administrador e se haverá
   agentes em outra máquina;
4. baixa as imagens e sobe tudo (a primeira vez leva alguns minutos);
5. espera o hub ficar pronto e mostra o endereço.

### Os dois modos de acesso

| | Endereço | Quando usar |
|---|---|---|
| **Direto** | `http://IP:8000` | rede local, ou um servidor só seu |
| **Com domínio** | `https://ikabot.seudominio.com` | acesso de fora, com ou sem HTTPS |

No modo com domínio o instalador liga o Nginx Proxy Manager; falta só cadastrar o domínio no
painel dele (porta 81). Passo a passo, inclusive o controle manual: [docs/proxy.md](docs/proxy.md).

### Instalar à mão

```bash
cp .env.example .env     # preencha TODAS as senhas: não há valor padrão, de propósito
docker compose pull
docker compose up -d
```

Para gerar uma senha: `openssl rand -hex 32`.

## Primeiro acesso

1. Abra o endereço mostrado no fim da instalação e entre com o administrador.
2. Em **Contas do Jogo**, cadastre a primeira conta: o agente entra no jogo e lê as cidades.
3. Troque a senha do administrador pela tela, se quiser: ela vale daí em diante (a do `.env`
   só serve para criar o usuário; `ADMIN_RESET_PASSWORD=true` força a do `.env` de novo).

## Configuração

Tudo fica no `.env`; o [`.env.example`](.env.example) explica cada linha. As que mais importam:

| Variável | Para quê |
|---|---|
| `HUB_PUBLIC_URL` | o endereço da barra do navegador. Errado, o login falha com erro de CSRF. |
| `HUB_PORT`, `HUB_BIND` | porta do hub e em que interface escuta (`127.0.0.1` atrás de um proxy) |
| `COMPOSE_PROFILES` | `proxy` (Nginx Proxy Manager), `tools` (phpMyAdmin), `telegram` (Telegram sem HTTPS), separados por vírgula |
| `REDIS_BIND` | `0.0.0.0` só se houver agentes em outra máquina |
| `MANUAL_GATEWAY_DOMAIN` | domínio do controle manual; vazio = desligado |
| `TZ` | fuso horário dos containers |

Banco, Redis e IkabotAPI escutam só em `127.0.0.1` por padrão.

## Agente em outra máquina

Para espalhar as contas por outros IPs. No servidor do hub, o Redis precisa estar aberto
(`REDIS_BIND=0.0.0.0` no `.env`, depois `docker compose up -d redis`) e a porta do hub acessível.
Na outra máquina:

```bash
docker run -d --restart unless-stopped --name ikabot-agent \
  -e HUB_URL=http://IP_DO_HUB:8000 \
  -e REDIS_URL=redis://:SENHA_DO_REDIS@IP_DO_HUB:6379/0 \
  -e AGENT_TOKEN=O_MESMO_AGENT_TOKEN_DO_HUB \
  -e AGENT_NAME=agente-vps-01 \
  --log-opt max-size=10m --log-opt max-file=3 \
  blackoneal/ikabot-web-agent:latest
```

Pela internet aberta, prefira uma VPN entre as máquinas: Redis e hub falam sem criptografia.

## Atualizar

```bash
docker compose pull
docker compose up -d
```

A página **Atualizações** do hub mostra as versões em uso e as publicadas, e atualiza os agentes
pelo supervisor quando ele está instalado.

## Backup

```bash
# copia o banco para um arquivo
docker compose exec -T mariadb sh -c 'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction ikabot_hub' > backup-$(date +%F).sql
# restaura
docker compose exec -T mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" ikabot_hub' < backup-AAAA-MM-DD.sql
```

Guarde também o `.env`: sem ele as senhas guardadas no banco não abrem (`APP_SECRET`).

## Diferenças da sua máquina

Um segundo agente, outra porta, um volume a mais: ponha num `docker-compose.override.yml` ao lado
do `docker-compose.yml`. O compose lê os dois sozinho e o git ignora o override, então
`git pull` nunca briga com as suas mudanças.

```yaml
services:
  agent_2:
    extends: { file: docker-compose.yml, service: agent }
    environment:
      AGENT_NAME: ikabot-agent-02
```

## Problemas comuns

| Sintoma | O que ver |
|---|---|
| O compose diz "defina ... no .env" | falta uma senha no `.env`; rode o instalador ou preencha à mão |
| Erro de CSRF ou "Bad Request (400)" ao entrar | `HUB_PUBLIC_URL` diferente do endereço da barra do navegador |
| O agente não aparece em Nós & Agents | `AGENT_TOKEN` igual no hub e no agente; `docker compose logs -f agent` |
| Login no jogo falha com token/captcha | `docker compose logs ikabotapi`; a primeira montagem leva minutos |
| O painel não abre | `docker compose ps` e `docker compose logs -f hub` |

Comandos úteis: `docker compose ps`, `docker compose logs -f hub`, `docker compose restart hub`,
`docker compose down` (para tudo, sem apagar dados).

---

## English summary

**Install.** Clone the repository and run `./install.sh` (Linux) or
`powershell -ExecutionPolicy Bypass -File .\install.ps1` (Windows with Docker Desktop). The installer
checks Docker, writes `.env` with random secrets, asks how the hub will be reached (directly at
`http://IP:8000`, or through a domain with the bundled Nginx Proxy Manager, with or without HTTPS),
pulls the images, starts everything and waits until the hub is healthy.

**Manual install.** `cp .env.example .env`, fill in every secret (there are no working defaults on
purpose: compose refuses to start without them), then `docker compose pull && docker compose up -d`.

**Access by domain.** See [docs/proxy.md](docs/proxy.md) (Portuguese, with tables): proxy host
`hub:8000`, optional wildcard host `gateway:8001` for manual control. Set `HUB_PUBLIC_URL` to the exact
address in the browser bar, or login fails with a CSRF error.

**Update.** `docker compose pull && docker compose up -d`. **Back up** the database with
`mariadb-dump` (see above) and keep your `.env`.

## Licença

Este projeto se apoia no [ikabot](https://github.com/Ikabot-Collective/ikabot), do Ikabot Collective.
