#!/usr/bin/env bash
set -euo pipefail

NOXT_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$NOXT_REPO_ROOT"

for NOXT_REQUIRED_FILE in .env_dev .env.geoserver-cloud .env_test_cloud; do
    if [[ ! -f "$NOXT_REQUIRED_FILE" ]]; then
        echo "Required local config is missing: $NOXT_REQUIRED_FILE" >&2
        if [[ "$NOXT_REQUIRED_FILE" == ".env_test_cloud" ]]; then
            echo "Create it from .env_test_cloud.sample, then provide the two test token files." >&2
        fi
        exit 2
    fi
done

NOXT_CLOUD_TEST_LOG="${NOXT_CLOUD_TEST_LOG:-/tmp/noxt-geonode-cloud-$(date -u +%Y%m%dT%H%M%SZ).log}"
echo "GeoNode Cloud test log: $NOXT_CLOUD_TEST_LOG"

docker compose -p geonode-cloud-oidc \
    --env-file .env_dev \
    --env-file .env.geoserver-cloud \
    -f docker-compose.geoserver-cloud-stack.yml \
    exec -T django sh -lc '
set -eu
set -a
. /usr/src/geonode/.env_test_cloud
set +a

# Fail before touching disposable test data if the Cloud REST gateway or ACL API is unreachable.
curl --connect-timeout 3 --max-time 10 -fsS "${GEOSERVER_LOCATION}web/" >/dev/null || {
    echo "GeoServer Cloud gateway is unreachable at ${GEOSERVER_LOCATION}; test cleanup and tests were not started." >&2
    exit 3
}
curl --connect-timeout 3 --max-time 10 -fsS "${NOXT_GEOSERVER_ACL_HEALTH_URL}" >/dev/null || {
    echo "GeoServer ACL health endpoint is unreachable at ${NOXT_GEOSERVER_ACL_HEALTH_URL}; test cleanup and tests were not started." >&2
    exit 3
}
GEONODE_GEODATABASE=geonode_data python manage.py shell < /usr/src/geonode/docker/noxt_seed_cloud_demo.py
NOXT_CLOUD_TEST_STATE_ACTION=prepare python manage.py shell < /usr/src/geonode/docker/noxt_cloud_test_state.py

if python manage.py test --noinput --keepdb --verbosity 2 "$@"; then
    NOXT_CLOUD_TEST_STATUS=0
else
    NOXT_CLOUD_TEST_STATUS=$?
fi

if NOXT_CLOUD_TEST_STATE_ACTION=cleanup python manage.py shell < /usr/src/geonode/docker/noxt_cloud_test_state.py; then
    NOXT_CLOUD_CLEANUP_STATUS=0
else
    NOXT_CLOUD_CLEANUP_STATUS=$?
fi

if [ "$NOXT_CLOUD_CLEANUP_STATUS" -ne 0 ]; then
    echo "Cloud test-state cleanup failed with status $NOXT_CLOUD_CLEANUP_STATUS." >&2
    exit "$NOXT_CLOUD_CLEANUP_STATUS"
fi
exit "$NOXT_CLOUD_TEST_STATUS"
' noxt-cloud-tests "$@" 2>&1 | tee "$NOXT_CLOUD_TEST_LOG"
