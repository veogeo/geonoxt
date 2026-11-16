"""Prepare and clean the disposable GeoNode + GeoServer Cloud test state."""

from datetime import timedelta
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from django.conf import settings
from django.db import connections
from django.utils import timezone
from oauth2_provider.models import AccessToken
from psycopg2 import sql

NOXT_CLOUD_TEST_DATABASES = ("test_geonode", "test_geonode_data")
NOXT_CLOUD_TEST_STORE = "test_geonode_data"
NOXT_CLOUD_PRODUCTION_STORE = "geonode_data"
NOXT_CLOUD_TEST_TOKEN_USERS = (
    ("noxt-bearer-test", "NOXT_GEOSERVER_CLOUD_TEST_AUTHORIZED_TOKEN_FILE"),
    ("noxt-bearer-outsider", "NOXT_GEOSERVER_CLOUD_TEST_UNAUTHORIZED_TOKEN_FILE"),
)


def noxt_feature_type_names(noxt_rest_base, noxt_auth, noxt_store):
    noxt_url = f"{noxt_rest_base}/workspaces/geonode/datastores/{noxt_store}/featuretypes.json"
    noxt_response = requests.get(noxt_url, auth=noxt_auth, timeout=30)
    noxt_response.raise_for_status()
    noxt_value = noxt_response.json().get("featureTypes", {})
    noxt_items = noxt_value.get("featureType", []) if isinstance(noxt_value, dict) else []
    if isinstance(noxt_items, dict):
        noxt_items = [noxt_items]
    return {
        noxt_item["name"]
        for noxt_item in noxt_items
        if isinstance(noxt_item, dict) and noxt_item.get("name")
    }


def noxt_ensure_cloud_test_workspace_and_stores():
    """Bootstrap GeoNode's workspace and stores in a fresh isolated Cloud catalog."""
    noxt_server = settings.OGC_SERVER["default"]
    noxt_rest_base = noxt_server["LOCATION"].rstrip("/") + "/rest"
    noxt_auth = (noxt_server["USER"], noxt_server["PASSWORD"])

    noxt_workspace_url = f"{noxt_rest_base}/workspaces/geonode.json"
    noxt_workspace_response = requests.get(noxt_workspace_url, auth=noxt_auth, timeout=30)
    if noxt_workspace_response.status_code == 404:
        noxt_workspace_response = requests.post(
            f"{noxt_rest_base}/workspaces",
            data="<workspace><name>geonode</name></workspace>",
            headers={"Content-Type": "application/xml"},
            auth=noxt_auth,
            timeout=30,
        )
    if noxt_workspace_response.status_code not in (200, 201, 202):
        raise RuntimeError(
            f"Could not ensure the GeoNode workspace in the isolated Cloud catalog: "
            f"HTTP {noxt_workspace_response.status_code} {noxt_workspace_response.text[:500]}"
        )

    noxt_databases = (
        (NOXT_CLOUD_PRODUCTION_STORE, settings.DATABASES["datastore"]),
        (NOXT_CLOUD_TEST_STORE, settings.DATABASES["default"]),
    )
    for noxt_store, noxt_database in noxt_databases:
        noxt_store_url = f"{noxt_rest_base}/workspaces/geonode/datastores/{noxt_store}.json"
        noxt_store_response = requests.get(noxt_store_url, auth=noxt_auth, timeout=30)
        if noxt_store_response.status_code not in (200, 404):
            raise RuntimeError(
                f"Could not inspect GeoNode store {noxt_store}: "
                f"HTTP {noxt_store_response.status_code} {noxt_store_response.text[:500]}"
            )

        noxt_connection = {
            "host": noxt_database.get("HOST") or "db",
            "port": str(noxt_database.get("PORT") or "5432"),
            "database": noxt_database["NAME"] if noxt_store == NOXT_CLOUD_PRODUCTION_STORE else NOXT_CLOUD_TEST_STORE,
            "user": noxt_database["USER"],
            "passwd": noxt_database["PASSWORD"],
            "dbtype": "postgis",
            "schema": "public",
            "validate connections": "true",
        }
        noxt_existing_connection = {}
        if noxt_store_response.status_code == 200:
            noxt_entries = (
                noxt_store_response.json()
                .get("dataStore", {})
                .get("connectionParameters", {})
                .get("entry", [])
            )
            noxt_existing_connection = {
                entry.get("@key"): entry.get("$")
                for entry in noxt_entries
                if isinstance(entry, dict) and entry.get("@key")
            }
            if all(str(noxt_existing_connection.get(key, "")) == str(value) for key, value in noxt_connection.items()):
                continue

        noxt_store_xml = ET.Element("dataStore")
        ET.SubElement(noxt_store_xml, "name").text = noxt_store
        ET.SubElement(noxt_store_xml, "enabled").text = "true"
        noxt_parameters = ET.SubElement(noxt_store_xml, "connectionParameters")
        for noxt_key, noxt_value in noxt_connection.items():
            ET.SubElement(noxt_parameters, "entry", {"key": noxt_key}).text = str(noxt_value)
        noxt_store_xml_data = ET.tostring(noxt_store_xml, encoding="unicode")

        if noxt_store_response.status_code == 404:
            noxt_write_response = requests.post(
                f"{noxt_rest_base}/workspaces/geonode/datastores",
                data=noxt_store_xml_data,
                headers={"Content-Type": "application/xml"},
                auth=noxt_auth,
                timeout=30,
            )
            noxt_action = "create"
        else:
            noxt_write_response = requests.put(
                noxt_store_url.removesuffix(".json"),
                data=noxt_store_xml_data,
                headers={"Content-Type": "application/xml"},
                auth=noxt_auth,
                timeout=30,
            )
            noxt_action = "update"
        if noxt_write_response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Could not {noxt_action} GeoNode store {noxt_store}: "
                f"HTTP {noxt_write_response.status_code} {noxt_write_response.text[:500]}"
            )
    print("GeoNode workspace and production/test stores are ready in the isolated Cloud catalog.")


