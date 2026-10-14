# Acesso por domínio: Nginx Proxy Manager

O hub pode ser aberto de dois jeitos:

| Modo | Endereço | Precisa de |
|---|---|---|
| Direto | `http://IP-da-máquina:8000` | nada; serve para a rede local |
| Com domínio | `https://ikabot.seudominio.com` (ou `http://`) | um domínio apontando para a máquina e o Nginx Proxy Manager |

O instalador (`install.sh` / `install.ps1`) prepara os dois. Este guia é o passo que falta
no modo com domínio: ligar o domínio ao hub dentro do Nginx Proxy Manager (NPM).

## 1. Antes

- O domínio (registro A) aponta para o IP público da máquina.
- As portas 80 e 443 chegam à máquina (no roteador, encaminhe as duas).
- No `.env`, `COMPOSE_PROFILES=proxy` e `HUB_PUBLIC_URL=https://ikabot.seudominio.com`
  (o instalador já deixa assim).

## 2. Primeiro acesso ao painel do NPM

1. Abra `http://IP-da-máquina:81`.
2. Login inicial: `admin@example.com` / `changeme`. O NPM pede para trocar os dois na hora.
3. Depois de configurar, o painel pode ficar fechado para fora: no `.env`,
   `PROXY_ADMIN_BIND=127.0.0.1` e `docker compose up -d npm`. Para voltar a mexer, abra
   por um túnel SSH (`ssh -L 8181:127.0.0.1:81 usuario@servidor` e `http://localhost:8181`).

## 3. O hub

**Hosts → Proxy Hosts → Add Proxy Host**

| Campo | Valor |
|---|---|
| Domain Names | `ikabot.seudominio.com` |
| Scheme | `http` |
| Forward Hostname / IP | `hub` |
| Forward Port | `8000` |
| Block Common Exploits | ligado |
| Websockets Support | ligado |

Aba **SSL** (só com HTTPS): *Request a new SSL Certificate*, marque **Force SSL** e
**HTTP/2 Support**, aceite os termos do Let's Encrypt e salve. O certificado é renovado sozinho.

Sem HTTPS: deixe a aba SSL em branco e use `HUB_PUBLIC_URL=http://ikabot.seudominio.com`.

## 4. Controle manual (opcional)

O controle manual abre cada sessão do jogo num subdomínio próprio
(`<token>.jogo.seudominio.com`), servido pelo container `gateway`. Precisa de:

1. Um registro DNS curinga: `*.jogo.seudominio.com` → IP da máquina.
2. No `.env`: `MANUAL_GATEWAY_DOMAIN=jogo.seudominio.com` e `MANUAL_GATEWAY_SCHEME=https`
   (ou `http`), depois `docker compose up -d hub gateway`.
3. No NPM, outro Proxy Host:

| Campo | Valor |
|---|---|
| Domain Names | `*.jogo.seudominio.com` |
| Scheme | `http` |
| Forward Hostname / IP | `gateway` |
| Forward Port | `8001` |
| Websockets Support | ligado |

   Com HTTPS, o certificado de um domínio curinga exige o desafio por DNS (*Use a DNS
   Challenge* na aba SSL, com a API do seu provedor de DNS).

Sem domínio (só IP) o controle manual ainda não funciona; o botão não aparece no hub.

## 5. Já tenho meu proxy

Se já existe um nginx, Traefik, Caddy ou NPM na máquina, não ligue o perfil `proxy`: deixe
`COMPOSE_PROFILES` vazio, publique o hub só para a máquina (`HUB_BIND=127.0.0.1`) e aponte o
seu proxy para `127.0.0.1:8000` (hub) e `127.0.0.1:8001` (gateway). Para nginx puro:

```nginx
server {
    listen 80;
    server_name ikabot.seudominio.com;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }
}
```

## Problemas comuns

| Sintoma | Causa |
|---|---|
| "Falha de verificação CSRF" ao entrar | `HUB_PUBLIC_URL` não é o endereço que está na barra do navegador (http × https, com ou sem porta). Corrija e `docker compose up -d hub`. |
| "Bad Request (400)" | o mesmo: o host não está em `HUB_PUBLIC_URL` nem em `DJANGO_ALLOWED_HOSTS`. |
| NPM não emite o certificado | a porta 80 não chega à máquina, ou o domínio ainda não aponta para ela. |
| 502 Bad Gateway | o hub ainda está subindo (`docker compose ps`), ou o Forward Hostname não é `hub`. |
