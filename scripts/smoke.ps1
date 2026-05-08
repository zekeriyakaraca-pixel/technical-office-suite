$ErrorActionPreference = "Stop"

$SuiteRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeDir = Join-Path $SuiteRoot "runtime"
$StateDir = Join-Path $SuiteRoot ".state"
$UvCacheDir = Join-Path $StateDir "uv-cache"
$PytestTmp = Join-Path $StateDir "pytest-tmp"
$env:TECH_OFFICE_SUITE_ROOT = $SuiteRoot
$env:UV_CACHE_DIR = $UvCacheDir
$env:TEMP = $PytestTmp
$env:TMP = $PytestTmp
$env:PYTHONPATH = "$RuntimeDir;$SuiteRoot\mcp\autocad-mcp-server\src"

New-Item -ItemType Directory -Path $UvCacheDir, $PytestTmp -Force | Out-Null

function Stop-ProcessTree {
  param([int]$ProcessId)
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Get-PortListener {
  param([int]$Port)
  $lines = & netstat -ano -p tcp
  foreach ($line in $lines) {
    if ($line -match "^\s*TCP\s+(?:127\.0\.0\.1|0\.0\.0\.0):$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
      return [int]$Matches[1]
    }
  }
  return $null
}

$runtimeCommand = "cd `"$RuntimeDir`"; `$env:TECH_OFFICE_SUITE_ROOT=`"$SuiteRoot`"; `$env:UV_CACHE_DIR=`"$UvCacheDir`"; `$env:TEMP=`"$PytestTmp`"; `$env:TMP=`"$PytestTmp`"; `$env:PYTHONPATH=`"$RuntimeDir;$SuiteRoot\mcp\autocad-mcp-server\src`"; uv run --extra dev python -m uvicorn technical_office_runtime.app:app --host 127.0.0.1 --port 7770"
$runtime = Start-Process -WindowStyle Hidden powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $runtimeCommand -PassThru

try {
  $ready = $false
  for ($i = 0; $i -lt 30; $i++) {
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:7770/health" -Method Get -TimeoutSec 2
      if ($health.ok -eq $true) {
        $ready = $true
        break
      }
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $ready) {
    throw "Runtime did not become ready on http://127.0.0.1:7770"
  }

  $root = Invoke-WebRequest -Uri "http://127.0.0.1:7770/" -Method Get -TimeoutSec 5 -UseBasicParsing
  if ($root.Content -notmatch "Technical Office 2D Runtime") {
    throw "2D dashboard root did not render expected content"
  }

  $state = Invoke-RestMethod -Uri "http://127.0.0.1:7770/state" -Method Get
  foreach ($agentId in @("teknik-ofis-muduru", "autocad-uzman-1", "autocad-uzman-2", "kalite-kontrol", "dokuman-kontrol")) {
    if (-not $state.active.PSObject.Properties.Name.Contains($agentId)) {
      throw "Missing active agent in /state: $agentId"
    }
  }

  $registry = Invoke-RestMethod -Uri "http://127.0.0.1:7770/registry" -Method Get
  if (-not $registry.models.PSObject.Properties.Name.Contains("technical-office/codex-cli")) {
    throw "Missing technical-office/codex-cli model in /registry"
  }

  $jobId = "smoke-001"
  $jobDir = Join-Path $SuiteRoot "workspace\imports\jobs\$jobId"
  $outputDir = Join-Path $SuiteRoot "workspace\outputs\jobs\$jobId"
  if (Test-Path $jobDir) {
    Remove-Item -LiteralPath $jobDir -Recurse -Force
  }
  if (Test-Path $outputDir) {
    Remove-Item -LiteralPath $outputDir -Recurse -Force
  }
  New-Item -ItemType Directory -Path $jobDir -Force | Out-Null
  $jobJson = @{
    job_id = $jobId
    project_name = "Smoke Pipeline Test"
    manager_agent = "teknik-ofis-muduru"
    created_at = (Get-Date).ToString("o")
  } | ConvertTo-Json -Depth 4
  Set-Content -Path (Join-Path $jobDir "job.json") -Value $jobJson -Encoding UTF8
  $stream = "BT /F1 12 Tf 10 180 Td`n(POZ: SMK100) Tj`n(PLAKA 200x100x10 S355) Tj`nET`n0 0 m 200 0 l 200 100 l 0 100 l h S`n"
  $pdf = "%PDF-1.4`n" +
    "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj`n" +
    "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj`n" +
    "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R >> endobj`n" +
    "4 0 obj << /Length $($stream.Length) >> stream`n$stream`nendstream endobj`n" +
    "xref`n0 5`n0000000000 65535 f `n" +
    "trailer << /Root 1 0 R >>`n%%EOF`n"
  [System.IO.File]::WriteAllBytes((Join-Path $jobDir "input.pdf"), [System.Text.Encoding]::GetEncoding("iso-8859-1").GetBytes($pdf))

  $run = Invoke-RestMethod -Uri "http://127.0.0.1:7770/api/jobs/$jobId/run" -Method Post -ContentType "application/json" -Body '{"autocad_live_policy":"off"}'
  if ($run.ok -ne $true) {
    throw "Job API run failed"
  }

  $detail = Invoke-RestMethod -Uri "http://127.0.0.1:7770/api/jobs/$jobId" -Method Get
  if ($detail.job.summary.ok -ne $true) {
    throw "Job smoke test failed: summary ok=$($detail.job.summary.ok)"
  }

  Write-Host "Smoke OK"
  Write-Host "Dashboard: http://127.0.0.1:7770/"
  Write-Host "Job status: $($detail.job.status)"
} finally {
  if ($runtime) {
    Stop-ProcessTree -ProcessId $runtime.Id
  }
  $portPid = Get-PortListener -Port 7770
  if ($portPid) {
    Stop-ProcessTree -ProcessId $portPid
  }
}