def noxt_check_test_environment():
    if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
        raise RuntimeError("Cloud test runner requires NOXT_GEOSERVER_CLOUD_ENABLED=True")
    if not getattr(settings, "NOXT_GEOSERVER_ACL_ENABLED", False):
        raise RuntimeError("Cloud test runner requires ACL to be enabled")
    if os.environ.get("GEONODE_GEODATABASE") != NOXT_CLOUD_TEST_STORE:
        raise RuntimeError("Cloud tests must target GEONODE_GEODATABASE=test_geonode_data")
    if settings.DATABASES["default"]["NAME"] != "geonode":
        raise RuntimeError("Refusing cleanup: default database is not the expected GeoNode database")
    if settings.DATABASES.get("datastore", {}).get("NAME") != "geonode_data":
        raise RuntimeError("Refusing cleanup: datastore database is not the expected GeoNode data database")
    for noxt_username, noxt_path_variable in NOXT_CLOUD_TEST_TOKEN_USERS:
        noxt_path = os.environ.get(noxt_path_variable)
        if not noxt_path or not Path(noxt_path).is_file():
            raise RuntimeError(f"Missing test token file configured by {noxt_path_variable}")
        noxt_token = Path(noxt_path).read_text(encoding="utf-8").strip()
        if not noxt_token:
            raise RuntimeError(f"Test token file configured by {noxt_path_variable} is empty")
        noxt_record = AccessToken.objects.select_related("user", "application").get(
            token=noxt_token, user__username=noxt_username
        )
        if not noxt_record.user.is_active or not noxt_record.application_id:
            raise RuntimeError(f"Test OAuth token for {noxt_username} is not linked to an active test account")
        noxt_minimum_expiry = timezone.now() + timedelta(hours=12)
        if noxt_record.expires < noxt_minimum_expiry:
            noxt_record.expires = noxt_minimum_expiry
            noxt_record.save(update_fields=["expires"])
        print(f"Bearer test token ready for {noxt_username}; expires {noxt_record.expires.isoformat()}.")

    noxt_remote_url = os.environ.get("NOXT_SELENIUM_REMOTE_URL")
    if not noxt_remote_url:
        raise RuntimeError("Missing NOXT_SELENIUM_REMOTE_URL in .env_test_cloud")
    noxt_parts = urlsplit(noxt_remote_url)
    noxt_path = noxt_parts.path.rstrip("/")
    if noxt_path.endswith("/wd/hub"):
        noxt_path = noxt_path[:-7]
    noxt_status_url = urlunsplit(
        (noxt_parts.scheme, noxt_parts.netloc, f"{noxt_path}/status", "", "")
    )
    noxt_status_response = requests.get(noxt_status_url, timeout=15)
    noxt_status_response.raise_for_status()
    noxt_status = noxt_status_response.json().get("value", {})
    if noxt_status.get("ready") is False:
        raise RuntimeError(f"Remote Selenium node is not ready: {noxt_status.get('message', 'unknown status')}")
    print(f"Remote Selenium is ready at {noxt_remote_url}.")


