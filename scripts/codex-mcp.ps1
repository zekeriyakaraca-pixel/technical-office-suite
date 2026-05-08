$ErrorActionPreference = "Continue"

$SuiteRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ExpectedServer = Join-Path $SuiteRoot "mcp\autocad-mcp-server"
$ExpectedSrc = Join-Path $ExpectedServer "src"

Write-Host "Technical Office Codex MCP doctor"
Write-Host "Expected autocad-mcp path: $ExpectedServer"
Write-Host ""

$Codex = Get-Command codex.cmd -ErrorAction SilentlyContinue
if (-not $Codex) {
  Write-Host "codex.cmd: missing"
  exit 1
}

Write-Host "codex.cmd: $($Codex.Source)"
Write-Host ""

Write-Host "== codex.cmd --version =="
Write-Host "codex.cmd is available; run codex.cmd --version for raw CLI output"
Write-Host ""

Write-Host "== codex.cmd exec --help =="
Write-Host "runtime doctor validates exec help; this script validates MCP config without nested Codex PATH writes"
Write-Host ""

Write-Host "== codex.cmd mcp list =="
$ListOutput = ""
Write-Host "validated from ~/.codex/config.toml; run codex.cmd mcp list for raw CLI output"
Write-Host $ListOutput

Write-Host "== codex.cmd mcp get autocad-mcp =="
$GetOutput = ""
Write-Host "validated from ~/.codex/config.toml; run codex.cmd mcp get autocad-mcp for raw CLI output"
Write-Host $GetOutput

$ConfigPath = Join-Path $env:USERPROFILE ".codex\config.toml"
$ConfigText = ""
Write-Host "== Codex global config file =="
if (Test-Path $ConfigPath) {
  try {
    $ConfigText = Get-Content -Path $ConfigPath -Raw -Encoding UTF8
    Write-Host "readable: $ConfigPath"
  } catch {
    Write-Host "not readable: $ConfigPath"
  }
} else {
  Write-Host "missing: $ConfigPath"
}
Write-Host ""

$AutocadMcpConfig = ""
if ($ConfigText -match "(?ms)^\[mcp_servers\.autocad-mcp\].*?(?=^\[|\z)") {
  $AutocadMcpConfig = $Matches[0]
}

$Combined = ($ListOutput + "`n" + $GetOutput + "`n" + $AutocadMcpConfig).Replace('\', '/').ToLowerInvariant()
$ExpectedNormalized = $ExpectedServer.Replace('\', '/').ToLowerInvariant()

Write-Host "== result =="
if ($Combined.Contains($ExpectedNormalized)) {
  Write-Host "autocad-mcp: ready"
  exit 0
}

if ($Combined.Contains("technical_office_engineer")) {
  Write-Host "autocad-mcp: stale path detected"
} elseif ($Combined.Contains("failed to load configuration") -or $Combined.Contains("access is denied") -or $Combined.Contains("eris")) {
  Write-Host "autocad-mcp: Codex global MCP config could not be read through codex.cmd from this process"
} elseif ($Combined.Contains("autocad-mcp")) {
  Write-Host "autocad-mcp: configured, but not pointing to this repo"
} else {
  Write-Host "autocad-mcp: not configured"
}

Write-Host ""
Write-Host "Manual fix commands:"
Write-Host "  codex.cmd mcp remove autocad-mcp"
Write-Host "  codex.cmd mcp add autocad-mcp --env AUTOCAD_MCP_BACKEND=auto --env AUTOCAD_MCP_IPC_DIR=C:/temp --env PYTHONIOENCODING=utf-8 --env PYTHONPATH=`"$ExpectedSrc`" -- uv run --project `"$ExpectedServer`" python -m autocad_mcp"

exit 2
