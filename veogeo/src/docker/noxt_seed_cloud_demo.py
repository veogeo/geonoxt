"""Create reusable manual/demo GeoNode accounts, datasets, and Cloud Bearer JWTs."""

import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from oauth2_provider.models import AccessToken, Application
from rest_framework.test import APIClient

from geonoxt.oauth import geonode_cloud_access_token_generator
from geonode.layers.models import Dataset
from geonode.resource.models import ExecutionRequest


noxt_repo = Path("/usr/src/geonode")
os.environ["GEONODE_GEODATABASE"] = "geonode_data"
noxt_user_model = get_user_model()
noxt_password = os.environ.get("NOXT_MANUAL_TEST_PASSWORD", "").strip()
if not noxt_password:
    raise CommandError("Set NOXT_MANUAL_TEST_PASSWORD in .env_test_cloud for the reusable local accounts.")
if not settings.NOXT_GEOSERVER_CLOUD_ENABLED:
    raise CommandError("The persistent demo seed requires NOXT_GEOSERVER_CLOUD_ENABLED=True.")

noxt_admin, _ = noxt_user_model.objects.get_or_create(
    username="admin",
    defaults={"email": "admin@localhost", "is_active": True, "is_staff": True, "is_superuser": True},
)
if not noxt_admin.is_superuser or not noxt_admin.is_staff:
    noxt_admin.is_superuser = True
    noxt_admin.is_staff = True
    noxt_admin.is_active = True
    noxt_admin.save(update_fields=["is_superuser", "is_staff", "is_active"])

noxt_users = {}
for noxt_username in ("noxt-bearer-test", "noxt-bearer-outsider"):
    noxt_user, noxt_created = noxt_user_model.objects.get_or_create(
        username=noxt_username,
        defaults={"email": f"{noxt_username}@example.invalid", "is_active": True},
    )
    noxt_user.is_active = True
    noxt_user.email = f"{noxt_username}@example.invalid"
    if noxt_created:
        noxt_user.set_password(noxt_password)
    noxt_user.save()
    noxt_users[noxt_username] = noxt_user

call_command("noxt_configure_geoserver_cloud_oauth", verbosity=0)
noxt_application = Application.objects.get(client_id=os.environ["NOXT_GEOSERVER_CLOUD_OIDC_CLIENT_ID"])
for noxt_username, noxt_env in (
    ("noxt-bearer-test", "NOXT_GEOSERVER_CLOUD_TEST_AUTHORIZED_TOKEN_FILE"),
    ("noxt-bearer-outsider", "NOXT_GEOSERVER_CLOUD_TEST_UNAUTHORIZED_TOKEN_FILE"),
):
    noxt_path_value = os.environ.get(noxt_env, "").strip()
    if not noxt_path_value:
        raise CommandError(f"Set {noxt_env} in .env_test_cloud.")
    noxt_path = Path(noxt_path_value)
    noxt_path.parent.mkdir(parents=True, exist_ok=True)
    AccessToken.objects.filter(user=noxt_users[noxt_username], application=noxt_application).delete()
    noxt_token = geonode_cloud_access_token_generator(
        SimpleNamespace(
            user=noxt_users[noxt_username],
            expires_in=365 * 24 * 60 * 60,
            scopes=["read", "write"],
        )
    )
    noxt_path.write_text(noxt_token + "\n", encoding="utf-8")
    noxt_path.chmod(0o600)

