$ErrorActionPreference = "Stop"

$SuiteScript = Join-Path $PSScriptRoot "suite.ps1"
& $SuiteScript start @args
