#!/usr/bin/env bash
set -euo pipefail

NOXT_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$NOXT_REPO_ROOT"

for NOXT_REQUIRED_FILE in .env_dev .env.geoserver-cloud .env_test_cloud; do
    if [[ ! -f "$NOXT_REQUIRED_FILE" ]]; then
        echo "Required local config is missing: $NOXT_REQUIRED_FILE" >&2
        exit 2
    fi
done

docker compose -p geonode-cloud-oidc \
    --env-file .env_dev \
    --env-file .env.geoserver-cloud \
    -f docker-compose.geoserver-cloud-stack.yml \
    exec -T django sh -lc '
set -eu
set -a
. /usr/src/geonode/.env_test_cloud
set +a
GEONODE_GEODATABASE=geonode_data python manage.py shell < /usr/src/geonode/docker/noxt_seed_cloud_demo.py
'
