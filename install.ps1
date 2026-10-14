# =============================================================================
# ikabot-web - instalador (Windows com Docker Desktop)
# =============================================================================
# Uso, na pasta do projeto (PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Sem perguntas: defina $env:IKABOT_YES=1, $env:IKABOT_MODE=ip e $env:IKABOT_HUB_PORT antes.
#
# O que faz: confere o Docker, gera o .env com senhas aleatórias (nunca sobrescreve um
# .env que já existe sem perguntar), pergunta como o hub vai ser acessado, baixa as
# imagens, sobe tudo e espera o hub ficar pronto.
# =============================================================================
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$RepoRaw = if ($env:IKABOT_REPO_RAW) { $env:IKABOT_REPO_RAW } else { "https://raw.githubusercontent.com/rdgweb/ikabot-web/main" }
$Yes = ($env:IKABOT_YES -eq "1")

function Say($text) { Write-Host $text }
function Step($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "Erro: $text" -ForegroundColor Red; exit 1 }

function Ask($question, $default = "") {
    if ($Yes) { return $default }
    $prompt = if ($default -ne "") { "$question [$default]" } else { $question }
    $answer = Read-Host $prompt
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim()
}

function New-Secret([int]$bytes = 32) {
    $buffer = New-Object byte[] $bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buffer)
    return -join ($buffer | ForEach-Object { $_.ToString("x2") })
}

function Set-Env($key, $value) {
    $lines = [System.Collections.Generic.List[string]](Get-Content -Path ".env" -Encoding UTF8)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($key))=") { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$key=$value") }
    # sem BOM e com fim de linha LF: o docker compose lê o .env assim sem surpresa
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $PWD ".env"), (($lines -join "`n") + "`n"), $utf8)
}

function Get-LocalIp {
    try {
        # the network card that has a gateway is the one the other machines reach
        $config = Get-NetIPConfiguration -ErrorAction Stop | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | Select-Object -First 1
        if ($config) { return $config.IPv4Address[0].IPAddress }
    } catch { }
    try {
        $address = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -First 1
        if ($address) { return $address.IPAddress }
    } catch { }
    return "127.0.0.1"
}

# ── 1. Docker ────────────────────────────────────────────────────────────────
Step "1/5  Conferindo o Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker não encontrado. Instale o Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/" }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "Falta o 'docker compose' (v2). Atualize o Docker Desktop." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "O Docker não respondeu. Abra o Docker Desktop e espere ele dizer que está rodando." }
Say ("  " + (docker --version))

# ── 2. Arquivos do projeto ───────────────────────────────────────────────────
Step "2/5  Arquivos do projeto"
foreach ($file in @("docker-compose.yml", ".env.example", "ikabotapi_overrides/SupportedUserAgents.json")) {
    if (-not (Test-Path $file)) {
        Say "  baixando $file"
        $folder = Split-Path $file -Parent
        if ($folder) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
        try { Invoke-WebRequest -UseBasicParsing -Uri "$RepoRaw/$file" -OutFile $file } catch { Fail "não consegui baixar $file de $RepoRaw" }
    }
}
Say "  ok"

