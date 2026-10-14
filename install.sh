#!/usr/bin/env bash
# =============================================================================
# ikabot-web - instalador (Linux)
# =============================================================================
# Uso, na pasta do projeto:   ./install.sh
# Sem perguntas (tudo pelo ambiente, para scripts e testes):
#   IKABOT_YES=1 IKABOT_MODE=ip IKABOT_HUB_PORT=8000 ./install.sh
#
# O que faz: confere o Docker, gera o .env com senhas aleatórias (nunca sobrescreve
# um .env que já existe sem perguntar), pergunta como o hub vai ser acessado, baixa
# as imagens, sobe tudo e espera o hub ficar pronto.
# =============================================================================
set -euo pipefail

REPO_RAW="${IKABOT_REPO_RAW:-https://raw.githubusercontent.com/rdgweb/ikabot-web/main}"
YES="${IKABOT_YES:-0}"
cd "$(dirname "$0")"

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mErro:\033[0m %s\n' "$*" >&2; exit 1; }

ask() {   # ask "pergunta" "padrão" -> resposta (o padrão quando IKABOT_YES=1 ou Enter)
    local question="$1" default="${2:-}" answer=""
    if [ "$YES" = "1" ]; then printf '%s' "$default"; return; fi
    if [ -n "$default" ]; then read -r -p "$question [$default]: " answer </dev/tty; else read -r -p "$question: " answer </dev/tty; fi
    printf '%s' "${answer:-$default}"
}

secret() { # uma senha aleatória só com letras e números (vai em URL e em linha de comando)
    if command -v openssl >/dev/null 2>&1; then openssl rand -hex "${1:-32}"
    else head -c "${1:-32}" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

set_env() { # set_env CHAVE valor  -> grava ou troca a linha no .env
    local key="$1" value="$2"
    if grep -q "^${key}=" .env; then
        local escaped; escaped=$(printf '%s' "$value" | sed -e 's/[\/&|]/\\&/g')
        sed -i "s|^${key}=.*|${key}=${escaped}|" .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

# ── 1. Docker ────────────────────────────────────────────────────────────────
step "1/5  Conferindo o Docker"
command -v docker >/dev/null 2>&1 || fail "Docker não encontrado. Instale: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "Falta o 'docker compose' (v2). Instale o plugin: https://docs.docker.com/compose/install/linux/"
docker info >/dev/null 2>&1 || fail "O Docker não respondeu. Ele está rodando? Seu usuário está no grupo docker (ou use sudo)?"
say "  $(docker --version) | $(docker compose version --short 2>/dev/null || true)"

# ── 2. Arquivos do projeto ───────────────────────────────────────────────────
step "2/5  Arquivos do projeto"
for file in docker-compose.yml .env.example ikabotapi_overrides/SupportedUserAgents.json; do
    if [ ! -f "$file" ]; then
        say "  baixando $file"
        mkdir -p "$(dirname "$file")"
        curl -fsSL "$REPO_RAW/$file" -o "$file" || fail "não consegui baixar $file de $REPO_RAW"
    fi
done
say "  ok"

# ── 3. .env ──────────────────────────────────────────────────────────────────
step "3/5  Configuração (.env)"
if [ -f .env ]; then
    keep=$(ask "Já existe um .env. Manter como está? (s/n)" "s")
    case "$keep" in
        [nN]*) cp .env ".env.bak.$(date +%Y%m%d%H%M%S)"; say "  cópia guardada; gerando um novo"; rm .env ;;
        *) say "  mantendo o .env atual" ;;
    esac
fi

if [ ! -f .env ]; then
    cp .env.example .env
    for key in DJANGO_SECRET_KEY APP_SECRET AGENT_TOKEN MYSQL_ROOT_PASSWORD DB_PASSWORD REDIS_PASSWORD; do
        set_env "$key" "$(secret 32)"
    done

    say ""
    say "  Como o hub vai ser aberto no navegador?"
    say "    1) Direto pelo IP desta máquina, sem domínio  (http://IP:porta)"
    say "    2) Por um domínio, com o Nginx Proxy Manager incluído (com ou sem HTTPS)"
    mode="${IKABOT_MODE:-}"
    [ -z "$mode" ] && mode=$(ask "  Escolha" "1")
    case "$mode" in 2|npm|proxy) mode=npm ;; *) mode=ip ;; esac

    ip=$(hostname -I 2>/dev/null | awk '{print $1}'); ip="${ip:-127.0.0.1}"
    if [ "$mode" = "ip" ]; then
        port="${IKABOT_HUB_PORT:-$(ask "  Porta do hub" "8000")}"
        set_env HUB_PORT "$port"
        set_env HUB_BIND "0.0.0.0"
        set_env HUB_PUBLIC_URL "http://${IKABOT_HOST:-$ip}:$port"
        set_env COMPOSE_PROFILES "telegram"      # sem HTTPS o Telegram não tem webhook
    else
        domain="${IKABOT_DOMAIN:-$(ask "  Domínio do hub (ex.: ikabot.seudominio.com)" "")}"
        [ -n "$domain" ] || fail "o modo com proxy precisa de um domínio apontando para esta máquina"
        https=$(ask "  Vai usar HTTPS (certificado Let's Encrypt pelo Nginx Proxy Manager)? (s/n)" "s")
        scheme=https; case "$https" in [nN]*) scheme=http ;; esac
        set_env HUB_PORT "${IKABOT_HUB_PORT:-8000}"
        set_env HUB_BIND "127.0.0.1"          # só o proxy fala com o hub
        set_env HUB_PUBLIC_URL "$scheme://$domain"
        set_env MANUAL_GATEWAY_SCHEME "$scheme"
        if [ "$scheme" = "https" ]; then set_env COMPOSE_PROFILES "proxy"; else set_env COMPOSE_PROFILES "proxy,telegram"; fi
    fi

    user=$(ask "  Usuário do administrador" "admin")
    password="${IKABOT_ADMIN_PASSWORD:-}"
    if [ -z "$password" ] && [ "$YES" != "1" ]; then
        read -r -s -p "  Senha do administrador (Enter = gerar uma): " password </dev/tty; echo
    fi
    generated=0
    if [ -z "$password" ]; then password=$(secret 9); generated=1; fi
    set_env ADMIN_USERNAME "$user"
    set_env ADMIN_PASSWORD "$password"

    remote=$(ask "  Vai ter agentes em OUTRA máquina? (s/n)" "n")
    case "$remote" in [sS]*) set_env REDIS_BIND "0.0.0.0" ;; esac

    tz=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || true)
    [ -n "$tz" ] && set_env TZ "$tz"
    chmod 600 .env
    say "  .env criado (permissão 600)"