def noxt_ensure_production_store():
    noxt_server = settings.OGC_SERVER["default"]
    noxt_rest_base = noxt_server["LOCATION"].rstrip("/") + "/rest"
    noxt_auth = (noxt_server["USER"], noxt_server["PASSWORD"])
    noxt_workspace_url = f"{noxt_rest_base}/workspaces/geonode.json"
    noxt_workspace = requests.get(noxt_workspace_url, auth=noxt_auth, timeout=30)
    if noxt_workspace.status_code == 404:
        noxt_workspace = requests.post(
            f"{noxt_rest_base}/workspaces",
            data="<workspace><name>geonode</name></workspace>",
            headers={"Content-Type": "application/xml"},
            auth=noxt_auth,
            timeout=30,
        )
    if noxt_workspace.status_code not in (200, 201, 202):
        raise CommandError(f"Could not ensure the production workspace: HTTP {noxt_workspace.status_code} {noxt_workspace.text[:500]}")

    noxt_store_url = f"{noxt_rest_base}/workspaces/geonode/datastores/geonode_data.json"
    noxt_store = requests.get(noxt_store_url, auth=noxt_auth, timeout=30)
    if noxt_store.status_code == 200:
        return
    if noxt_store.status_code != 404:
        raise CommandError(f"Could not inspect the production datastore: HTTP {noxt_store.status_code} {noxt_store.text[:500]}")

    noxt_database = settings.DATABASES["datastore"]
    noxt_xml = ET.Element("dataStore")
    ET.SubElement(noxt_xml, "name").text = "geonode_data"
    ET.SubElement(noxt_xml, "enabled").text = "true"
    noxt_parameters = ET.SubElement(noxt_xml, "connectionParameters")
    for noxt_key, noxt_value in {
        "host": noxt_database.get("HOST") or "db",
        "port": str(noxt_database.get("PORT") or "5432"),
        "database": "geonode_data",
        "user": noxt_database["USER"],
        "passwd": noxt_database["PASSWORD"],
        "dbtype": "postgis",
        "schema": "public",
        "validate connections": "true",
    }.items():
        ET.SubElement(noxt_parameters, "entry", {"key": noxt_key}).text = str(noxt_value)
    noxt_created_store = requests.post(
        f"{noxt_rest_base}/workspaces/geonode/datastores",
        data=ET.tostring(noxt_xml, encoding="unicode"),
        headers={"Content-Type": "application/xml"},
        auth=noxt_auth,
        timeout=30,
    )
    if noxt_created_store.status_code not in (200, 201, 202):
        raise CommandError(f"Could not create the production datastore: HTTP {noxt_created_store.status_code} {noxt_created_store.text[:500]}")


def noxt_remove_misplaced_feature(noxt_name, noxt_store):
    noxt_server = settings.OGC_SERVER["default"]
    noxt_rest_base = noxt_server["LOCATION"].rstrip("/") + "/rest"
    noxt_auth = (noxt_server["USER"], noxt_server["PASSWORD"])
    noxt_url = (
        f"{noxt_rest_base}/workspaces/geonode/datastores/{noxt_store}/"
        f"featuretypes/{noxt_name}.json"
    )
    noxt_response = requests.delete(noxt_url, params={"recurse": "true"}, auth=noxt_auth, timeout=30)
    if noxt_response.status_code not in (200, 202, 204, 404):
        raise CommandError(f"Could not remove misplaced Cloud feature {noxt_name}: HTTP {noxt_response.status_code}")
    from geonode.geoserver.helpers import acl_client

    noxt_rules = acl_client.get_rules(workspace="geonode", layer=noxt_name)["rules"]
    for noxt_rule in noxt_rules:
        if noxt_rule.get("id") is not None:
            acl_client._request("DELETE", f"rules/id/{noxt_rule['id']}")


noxt_ensure_production_store()
noxt_client = APIClient()
noxt_client.force_authenticate(user=noxt_admin)
noxt_upload_url = reverse("importer_upload")
def noxt_wait_import(noxt_response):
    if noxt_response.status_code != 201:
        raise CommandError(f"Demo dataset import failed: HTTP {noxt_response.status_code} {noxt_response.content[:1000]!r}")
    noxt_exec_id = noxt_response.json().get("execution_id")
    if not noxt_exec_id:
        raise CommandError(f"Demo dataset import returned no execution id: {noxt_response.content[:1000]!r}")
    noxt_deadline = time.monotonic() + 300
    while time.monotonic() < noxt_deadline:
        noxt_execution = ExecutionRequest.objects.filter(exec_id=noxt_exec_id).first()
        if noxt_execution and noxt_execution.status == ExecutionRequest.STATUS_FINISHED:
            return
        if noxt_execution and noxt_execution.status == ExecutionRequest.STATUS_FAILED:
            raise CommandError(f"Demo dataset import failed: {noxt_execution.log or noxt_execution.status}")
        time.sleep(2)
    raise CommandError(f"Demo dataset import timed out: {noxt_exec_id}")