# ── 3. .env ──────────────────────────────────────────────────────────────────
Step "3/5  Configuração (.env)"
$generated = $false
$password = ""
if (Test-Path ".env") {
    $keep = Ask "Já existe um .env. Manter como está? (s/n)" "s"
    if ($keep -match "^[nN]") {
        Copy-Item ".env" (".env.bak." + (Get-Date -Format "yyyyMMddHHmmss"))
        Remove-Item ".env"
        Say "  cópia guardada; gerando um novo"
    } else { Say "  mantendo o .env atual" }
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    foreach ($key in @("DJANGO_SECRET_KEY", "APP_SECRET", "AGENT_TOKEN", "MYSQL_ROOT_PASSWORD", "DB_PASSWORD", "REDIS_PASSWORD")) {
        Set-Env $key (New-Secret 32)
    }

    Say ""
    Say "  Como o hub vai ser aberto no navegador?"
    Say "    1) Direto pelo IP desta máquina, sem domínio  (http://IP:porta)"
    Say "    2) Por um domínio, com o Nginx Proxy Manager incluído (com ou sem HTTPS)"
    $mode = if ($env:IKABOT_MODE) { $env:IKABOT_MODE } else { Ask "  Escolha" "1" }
    $mode = if ($mode -in @("2", "npm", "proxy")) { "npm" } else { "ip" }
    $ip = if ($env:IKABOT_HOST) { $env:IKABOT_HOST } else { Get-LocalIp }

    if ($mode -eq "ip") {
        $port = if ($env:IKABOT_HUB_PORT) { $env:IKABOT_HUB_PORT } else { Ask "  Porta do hub" "8000" }
        Set-Env "HUB_PORT" $port
        Set-Env "HUB_BIND" "0.0.0.0"
        Set-Env "HUB_PUBLIC_URL" "http://${ip}:$port"
        Set-Env "COMPOSE_PROFILES" ""
    } else {
        $domain = if ($env:IKABOT_DOMAIN) { $env:IKABOT_DOMAIN } else { Ask "  Domínio do hub (ex.: ikabot.seudominio.com)" "" }
        if (-not $domain) { Fail "o modo com proxy precisa de um domínio apontando para esta máquina" }
        $https = Ask "  Vai usar HTTPS (certificado Let's Encrypt pelo Nginx Proxy Manager)? (s/n)" "s"
        $scheme = if ($https -match "^[nN]") { "http" } else { "https" }
        Set-Env "HUB_PORT" $(if ($env:IKABOT_HUB_PORT) { $env:IKABOT_HUB_PORT } else { "8000" })
        Set-Env "HUB_BIND" "127.0.0.1"
        Set-Env "HUB_PUBLIC_URL" "${scheme}://$domain"
        Set-Env "MANUAL_GATEWAY_SCHEME" $scheme
        Set-Env "COMPOSE_PROFILES" "proxy"
    }

    $user = Ask "  Usuário do administrador" "admin"
    $password = if ($env:IKABOT_ADMIN_PASSWORD) { $env:IKABOT_ADMIN_PASSWORD } else { "" }
    if (-not $password -and -not $Yes) {
        $secure = Read-Host "  Senha do administrador (Enter = gerar uma)" -AsSecureString
        $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    if (-not $password) { $password = New-Secret 9; $generated = $true }
    Set-Env "ADMIN_USERNAME" $user
    Set-Env "ADMIN_PASSWORD" $password

    $remote = Ask "  Vai ter agentes em OUTRA máquina? (s/n)" "n"
    if ($remote -match "^[sS]") { Set-Env "REDIS_BIND" "0.0.0.0" }
    Say "  .env criado"
}

# ── 4. Subir ─────────────────────────────────────────────────────────────────
Step "4/5  Baixando as imagens e subindo (a primeira vez demora: o IkabotAPI é montado aqui)"
docker compose pull --ignore-buildable
if ($LASTEXITCODE -ne 0) { docker compose pull hub agent }
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "o docker compose não conseguiu subir. Veja a mensagem acima." }

# ── 5. Esperar o hub ─────────────────────────────────────────────────────────
Step "5/5  Esperando o hub ficar pronto"
$hub = (docker compose ps -q hub).Trim()
$status = ""
for ($i = 0; $i -lt 60; $i++) {
    $status = (docker inspect -f "{{.State.Health.Status}}" $hub 2>$null)
    if ($status -eq "healthy") { break }
    Start-Sleep -Seconds 5
}
if ($status -ne "healthy") { Fail "o hub não ficou pronto em 5 minutos. Veja: docker compose logs hub" }

$envText = Get-Content ".env" -Encoding UTF8
$url = (($envText | Where-Object { $_ -like "HUB_PUBLIC_URL=*" }) -replace "^HUB_PUBLIC_URL=", "")
$admin = (($envText | Where-Object { $_ -like "ADMIN_USERNAME=*" }) -replace "^ADMIN_USERNAME=", "")
Step "Pronto."
Say "  Hub:      $url"
Say "  Usuário:  $admin"
if ($generated) { Say "  Senha:    $password   (anote; troque pela tela depois)" }
if ($envText | Where-Object { $_ -match "^COMPOSE_PROFILES=.*proxy" }) {
    Say ""
    Say "  Falta ligar o domínio no Nginx Proxy Manager:"
    Say "    1. Abra http://$(Get-LocalIp):81  (primeiro login: admin@example.com / changeme)"
    Say "    2. Proxy Hosts > Add: domínio do hub, encaminhar para  hub  porta 8000"
    Say "    3. Aba SSL: peça o certificado (se escolheu HTTPS) e marque Force SSL"
    Say "    Passo a passo, inclusive o controle manual: docs/proxy.md"
}
Say ""
Say "  Próximos passos: cadastre a primeira conta do jogo em Contas do Jogo."
Say "  Atualizar depois:  docker compose pull; docker compose up -d"
