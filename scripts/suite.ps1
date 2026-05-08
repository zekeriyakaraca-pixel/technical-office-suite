param(
  [ValidateSet("start", "stop", "restart", "status", "logs")]
  [string]$Command = "start",
  [int]$RuntimePort = 7770,
  [switch]$Force,
  [int]$Tail = 80
)

$ErrorActionPreference = "Stop"

$SuiteRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeDir = Join-Path $SuiteRoot "runtime"
$RuntimePython = Join-Path $RuntimeDir ".venv\Scripts\python.exe"
$SuiteStateDir = Join-Path $SuiteRoot ".state\suite"
$LogsDir = Join-Path $SuiteStateDir "logs"
$UvCacheDir = Join-Path $SuiteRoot ".state\uv-cache"
$PytestTmp = Join-Path $SuiteRoot ".state\pytest-tmp"
$ProcessStatePath = Join-Path $SuiteStateDir "processes.json"

function New-SuiteDirectories {
  New-Item -ItemType Directory -Path $SuiteStateDir, $LogsDir, $UvCacheDir, $PytestTmp -Force | Out-Null
}

function Get-ProcessState {
  if (-not (Test-Path $ProcessStatePath)) {
    return $null
  }
  try {
    return Get-Content -Path $ProcessStatePath -Raw | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Test-ProcessAlive {
  param([object]$ProcessId)
  if ($null -eq $ProcessId) {
    return $false
  }
  $process = Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
  return $null -ne $process
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

function Get-CommandLine {
  param([int]$ProcessId)
  $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($processInfo) {
    return [string]$processInfo.CommandLine
  }
  return ""
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-SuitePortListener {
  param([int]$Port)
  $listenerPid = Get-PortListener -Port $Port
  if ($null -eq $listenerPid -or $listenerPid -eq $PID) {
    return
  }
  $commandLine = Get-CommandLine -ProcessId $listenerPid
  if ($commandLine -and $commandLine.Contains($SuiteRoot)) {
    Stop-ProcessTree -ProcessId $listenerPid
    Write-Host "Stopped old suite listener on port $Port (PID $listenerPid)"
    return
  }
  if ($Force) {
    Stop-ProcessTree -ProcessId $listenerPid
    Write-Host "Stopped process on port $Port with -Force (PID $listenerPid)"
    return
  }
  $process = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
  $processName = if ($process) { [string]$process.ProcessName } else { "unknown" }
  throw "Port $Port is already in use by PID $listenerPid ($processName), and it is not part of this suite. Use -Force if you want this script to stop it."
}

function Stop-Suite {
  $state = Get-ProcessState
  if ($state -and $state.runtime) {
    $entry = $state.runtime
    if ($entry -and (Test-ProcessAlive $entry.pid)) {
      Stop-ProcessTree -ProcessId ([int]$entry.pid)
      Write-Host "Stopped runtime (PID $($entry.pid))"
    }
    if ($entry -and $entry.port) {
      $portPid = Get-PortListener -Port ([int]$entry.port)
      if ($portPid) {
        Stop-ProcessTree -ProcessId ([int]$portPid)
        Write-Host "Stopped runtime listener on port $($entry.port) (PID $portPid)"
      }
    }
  }
  Stop-SuitePortListener -Port $RuntimePort
  Remove-Item -Path $ProcessStatePath -Force -ErrorAction SilentlyContinue
}

function Start-SuiteProcess {
  param(
    [string]$Name,
    [string]$WorkingDir,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$StdOutPath,
    [string]$StdErrPath
  )
  Remove-Item -Path $StdOutPath, $StdErrPath -Force -ErrorAction SilentlyContinue
  $process = Start-Process `
    -FilePath $FilePath `
    -ArgumentList $Arguments `
    -WorkingDirectory $WorkingDir `
    -RedirectStandardOutput $StdOutPath `
    -RedirectStandardError $StdErrPath `
    -WindowStyle Hidden `
    -PassThru
  Write-Host "Started $Name (PID $($process.Id))"
  return $process
}

function Start-Suite {
  New-SuiteDirectories
  Stop-Suite
  Start-Sleep -Seconds 1

  $runtimeOut = Join-Path $LogsDir "runtime.out.log"
  $runtimeErr = Join-Path $LogsDir "runtime.err.log"
  $pythonPath = "$RuntimeDir;$SuiteRoot\mcp\autocad-mcp-server\src"
  $env:TECH_OFFICE_SUITE_ROOT = $SuiteRoot
  $env:UV_CACHE_DIR = $UvCacheDir
  $env:TEMP = $PytestTmp
  $env:TMP = $PytestTmp
  $env:PYTHONPATH = $pythonPath
  $runtimeServerScript = Join-Path $SuiteRoot "scripts\runtime-server.ps1"
  $runtimeProcess = Start-SuiteProcess `
    -Name "runtime" `
    -WorkingDir $RuntimeDir `
    -FilePath "powershell" `
    -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runtimeServerScript, "-RuntimePort", "$RuntimePort") `
    -StdOutPath $runtimeOut `
    -StdErrPath $runtimeErr
  $state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    suite_root = $SuiteRoot
    runtime = [ordered]@{
      pid = $runtimeProcess.Id
      port = $RuntimePort
      url = "http://127.0.0.1:$RuntimePort"
      stdout = $runtimeOut
      stderr = $runtimeErr
    }
  }
  $state | ConvertTo-Json -Depth 5 | Set-Content -Path $ProcessStatePath -Encoding UTF8

  Write-Host "Technical Office runtime: http://127.0.0.1:$RuntimePort"
  Write-Host "2D dashboard: http://127.0.0.1:$RuntimePort/"
  Write-Host "State: $ProcessStatePath"
  Write-Host "Logs:  $LogsDir"
}

function Show-SuiteStatus {
  $state = Get-ProcessState
  if (-not $state) {
    Write-Host "No saved suite state. Use: .\scripts\suite.ps1 start"
    $portPid = Get-PortListener -Port $RuntimePort
    if ($portPid) {
      $process = Get-Process -Id $portPid -ErrorAction SilentlyContinue
      $processName = if ($process) { [string]$process.ProcessName } else { "unknown" }
      Write-Host "Port $RuntimePort is already listening: PID=$portPid process=$processName"
    }
    return
  }
  $entry = $state.runtime
  if ($entry) {
    $alive = Test-ProcessAlive $entry.pid
    $portPid = Get-PortListener -Port ([int]$entry.port)
    $portText = if ($portPid) { "port PID $portPid" } else { "port closed" }
    Write-Host "runtime PID=$($entry.pid) alive=$alive $portText url=$($entry.url)"
  }
}

function Show-SuiteLogs {
  New-SuiteDirectories
  foreach ($path in @(
      (Join-Path $LogsDir "runtime.out.log"),
      (Join-Path $LogsDir "runtime.err.log")
    )) {
    Write-Host ""
    Write-Host "== $path =="
    if (Test-Path $path) {
      Get-Content -Path $path -Tail $Tail
    } else {
      Write-Host "(no log yet)"
    }
  }
}

switch ($Command) {
  "start" { Start-Suite }
  "stop" { Stop-Suite; Write-Host "Technical Office runtime stopped." }
  "restart" { Stop-Suite; Start-Sleep -Seconds 1; Start-Suite }
  "status" { Show-SuiteStatus }
  "logs" { Show-SuiteLogs }
}
