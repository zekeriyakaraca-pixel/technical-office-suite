param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$WorkspaceRoot = Resolve-Path (Join-Path $ProjectRoot "..")
$TeklaSitePackages = Join-Path $WorkspaceRoot "tekla-mcp-server\.venv\Lib\site-packages"
$env:PYTHONPATH = "$ProjectRoot\src;$env:PYTHONPATH"
if (Test-Path $TeklaSitePackages) {
    $env:AUTOCAD_MCP_OPTIONAL_SITE_PACKAGES = $TeklaSitePackages
}

if (-not (Test-Path $Python)) {
    Write-Error "Python environment not found: $Python"
    exit 1
}

& $Python -m autocad_mcp.technical_office.partlist @Args
exit $LASTEXITCODE
