<#
set-railway-vars.ps1

Reads the Backend/.env file and sets Railway environment variables using the
Railway CLI. By default this script will:
- Skip blank lines and comments
- Skip DATABASE_URL (prompt to set it)
- Skip values that look like placeholders (contain 'your_' or 'your-' or 'your')
- Prompt before setting each variable (use -Auto to set without prompts)

Usage:
  powershell -ExecutionPolicy Bypass -File .\set-railway-vars.ps1
  powershell -ExecutionPolicy Bypass -File .\set-railway-vars.ps1 -Auto
#>

param(
  [switch]$Auto
)

$envFile = Join-Path $PSScriptRoot '.env'

if (-not (Test-Path $envFile)) {
  Write-Error "Env file not found: $envFile"
  exit 1
}

if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
  Write-Error "railway CLI not found. Install it first: npm i -g @railway/cli"
  exit 1
}

Write-Host "Reading env file: $envFile"

function LooksLikePlaceholder($val) {
  if (-not $val) { return $true }
  $lower = $val.ToLower()
  return $lower -match 'your_' -or $lower -match 'your-' -or $lower -match '^your' -or $lower -match 'replace' -or $lower -match 'changeme' -or $lower -match 'example' -or $lower -match 'TODO'
}

foreach ($raw in Get-Content $envFile) {
  $line = $raw.Trim()
  if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) { continue }
  $idx = $line.IndexOf('=')
  if ($idx -lt 0) { Write-Host "Skipping invalid line: $line"; continue }
  $key = $line.Substring(0, $idx).Trim()
  $value = $line.Substring($idx + 1)

  if ($key -eq 'DATABASE_URL') {
    if (-not $Auto) {
      $ans = Read-Host "Found DATABASE_URL. Do you want to set Railway variable DATABASE_URL from .env? (y/N)"
      if ($ans.ToLower() -ne 'y') { Write-Host "Skipping DATABASE_URL"; continue }
    }
  }

  if (LooksLikePlaceholder($value)) {
    if (-not $Auto) {
      $ans = Read-Host "Value for $key looks like a placeholder: '$value'. Set it anyway? (y/N)"
      if ($ans.ToLower() -ne 'y') { Write-Host "Skipping $key"; continue }
    } else {
      Write-Host "Skipping placeholder-like variable: $key"
      continue
    }
  }

  Write-Host "Setting Railway variable: $key"
  try {
    # Use cmd.exe /c to ensure the CLI receives a single KEY=VALUE argument token
    $cmd = "railway variables set $key=$value"
    Start-Process -FilePath cmd.exe -ArgumentList '/c', $cmd -NoNewWindow -Wait
  } catch {
    Write-Error ([string]::Format('Failed to set {0}: {1}', $key, $_))
  }
}

Write-Host "Done. Review variables in the Railway dashboard and restart the service."