def noxt_set_demo_permissions(noxt_dataset):
    noxt_dataset.set_permissions(
        {
            "users": {
                "AnonymousUser": ["view_resourcebase", "download_resourcebase"],
                "admin": [
                    "view_resourcebase",
                    "change_dataset_data",
                    "change_resourcebase_permissions",
                    "download_resourcebase",
                ],
                "noxt-bearer-test": ["view_resourcebase", "change_dataset_data", "download_resourcebase"],
                "noxt-bearer-outsider": ["view_resourcebase", "download_resourcebase"],
            },
            "groups": {},
        }
    )


def noxt_ensure_dataset(noxt_name, noxt_upload, noxt_expected_store):
    noxt_dataset = Dataset.objects.filter(alternate__icontains=f"geonode:{noxt_name}").first()
    if noxt_dataset is not None and noxt_dataset.store != noxt_expected_store:
        noxt_remove_misplaced_feature(noxt_name, noxt_dataset.store)
        noxt_dataset.delete()
        noxt_dataset = None
    if noxt_dataset is None:
        with noxt_upload() as noxt_file:
            with override_settings(ASYNC_SIGNALS=False):
                noxt_response = noxt_client.post(
                    noxt_upload_url,
                    data={"base_file": noxt_file, "action": "upload"},
                )
        noxt_wait_import(noxt_response)
        noxt_dataset = Dataset.objects.filter(alternate__icontains=f"geonode:{noxt_name}").first()
    if noxt_dataset is None:
        raise CommandError(f"GeoNode did not persist the expected demo dataset {noxt_name}.")
    noxt_set_demo_permissions(noxt_dataset)
    return noxt_dataset


def noxt_vector_file():
    noxt_source = noxt_repo / "geonode/upload/tests/fixture/valid.gpkg"
    noxt_directory = Path(tempfile.mkdtemp(prefix="noxt-cloud-demo-vector-"))
    noxt_path = noxt_directory / "noxt_demo_roads.gpkg"
    subprocess.run(
        ["ogr2ogr", "-f", "GPKG", str(noxt_path), str(noxt_source), "stazioni_metropolitana", "-nln", "noxt_demo_roads"],
        check=True,
        capture_output=True,
        text=True,
    )
    return noxt_directory, noxt_path


noxt_vector_dir, noxt_vector_path = noxt_vector_file()
try:
    noxt_vector = noxt_ensure_dataset(
        "noxt_demo_roads",
        lambda: open(noxt_vector_path, "rb"),
        "geonode_data",
    )
finally:
    shutil.rmtree(noxt_vector_dir, ignore_errors=True)

noxt_raster_fixture = noxt_repo / "geonode/upload/tests/fixture/test_raster.tif"
noxt_raster_dir = Path(tempfile.mkdtemp(prefix="noxt-cloud-demo-raster-"))
noxt_raster_path = noxt_raster_dir / "noxt_demo_elevation.tif"
shutil.copy2(noxt_raster_fixture, noxt_raster_path)
try:
    noxt_raster = noxt_ensure_dataset(
        "noxt_demo_elevation",
        lambda: open(noxt_raster_path, "rb"),
        "noxt_demo_elevation",
    )
finally:
    shutil.rmtree(noxt_raster_dir, ignore_errors=True)

print("Persistent GeoNode demo data is ready: noxt_demo_roads (vector), noxt_demo_elevation (raster).")
print("Reusable test accounts are ready: noxt-bearer-test and noxt-bearer-outsider.")
