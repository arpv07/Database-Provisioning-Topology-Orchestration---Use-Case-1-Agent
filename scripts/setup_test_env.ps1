# PowerShell Script for setup_test_env
$ErrorActionPreference = "Stop"

Write-Host "=== [1/4] Starting Docker Compose stack ==="
docker compose up -d

Write-Host "=== [2/4] Waiting for oracle-source container to report healthy ==="
$maxRetries = 60
$retry = 0
$status = ""

while ($status -ne "healthy" -and $retry -lt $maxRetries) {
    $status = (docker inspect --format='{{json .State.Health.Status}}' oracle-source 2>$null) -replace '"',''
    if ($status -eq "healthy") {
        Write-Host "oracle-source is HEALTHY!"
        break
    }
    Write-Host "Waiting for oracle-source... status is '$status' (attempt $($retry+1)/$maxRetries)"
    Start-Sleep -Seconds 5
    $retry++
}

if ($status -ne "healthy") {
    Write-Error "oracle-source failed to become healthy within timeout."
    exit 1
}

Write-Host "=== [3/4] Executing RMAN backup on oracle-source to /backups ==="
docker exec -u oracle oracle-source bash -c @"
  mkdir -p /backups
  rman target / <<EOF
  RUN {
    ALLOCATE CHANNEL ch1 DEVICE TYPE DISK;
    BACKUP DATABASE FORMAT '/backups/backup_%U.bkp' PLUS ARCHIVELOG;
  }
  EXIT;
EOF
"@

Write-Host "=== [4/4] Verifying required CLI tools on oracle-exadata-dev ==="
docker exec -u oracle oracle-exadata-dev bash -c @"
  MISSING=0
  for cmd in dbca sqlplus rman; do
    if ! which `$cmd >/dev/null 2>&1; then
      echo 'ERROR: Command '$cmd' is missing from PATH in oracle-exadata-dev!' >&2
      MISSING=1
    else
      echo 'Found '$cmd' at: '\$(which `$cmd)
    fi
  done
  if [ `$MISSING -eq 1 ]; then
    exit 1
  fi
"@

Write-Host "=== Test environment setup complete! ==="
