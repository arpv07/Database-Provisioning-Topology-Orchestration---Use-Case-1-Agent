#!/usr/bin/env bash
set -e

PRUNE_VOLUME=0

for arg in "$@"; do
  case $arg in
    -v|--prune-volume)
      PRUNE_VOLUME=1
      shift
      ;;
  esac
done

echo "=== Tearing down Docker Compose stack ==="
docker compose down

if [ $PRUNE_VOLUME -eq 1 ]; then
  echo "=== Pruning ars_backups volume ==="
  docker volume rm ars_backups 2>/dev/null || true
  echo "ars_backups volume removed."
else
  echo "=== Keeping ars_backups volume (use -v or --prune-volume to remove) ==="
fi

echo "Teardown complete."