fi

# ── 4. Subir ─────────────────────────────────────────────────────────────────
step "4/5  Baixando as imagens e subindo (a primeira vez leva alguns minutos)"
docker compose pull --ignore-pull-failures || true      # o que não estiver publicado é montado no passo seguinte
docker compose up -d || fail "o docker compose não conseguiu subir. Veja a mensagem acima (uma porta já em uso? mude HUB_PORT no .env)."

# ── 5. Esperar o hub ─────────────────────────────────────────────────────────
step "5/5  Esperando o hub ficar pronto"
hub=$(docker compose ps -q hub)
status=""
for _ in $(seq 1 60); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$hub" 2>/dev/null || echo "?")
    [ "$status" = "healthy" ] && break
    sleep 5
done
[ "$status" = "healthy" ] || fail "o hub não ficou pronto em 5 minutos. Veja: docker compose logs hub"

url=$(grep '^HUB_PUBLIC_URL=' .env | cut -d= -f2-)
admin=$(grep '^ADMIN_USERNAME=' .env | cut -d= -f2-)
step "Pronto."
say "  Hub:      ${url:-http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000}"
say "  Usuário:  $admin"
if [ "${generated:-0}" = "1" ]; then say "  Senha:    $password   (anote; troque pela tela depois)"; fi
if grep -q '^COMPOSE_PROFILES=.*proxy' .env; then
    say ""
    say "  Falta ligar o domínio no Nginx Proxy Manager:"
    say "    1. Abra http://$(hostname -I 2>/dev/null | awk '{print $1}'):81  (primeiro login: admin@example.com / changeme)"
    say "    2. Proxy Hosts > Add: domínio do hub, encaminhar para  hub  porta 8000"
    say "    3. Aba SSL: peça o certificado (se escolheu HTTPS) e marque Force SSL"
    say "    Passo a passo, inclusive o controle manual: docs/proxy.md"
fi
say ""
say "  Próximos passos: cadastre a primeira conta do jogo em Contas do Jogo."
say "  Atualizar depois:  docker compose pull && docker compose up -d"
