# ikabot-web

Painel web + worker para operar automacoes do Ikabot com `hub_v2` e `agent_v2`.

## O que este repositorio entrega

- `hub_v2`: painel Django, API interna e integracoes de administracao.
- `agent_v2`: worker que executa tarefas e conversa com o hub.
- `docker-compose.yml`: stack pronta com MariaDB, Redis, hub, agent e worker do Telegram.

## O que este repositorio nao entrega

- `IkabotAPI` nao faz parte deste repositorio.
- Recursos de captcha e geracao de token dependem de uma instancia externa do projeto oficial:
  `https://github.com/Ikabot-Collective/IkabotAPI`

Sem `IKABOTAPI_URL`, o sistema sobe normalmente, mas recursos que dependem dessa API externa nao vao funcionar.

## Requisitos

- Docker
- Docker Compose

Verificacao rapida:

```bash
docker --version
docker compose version
```

## Instalacao do zero

1. Clone o repositorio:

```bash
git clone https://github.com/rdgweb/ikabot-web.git
cd ikabot-web
```

2. Copie o arquivo de ambiente:

```bash
cp .env.example .env
```

No Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

3. Edite o `.env` e troque pelo menos estes valores:

- `DJANGO_SECRET_KEY`
- `DB_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `APP_SECRET`
- `AGENT_TOKEN`
- `ADMIN_PASSWORD`

4. Suba a stack:

```bash
docker compose up -d --build
```

5. Confira se os containers ficaram saudaveis:

```bash
docker compose ps
```

6. Abra o painel:

- Hub: `http://localhost:8000`
- phpMyAdmin opcional: `docker compose --profile tools up -d`

## Primeiro acesso

O hub cria automaticamente um usuario admin no primeiro boot usando as variaveis do `.env`:

- `ADMIN_USERNAME`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

Valores padrao do exemplo:

- usuario: `admin`
- email: `admin@ikabot.local`
- senha: a definida em `ADMIN_PASSWORD`

Depois do primeiro login, troque a senha se estiver usando um ambiente real.

## Configuracao minima para o sistema funcionar

### Hub

O hub sobe com:

- MariaDB
- Redis
- migracoes automaticas
- `collectstatic`
- criacao automatica do admin inicial

### Agent

O agent principal sobe junto com o hub por padrao.

Ele usa:

- `HUB_URL=http://hub:8000`
- `AGENT_TOKEN`
- `AGENT_NAME`

O agent extra `agent-vps-teste` ficou como perfil opcional:

```bash
docker compose --profile extra-agent up -d
```

## IkabotAPI

`IkabotAPI` e uma dependencia externa.

Se voce quiser usar captcha/token:

1. Suba uma instancia propria do projeto oficial.
2. Configure `IKABOTAPI_URL` no `.env`.
3. Reinicie o hub:

```bash
docker compose restart hub
```

Exemplo de URL:

```env
IKABOTAPI_URL=http://ikabotapi:5005
```

Repositorio oficial:

- `https://github.com/Ikabot-Collective/IkabotAPI`

## Atualizacao

Para atualizar a instalacao a partir do codigo:

```bash
git pull
docker compose up -d --build
```

## Modo com imagens prebuild

O compose tambem aceita imagens publicadas no Docker Hub via:

- `HUB_IMAGE`
- `AGENT_IMAGE`

Fluxo:

```bash
docker compose pull
docker compose up -d
```

Padroes atuais do `.env.example`:

- `blackoneal/ikabot-web-hub:latest`
- `blackoneal/ikabot-web-agent:latest`

## Publicar no Docker Hub

Build local:

```bash
docker build -t blackoneal/ikabot-web-hub:latest ./hub_v2
docker build -t blackoneal/ikabot-web-agent:latest ./agent_v2
```

Push:

```bash
docker push blackoneal/ikabot-web-hub:latest
docker push blackoneal/ikabot-web-agent:latest
```

Se quiser versionar:

```bash
docker tag blackoneal/ikabot-web-hub:latest blackoneal/ikabot-web-hub:v1
docker tag blackoneal/ikabot-web-agent:latest blackoneal/ikabot-web-agent:v1
docker push blackoneal/ikabot-web-hub:v1
docker push blackoneal/ikabot-web-agent:v1
```

## Comandos uteis

Subir:

```bash
docker compose up -d --build
```

Parar:

```bash
docker compose down
```

Ver logs do hub:

```bash
docker compose logs -f hub
```

Ver logs do agent:

```bash
docker compose logs -f agent
```

Refazer imagens:

```bash
docker compose build --no-cache
```

## Troubleshooting

### O painel nao abre

Veja os logs:

```bash
docker compose logs -f hub
```

### O agent nao conecta

Confirme:

- `AGENT_TOKEN` igual no hub e no agent
- `HUB_URL=http://hub:8000`
- container `hub` healthy

### Captcha/token nao funciona

Confirme:

- `IKABOTAPI_URL` preenchido
- instancia externa do IkabotAPI respondendo
- conectividade do container `hub` ate essa URL
