#Requires -Version 5.1
<#
.SYNOPSIS
  Technical Office Suite — SLA batch kontrolü ve raporlama.

.DESCRIPTION
  SIRKET_STANDARTLARI.md'deki SLA kurallarını kontrol eder:
    - 24 saat içinde atanmamış işleri uyarı logu olarak kaydeder
    - 72 saat tamamlanmamış işleri eskalasyon flag'i ile işaretler
    - Memory Bridge istatistiklerini raporlar

.PARAMETER RuntimeUrl
  Technical Office Runtime URL (default: http://localhost:7770)

.PARAMETER LogFile
  Batch log dosyası (default: workspace\batch-log.jsonl)

.EXAMPLE
  .\scripts\batch.ps1
  .\scripts\batch.ps1 -RuntimeUrl http://localhost:7770
#>
param(
  [string]$RuntimeUrl = "http://localhost:7770",
  [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $LogFile) { $LogFile = Join-Path $root "workspace\batch-log.jsonl" }
$logDir = Split-Path $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

function Write-Log {
  param([string]$Level, [string]$Message, [hashtable]$Data = @{})
  $entry = @{
    timestamp = (Get-Date).ToString("o")
    level     = $Level
    message   = $Message
    data      = $Data
  } | ConvertTo-Json -Compress
  Add-Content -Path $LogFile -Value $entry -Encoding UTF8
  $color = switch ($Level) { "ERROR" { "Red" } "WARN" { "Yellow" } "INFO" { "Cyan" } default { "White" } }
  Write-Host "[$Level] $Message" -ForegroundColor $color
}

function Invoke-Api {
  param([string]$Path)
  try {
    $response = Invoke-WebRequest -Uri "$RuntimeUrl$Path" -UseBasicParsing -TimeoutSec 10
    return $response.Content | ConvertFrom-Json
  } catch {
    return $null
  }
}

Write-Log "INFO" "Batch kontrol basliyor" @{ runtime_url = $RuntimeUrl }

# 1. Runtime erişilebilirlik kontrolü
$health = Invoke-Api "/api/health"
if (-not $health -or -not $health.ok) {
  Write-Log "ERROR" "Runtime erisileemiyor" @{ url = $RuntimeUrl }
  exit 1
}
Write-Log "INFO" "Runtime hazir" @{ status = $health.status; jobs_active = $health.jobs_active; jobs_total = $health.jobs_total }

# 2. SLA raporu
$slaData = Invoke-Api "/api/sla/report"
if ($slaData -and $slaData.ok) {
  $report = $slaData.report
  Write-Log "INFO" "SLA raporu alindi" @{
    total_jobs      = $report.total_jobs
    overdue_count   = $report.overdue_count
    compliance_rate = $report.sla_compliance_rate
  }

  if ($report.overdue_count -gt 0) {
    Write-Log "WARN" "Geciktirilen isler var" @{ count = $report.overdue_count }
    foreach ($job in $report.jobs) {
      if (-not $job.any_overdue) { continue }
      if (-not $job.assignment_sla.ok) {
        Write-Log "WARN" "Atama SLA asimi" @{
          job_id        = $job.job_id
          fsm_state     = $job.fsm_state
          elapsed_hours = $job.assignment_sla.elapsed_hours
          overdue_hours = $job.assignment_sla.overdue_hours
          message       = $job.assignment_sla.message
        }
      }
      if (-not $job.completion_sla.ok) {
        Write-Log "WARN" "Tamamlanma SLA asimi — Eskalasyon gerekli" @{
          job_id        = $job.job_id
          fsm_state     = $job.fsm_state
          elapsed_hours = $job.completion_sla.elapsed_hours
          overdue_hours = $job.completion_sla.overdue_hours
          message       = $job.completion_sla.message
        }
      }
    }
  } else {
    Write-Log "INFO" "Tum isler SLA uyumlu"
  }
} else {
  Write-Log "WARN" "SLA raporu alinamadi"
}

# 3. Memory Bridge istatistikleri
$memData = Invoke-Api "/api/memory/stats"
if ($memData -and $memData.ok) {
  $stats = $memData.stats
  Write-Log "INFO" "Memory Bridge istatistikleri" @{
    total_patterns          = $stats.total_patterns
    high_confidence         = $stats.high_confidence_patterns
    avg_confidence          = $stats.avg_confidence
    total_hits              = $stats.total_hits
  }
} else {
  Write-Log "WARN" "Memory Bridge istatistikleri alinamadi"
}

# 4. Metrikler özeti
$metricsData = Invoke-Api "/api/metrics"
if ($metricsData -and $metricsData.ok) {
  $m = $metricsData.metrics
  Write-Log "INFO" "Metrikler" @{
    uptime_seconds   = $m.uptime_seconds
    active_jobs      = $m.active_jobs
    jobs_by_status   = ($m.jobs_by_status | ConvertTo-Json -Compress)
    avg_extraction_s = $m.extraction.avg_seconds
  }
}

Write-Log "INFO" "Batch kontrol tamamlandi" @{ log_file = $LogFile }
Write-Host ""
Write-Host "Batch log: $LogFile" -ForegroundColor Green
