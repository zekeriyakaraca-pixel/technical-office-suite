param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Src = Join-Path $ProjectRoot "src"
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$TeklaSitePackages = Join-Path $WorkspaceRoot "tekla-mcp-server\.venv\Lib\site-packages"

if (-not (Test-Path $Python)) {
    throw "Python environment not found: $Python"
}

$PreviousPythonPath = $env:PYTHONPATH
$PathParts = @($Src)
if (Test-Path $TeklaSitePackages) {
    $env:AUTOCAD_MCP_OPTIONAL_SITE_PACKAGES = $TeklaSitePackages
}
if (-not [string]::IsNullOrWhiteSpace($PreviousPythonPath)) {
    $PathParts += $PreviousPythonPath
}
$env:PYTHONPATH = ($PathParts -join ";")

try {
    & $Python -m autocad_mcp.technical_office @RemainingArgs
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
