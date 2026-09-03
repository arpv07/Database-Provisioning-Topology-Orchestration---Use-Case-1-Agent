# PowerShell Script for teardown_test_env
param(
    [switch]$PruneVolume
)

Write-Host "=== Tearing down Docker Compose stack ==="
docker compose down

if ($PruneVolume) {
    Write-Host "=== Pruning ars_backups volume ==="
    docker volume rm ars_backups 2>$null
    Write-Host "ars_backups volume removed."
} else {
    Write-Host "=== Keeping ars_backups volume (use -PruneVolume to remove) ==="
}

Write-Host "Teardown complete."
