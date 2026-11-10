#!/bin/bash

# Exit script in case of error
set -e

INVOKE_LOG_STDOUT=${INVOKE_LOG_STDOUT:-FALSE}
invoke () {
    if [ $INVOKE_LOG_STDOUT = 'true' ] || [ $INVOKE_LOG_STDOUT = 'True' ]
    then
        $(which invoke) $@
    else
        $(which invoke) $@ > /usr/src/geonode/invoke.log 2>&1
    fi
    echo "$@ tasks done"
}

# Start cron service
service cron restart

echo $"\n\n\n"
echo "-----------------------------------------------------"
echo "STARTING DJANGO ENTRYPOINT $(date)"
echo "-----------------------------------------------------"

invoke update

source $HOME/.bashrc
source $HOME/.override_env

cmd="$@"

if [ ${IS_CELERY} = "true" ]  || [ ${IS_CELERY} = "True" ]
then
    echo "Executing Celery server $cmd for Production"
else

    invoke migrations
    invoke prepare

    STATIC_ROOT="${STATIC_ROOT:-/mnt/volumes/statics/static/}"
    LOCKFILE_DIR="$(dirname "$STATIC_ROOT")"
    if [ ${FORCE_REINIT} = "true" ]  || [ ${FORCE_REINIT} = "True" ] || [ ! -e "${LOCKFILE_DIR}/geonode_init.lock" ]; then
        invoke fixtures
        invoke initialized
        invoke updateadmin
    fi

    # GeoServer Cloud uses GeoNode as its OIDC provider and GeoServer ACL for
    # resource rules. Keep the legacy GeoServer startup path unchanged.
    cloud_enabled="${GEOSERVER_CLOUD_ENABLED:-False}"
    security_backend="${GEOSERVER_SECURITY_BACKEND:-}"
    if [ "${cloud_enabled,,}" = "true" ]; then
        echo "Configuring GeoServer Cloud OAuth client"
        python manage.py configure_geoserver_cloud_oauth --settings="${DJANGO_SETTINGS_MODULE:-geonode_project.settings}"

        if [ -z "$security_backend" ]; then
            security_backend="acl"
        fi
        if [ "${security_backend,,}" = "acl" ]; then
            acl_health_url="${GEOSERVER_ACL_HEALTH_URL:-http://acl:8081/actuator/health}"
            acl_ready=False
            for attempt in $(seq 1 60); do
                if curl --connect-timeout 2 --max-time 5 -fsS "$acl_health_url" >/dev/null; then
                    acl_ready=True
                    break
                fi
                sleep 2
            done
            if [ "$acl_ready" != "True" ]; then
                echo "GeoServer Cloud ACL did not become healthy at $acl_health_url" >&2
                exit 1
            fi
            echo "Synchronizing GeoNode permissions with GeoServer Cloud ACL"
            python manage.py sync_security_rules --settings="${DJANGO_SETTINGS_MODULE:-geonode_project.settings}"
        fi
    fi

    invoke statics
    invoke loadthesauri

    echo "Executing UWSGI server $cmd for Production"
fi

echo "-----------------------------------------------------"
echo "FINISHED DJANGO ENTRYPOINT --------------------------"
echo "-----------------------------------------------------"

# Run the CMD
echo "got command $cmd"
exec $cmd
