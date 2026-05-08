param(
  [int]$RuntimePort = 7770
)

$ErrorActionPreference = "Stop"

$SuiteRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeDir = Join-Path $SuiteRoot "runtime"
$StateDir = Join-Path $SuiteRoot ".state"
$UvCacheDir = Join-Path $StateDir "uv-cache"
$PytestTmp = Join-Path $StateDir "pytest-tmp"

New-Item -ItemType Directory -Path $UvCacheDir, $PytestTmp -Force | Out-Null

$env:TECH_OFFICE_SUITE_ROOT = $SuiteRoot
$env:UV_CACHE_DIR = $UvCacheDir
$env:TEMP = $PytestTmp
$env:TMP = $PytestTmp
$env:PYTHONPATH = "$RuntimeDir;$SuiteRoot\mcp\autocad-mcp-server\src"

Set-Location $RuntimeDir
uv run --extra dev python -m uvicorn technical_office_runtime.app:app --host 127.0.0.1 --port $RuntimePort