def noxt_clean_cloud_test_catalog():
    noxt_server = settings.OGC_SERVER["default"]
    noxt_rest_base = noxt_server["LOCATION"].rstrip("/") + "/rest"
    noxt_auth = (noxt_server["USER"], noxt_server["PASSWORD"])
    noxt_test_layers = noxt_feature_type_names(noxt_rest_base, noxt_auth, NOXT_CLOUD_TEST_STORE)
    noxt_production_layers = noxt_feature_type_names(noxt_rest_base, noxt_auth, NOXT_CLOUD_PRODUCTION_STORE)
    noxt_overlap = noxt_test_layers & noxt_production_layers
    if noxt_overlap:
        raise RuntimeError(
            "Refusing to clean test layers whose names also exist in the persistent store: "
            + ", ".join(sorted(noxt_overlap))
        )

    from geonode.geoserver.noxt_acl import Noxt_GeoServerACLClient

    noxt_acl = Noxt_GeoServerACLClient(
        settings.NOXT_GEOSERVER_ACL_URL,
        settings.NOXT_GEOSERVER_ACL_USER,
        settings.NOXT_GEOSERVER_ACL_PASSWORD,
    )
    noxt_rules = [
        noxt_rule
        for noxt_rule in noxt_acl.get_rules(workspace="geonode")["rules"]
        if noxt_rule.get("layer") in noxt_test_layers
    ]
    for noxt_rule in noxt_rules:
        noxt_acl._request("DELETE", f"rules/id/{noxt_rule['id']}")

    for noxt_layer in sorted(noxt_test_layers):
        noxt_url = (
            f"{noxt_rest_base}/workspaces/geonode/datastores/{NOXT_CLOUD_TEST_STORE}/"
            f"featuretypes/{quote(noxt_layer, safe='')}.json"
        )
        noxt_response = requests.delete(noxt_url, params={"recurse": "true"}, auth=noxt_auth, timeout=30)
        if noxt_response.status_code not in (200, 202, 204, 404):
            raise RuntimeError(f"Could not delete disposable feature type {noxt_layer}: HTTP {noxt_response.status_code}")

    noxt_remaining_layers = noxt_feature_type_names(noxt_rest_base, noxt_auth, NOXT_CLOUD_TEST_STORE)
    noxt_remaining_rules = [
        noxt_rule
        for noxt_rule in noxt_acl.get_rules(workspace="geonode")["rules"]
        if noxt_rule.get("layer") in noxt_test_layers
    ]
    if noxt_remaining_layers or noxt_remaining_rules:
        raise RuntimeError(
            f"Cloud test catalog cleanup incomplete: {len(noxt_remaining_layers)} layers, "
            f"{len(noxt_remaining_rules)} ACL rules remain"
        )
    print(
        f"Cleaned {len(noxt_test_layers)} feature types and {len(noxt_rules)} ACL rules from "
        f"{NOXT_CLOUD_TEST_STORE}; persistent catalog unchanged."
    )


def noxt_drop_test_databases():
    noxt_connection = connections["default"]
    noxt_connection.ensure_connection()
    noxt_connection.set_autocommit(True)
    with noxt_connection.cursor() as noxt_cursor:
        for noxt_database in NOXT_CLOUD_TEST_DATABASES:
            noxt_cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                [noxt_database],
            )
            noxt_cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(noxt_database))
            )
        noxt_cursor.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
            [list(NOXT_CLOUD_TEST_DATABASES)],
        )
        noxt_remaining = [row[0] for row in noxt_cursor.fetchall()]
    noxt_connection.close()
    if noxt_remaining:
        raise RuntimeError(f"Disposable test databases were not removed: {', '.join(noxt_remaining)}")
    print("Removed only test_geonode and test_geonode_data, including leftover database sessions.")


def noxt_main():
    noxt_action = os.environ.get("NOXT_CLOUD_TEST_STATE_ACTION", "prepare")
    if noxt_action == "prepare":
        noxt_check_test_environment()
    elif noxt_action != "cleanup":
        raise RuntimeError("NOXT_CLOUD_TEST_STATE_ACTION must be prepare or cleanup")
    noxt_ensure_cloud_test_workspace_and_stores()
    noxt_clean_cloud_test_catalog()
    noxt_drop_test_databases()


noxt_main()
