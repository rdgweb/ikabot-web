param(
  [switch]$BuildFromSource,
  [ValidateSet("all", "hub", "agent")]
  [string]$Target = "all",
  [int]$WaitTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeArgs = @("compose", "-f", (Join-Path $root "docker-compose.yml"))
$services = switch ($Target) {
  "hub" { @("hub") }
  "agent" { @("agent") }
  default { @() }
}

if ($BuildFromSource) {
  $composeArgs += @("-f", (Join-Path $root "docker-compose.build.yml"))
  & docker @composeArgs up -d --build --wait --wait-timeout $WaitTimeoutSeconds @services
} else {
  & docker @composeArgs pull @services
  if ($LASTEXITCODE -eq 0) {
    & docker @composeArgs up -d --wait --wait-timeout $WaitTimeoutSeconds @services
  }
}

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Host "Containers atualizados e saudaveis. Versoes em execucao:"
if ($Target -in @("all", "hub")) {
  $hubVersion = & docker @composeArgs exec -T hub sh -c "cat /app/VERSION"
  Write-Host "  hub:   $hubVersion"
}
if ($Target -in @("all", "agent")) {
  $agentVersion = & docker @composeArgs exec -T agent sh -c "cat /app/VERSION"
  Write-Host "  agent: $agentVersion"
}

& docker @composeArgs ps @services
exit $LASTEXITCODE
