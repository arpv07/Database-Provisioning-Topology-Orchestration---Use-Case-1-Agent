#!/usr/bin/env bash
set -e

echo "=== [1/4] Starting Docker Compose stack ==="
docker compose up -d

echo "=== [2/4] Waiting for oracle-source container to report healthy ==="
MAX_RETRIES=60
RETRY_COUNT=0
until [ "$STATUS" == "healthy" ] || [ $RETRY_COUNT -eq $MAX_RETRIES ]; do
  STATUS=$(docker inspect --format='{{json .State.Health.Status}}' oracle-source 2>/dev/null | tr -d '"' || true)
  if [ "$STATUS" == "healthy" ]; then
    echo "oracle-source is HEALTHY!"
    break
  fi
  echo "Waiting for oracle-source... status is '$STATUS' (attempt $((RETRY_COUNT+1))/$MAX_RETRIES)"
  sleep 5
  RETRY_COUNT=$((RETRY_COUNT+1))
done

if [ "$STATUS" != "healthy" ]; then
  echo "ERROR: oracle-source failed to become healthy within timeout." >&2
  exit 1
fi

echo "=== [3/4] Executing RMAN backup on oracle-source to /backups ==="
docker exec -u oracle oracle-source bash -c "
  mkdir -p /backups
  rman target / <<EOF
  RUN {
    ALLOCATE CHANNEL ch1 DEVICE TYPE DISK;
    BACKUP DATABASE FORMAT '/backups/backup_%U.bkp' PLUS ARCHIVELOG;
  }
  EXIT;
EOF
"

echo "=== [4/4] Verifying required CLI tools on oracle-exadata-dev ==="
docker exec -u oracle oracle-exadata-dev bash -c "
  MISSING=0
  for cmd in dbca sqlplus rman; do
    if ! which \$cmd >/dev/null 2>&1; then
      echo 'ERROR: Command \$cmd is missing from PATH in oracle-exadata-dev!' >&2
      MISSING=1
    else
      echo 'Found \$cmd at: '\$(which \$cmd)
    fi
  done
  if [ \$MISSING -eq 1 ]; then
    exit 1
  fi
"

echo "=== Test environment setup complete! ==="
