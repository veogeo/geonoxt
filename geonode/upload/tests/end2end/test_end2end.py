#########################################################################
#
# Copyright (C) 2024 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################
import ast
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import mock
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from dynamic_models.models import FieldSchema, ModelSchema
from geonode import documents
from geonode.assets.models import Asset
from geonode.base.enumerations import SOURCE_TYPE_LOCAL, SOURCE_TYPE_REMOTE
from geonode.documents.models import Document
from geonode.harvesting.models import HarvestableResource, Harvester
from geonode.resource.models import ExecutionRequest
from geonode.utils import OGC_Servers_Handler
from geoserver.catalog import Catalog
from geonode.upload import project_dir
from geonode.upload.tests.utils import ImporterBaseTestSupport
import gisdata
from django.db.models import Q
from geonode.base.models import ResourceBase
import logging
from geonode.harvesting.harvesters.wms import WebMapService
from geonode.services import enumerations
from geonode.services.models import Service
from django.forms.models import model_to_dict
from unittest import skip
import psycopg2
from psycopg2 import sql

logger = logging.getLogger()
geourl = settings.GEODATABASE_URL


@override_settings(
    FILE_UPLOAD_DIRECTORY_PERMISSIONS=0o777, FILE_UPLOAD_PERMISSIONS=0o7777, IMPORTER_ENABLE_DYN_MODELS=False
)
class BaseImporterEndToEndTest(ImporterBaseTestSupport):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.user = get_user_model().objects.exclude(username="Anonymous").first()
        cls.valid_gkpg = f"{project_dir}/tests/fixture/valid.gpkg"
        cls.valid_multiple_layers_gkpg = f"{project_dir}/tests/fixture/multiple_layers.gpkg"
        cls.valid_geojson = f"{project_dir}/tests/fixture/valid.geojson"
        cls.no_crs_gpkg = f"{project_dir}/tests/fixture/noCrsTable.gpkg"
        file_path = gisdata.PROJECT_ROOT
        filename = os.path.join(file_path, "both/good/sangis.org/Airport/Air_Runways.shp")
        cls.valid_shp = {
            "base_file": filename,
            "dbf_file": f"{file_path}/both/good/sangis.org/Airport/Air_Runways.dbf",
            "prj_file": f"{file_path}/both/good/sangis.org/Airport/Air_Runways.prj",
            "shx_file": f"{file_path}/both/good/sangis.org/Airport/Air_Runways.shx",
        }
        cls.valid_kml = f"{project_dir}/tests/fixture/valid.kml"
        cls.valid_tif = f"{project_dir}/tests/fixture/test_raster.tif"
        cls.valid_csv = f"{project_dir}/tests/fixture/valid.csv"

        cls.missing_geom_csv = f"{project_dir}/tests/fixture/missing_geom.csv"
        cls.valid_document = os.path.join(os.path.dirname(documents.__file__), "tests", "data", "img.gif")
        cls.valid_document_to_replace = os.path.join(
            os.path.dirname(documents.__file__), "tests", "data", "pdf_doc.pdf"
        )
        cls.url = reverse("importer_upload")
        ogc_server_settings = OGC_Servers_Handler(settings.OGC_SERVER)["default"]

        _user, _password = ogc_server_settings.credentials

        cls.cat = Catalog(service_url=ogc_server_settings.rest, username=_user, password=_password)

    def setUp(self) -> None:
        self.admin, _ = get_user_model().objects.get_or_create(username="admin")
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save()
        # for el in Dataset.objects.all():
        #    el.delete()

    # def tearDown(self) -> None:
    #    super().tearDown()
    #    for el in Dataset.objects.all():
    #        el.delete()

    def _wait_execution(self, response, _id="execution_id"):
        # if is async, we must wait. It will wait for 1 min before raise exception
        if ast.literal_eval(os.getenv("ASYNC_SIGNALS", "False")):
            tentative = 1
            while (
                ExecutionRequest.objects.get(exec_id=response.json().get(_id)) != ExecutionRequest.STATUS_FINISHED
                and tentative <= 15
            ):
                time.sleep(2)
                tentative += 1
        exc_obj = ExecutionRequest.objects.get(exec_id=response.json().get(_id))
        if exc_obj.status != ExecutionRequest.STATUS_FINISHED:
            raise Exception(f"Async still in progress after 1 min of waiting: {model_to_dict(exc_obj)}")

    def _cleanup_layers(self, name):
        if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            workspace = self.cat.get_workspace("geonode")
            resources = self.cat.get_resources(workspaces=[workspace]) if workspace else []
        else:
            resources = self.cat.get_resources()
        layer = [x for x in resources if name in x.name]
        if layer:
            for el in layer:
                try:
                    self.cat.delete(el)
                except:  # noqa
                    pass
        try:
            ResourceBase.objects.filter(alternate__icontains=name).delete()
        except:  # noqa
            pass

    def _noxt_create_unique_geopackage(self):
        """Copy the GeoPackage fixture with a unique layer name for Cloud runs."""
        layer_name = f"stazioni_metropolitana_{uuid.uuid4().hex[:12]}"
        directory = tempfile.mkdtemp(prefix="noxt-cloud-geopackage-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        geopackage_path = os.path.join(directory, f"{layer_name}.gpkg")
        subprocess.run(
            [
                "ogr2ogr",
                "-f",
                "GPKG",
                geopackage_path,
                self.valid_gkpg,
                "stazioni_metropolitana",
                "-nln",
                layer_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)
        return geopackage_path, layer_name

    def _noxt_create_unique_multilayer_geopackage(self):
        """Copy both layers with unique names so reruns cannot collide with Cloud data."""
        prefix = f"noxt_{uuid.uuid4().hex[:12]}"
        layer_names = (f"{prefix}_area", f"{prefix}_example")
        directory = tempfile.mkdtemp(prefix="noxt-cloud-multilayer-geopackage-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        geopackage_path = os.path.join(directory, f"{prefix}.gpkg")
        subprocess.run(
            [
                "ogr2ogr",
                "-f",
                "GPKG",
                geopackage_path,
                self.valid_multiple_layers_gkpg,
                "area",
                "-nln",
                layer_names[0],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "ogr2ogr",
                "-update",
                "-f",
                "GPKG",
                geopackage_path,
                self.valid_multiple_layers_gkpg,
                "example",
                "-nln",
                layer_names[1],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for layer_name in layer_names:
            self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)
        return geopackage_path, layer_names

    def _noxt_create_unique_csv(self):
        """Copy the CSV fixture with a unique layer name for Cloud runs."""
        layer_name = f"noxt_valid_{uuid.uuid4().hex[:12]}"
        directory = tempfile.mkdtemp(prefix="noxt-cloud-csv-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        csv_path = os.path.join(directory, f"{layer_name}.csv")
        shutil.copy2(self.valid_csv, csv_path)
        self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)
        return csv_path, layer_name

    def _noxt_create_unique_kml(self):
        """Copy the KML fixture and rename its internal feature for Cloud runs."""
        layer_name = f"noxt_sample_point_dataset_{uuid.uuid4().hex[:12]}"
        directory = tempfile.mkdtemp(prefix="noxt-cloud-kml-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        kml_path = os.path.join(directory, f"{layer_name}.kml")
        with open(self.valid_kml, encoding="utf-8") as source_file:
            kml_content = source_file.read().replace("sample_point_dataset", layer_name)
        with open(kml_path, "w", encoding="utf-8") as target_file:
            target_file.write(kml_content)
        self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)
        return kml_path, layer_name

    def _noxt_create_unique_shapefile(self):
        """Copy all shapefile components under one unique Cloud layer name."""
        layer_name = f"noxt_air_runways_{uuid.uuid4().hex[:12]}"
        directory = tempfile.mkdtemp(prefix="noxt-cloud-shapefile-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        payload = {}
        for key, source_path in self.valid_shp.items():
            _, extension = os.path.splitext(source_path)
            target_path = os.path.join(directory, f"{layer_name}{extension.lower()}")
            shutil.copy2(source_path, target_path)
            payload[key] = target_path
        self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)
        return payload, layer_name

    def _noxt_cleanup_uploaded_layer(self, layer_name):
        """Remove this test's uniquely named resource, GWC entry, and datastore table."""
        if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            # In Cloud, GWC is a separate service. Delete its tile-layer
            # configuration before deleting the REST catalog resource so its
            # catalog-reload event cannot retain a stale LayerInfo reference.
            from geonode.geoserver.security import delete_dataset_cache

            delete_dataset_cache(f"{settings.DEFAULT_WORKSPACE}:{layer_name}")
        self._cleanup_layers(layer_name)
        from geonode.geoserver.acl_rules import Noxt_ACLRuleBatch
        from geonode.geoserver.helpers import acl_client, acl_rule_manager

        acl_rules = [
            rule
            for rule in acl_client.get_rules()["rules"]
            if rule.get("workspace") == settings.DEFAULT_WORKSPACE and rule.get("layer") == layer_name
        ]
        if acl_rules:
            delete_batch = Noxt_ACLRuleBatch(f"Cleanup Cloud test layer {layer_name}")
            for rule_id in {rule["id"] for rule in acl_rules if rule.get("id") is not None}:
                delete_batch.add_delete_rule(rule_id)
            if delete_batch.length():
                acl_client.run_batch(delete_batch)

        database = connections["datastore"].settings_dict
        with psycopg2.connect(
            dbname=database["NAME"],
            user=database["USER"],
            password=database["PASSWORD"],
            host=database["HOST"],
            port=database["PORT"],
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS public.{} CASCADE").format(sql.Identifier(layer_name))
                )

    def _assertimport(
        self,
        payload,
        initial_name,
        overwrite=False,
        last_update=None,
        skip_geoserver=False,
        assert_payload=None,
        keep_resource=False,
        assert_failure=False,
        message="No handlers found for this dataset type/action",
    ):
        try:
            self.client.force_login(self.admin)
            response = self.client.post(self.url, data=payload)
            if assert_failure:
                self.assertEqual(500, response.status_code, response.json())
                self.assertListEqual([message], response.json()["errors"], response.json())
                return
            else:
                self.assertEqual(201, response.status_code, response.json())

            self._wait_execution(response)

            # check if the dynamic model is created
            if settings.IMPORTER_ENABLE_DYN_MODELS:
                _schema_id = ModelSchema.objects.filter(name__icontains=initial_name.lower().replace(" ", "_"))
                self.assertTrue(_schema_id.exists())
                schema_entity = _schema_id.first()
                self.assertTrue(FieldSchema.objects.filter(model_schema=schema_entity).exists())

                # Verify that ogr2ogr created the table with some data in it
                entries = ModelSchema.objects.filter(id=schema_entity.id).first()
                self.assertTrue(entries.as_model().objects.exists())

            # check if the geonode resource exists
            resource = ResourceBase.objects.filter(
                Q(alternate__icontains=f"geonode:{initial_name.lower().replace(' ', '_')}")
                | Q(alternate__icontains=initial_name.lower().replace(" ", "_"))
            )
            if assert_payload and assert_payload.get("sourcetype") == SOURCE_TYPE_REMOTE:
                # A remote WMS can reference a layer already present in
                # GeoNode's own workspace. Select the imported remote record,
                # not the local source layer sharing the same alternate.
                resource = resource.filter(sourcetype=SOURCE_TYPE_REMOTE)
            self.assertTrue(resource.exists())

            if not skip_geoserver:
                resources = self.cat.get_resources()
                # check if the resource is in geoserver
                self.assertTrue(resource.first().title in res for res in [y.name for y in resources])
            if overwrite:
                self.assertTrue(resource.first().last_updated > last_update)

            if assert_payload:
                target = resource.first()
                target.refresh_from_db()
                for key, value in assert_payload.items():
                    if hasattr(target, key):
                        self.assertEqual(getattr(target, key), value)
                    else:
                        logger.warning(f"The attribute {key} doesn't belong to the resource.")
            if keep_resource:
                return resource.first()
        finally:
            if not keep_resource:
                resource = ResourceBase.objects.filter(
                    Q(alternate__icontains=f"geonode:{initial_name}") | Q(alternate__icontains=initial_name)
                )
                if assert_payload and assert_payload.get("sourcetype") == SOURCE_TYPE_REMOTE:
                    resource = resource.filter(sourcetype=SOURCE_TYPE_REMOTE)
                resource.delete()


class ImporterGeoPackageImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_geopackage(self):
        geopackage_path, initial_name = self._noxt_create_unique_geopackage()
        payload = {"base_file": open(geopackage_path, "rb"), "action": "upload"}
        self.addCleanup(payload["base_file"].close)
        self._assertimport(payload, initial_name)

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_gpkg_overwrite(self):
        geopackage_path, initial_name = self._noxt_create_unique_geopackage()
        payload = {"base_file": open(geopackage_path, "rb"), "action": "upload"}
        self.addCleanup(payload["base_file"].close)
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)

        payload = {"base_file": open(geopackage_path, "rb"), "action": "replace"}
        self.addCleanup(payload["base_file"].close)
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated)

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(
        FILE_UPLOAD_PERMISSIONS=None,
        ASYNC_SIGNALS=False,
        GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data",
    )
    def test_file_upload_permissions_none_uploads_successfully(self):
        """
        Test that setting FILE_UPLOAD_PERMISSIONS=None prevents chmod errors during file upload.
        This is important for compatibility with volume types (e.g., Azure Files) that disallow chmod.
        """
        geopackage_path, initial_name = self._noxt_create_unique_geopackage()
        payload = {"base_file": open(geopackage_path, "rb"), "action": "upload"}
        self.addCleanup(payload["base_file"].close)

        self._assertimport(payload, initial_name)


class ImporterMultupleGeoPackageImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_multiple_layers_from_geopackage(self):
        geopackage_path, (area_name, example_name) = self._noxt_create_unique_multilayer_geopackage()
        try:
            resource = None
            payload = {"base_file": open(geopackage_path, "rb"), "action": "upload"}
            self.addCleanup(payload["base_file"].close)
            self._assertimport(payload, area_name)
            resource = ResourceBase.objects.filter(
                Q(alternate__icontains=f"geonode:{example_name}") | Q(alternate__icontains=example_name)
            )
            self.assertTrue(resource.exists())
        finally:
            if resource.first():
                resource.first().delete()


    @override_settings(ASYNC_SIGNALS=False)
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    @mock.patch("geonode.upload.handlers.common.vector.BaseVectorFileHandler._select_valid_layers")
    @override_settings(MEDIA_ROOT="/tmp/", ASSETS_ROOT="/tmp/")
    def test_import_geopackage_with_no_crs_table_should_raise_error_if_all_layer_are_invalid(
        self, _select_valid_layers
    ):
        _select_valid_layers.return_value = []

        self._cleanup_layers(name="mattia_test")
        payload = {"base_file": open(self.no_crs_gpkg, "rb"), "store_spatial_file": True, "action": "upload"}

        with self.assertLogs(level="ERROR") as _log:
            self.client.force_login(self.admin)

            response = self.client.post(self.url, data=payload)
            self.assertEqual(500, response.status_code)

        self.assertIn(
            "No valid layers found",
            [x.message for x in _log.records],
        )

        self._cleanup_layers(name="mattia_test")


class ImporterGeoJsonImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_geojson(self):

        self._cleanup_layers(name="valid")

        payload = {"base_file": open(self.valid_geojson, "rb"), "action": "upload"}
        initial_name = "valid"
        self._assertimport(payload, initial_name)

        self._cleanup_layers(name="valid")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_geojson_overwrite(self):
        self._cleanup_layers(name="valid")
        payload = {"base_file": open(self.valid_geojson, "rb"), "action": "upload"}
        initial_name = "valid"
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)
        payload = {"base_file": open(self.valid_geojson, "rb"), "action": "replace"}
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated)

        self._cleanup_layers(name="valid")


class ImporterGCSVImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_csv(self):
        if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            csv_path, initial_name = self._noxt_create_unique_csv()
        else:
            self._cleanup_layers(name="valid")
            csv_path, initial_name = self.valid_csv, "valid"
        payload = {"base_file": open(csv_path, "rb"), "action": "upload"}
        self._assertimport(payload, initial_name)
        if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            self._cleanup_layers(name="valid")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_csv_overwrite(self):
        self._cleanup_layers(name="valid")
        payload = {"base_file": open(self.valid_csv, "rb"), "action": "upload"}
        initial_name = "valid"
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)

        payload = {"base_file": open(self.valid_csv, "rb"), "action": "replace"}
        initial_name = "valid"
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated)
        self._cleanup_layers(name="valid")


class ImporterGCSVTabularImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_tabular_csv(self):
        self._cleanup_layers(name="missing_geom")

        payload = {"base_file": open(self.missing_geom_csv, "rb"), "action": "upload"}
        initial_name = "missing_geom"
        self._assertimport(
            payload,
            initial_name,
            assert_payload={
                "subtype": "tabular",
                "resource_type": "dataset",
            },
        )
        self._cleanup_layers(name="missing_geom")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_tabular_csv_overwrite(self):
        self._cleanup_layers(name="missing_geom")
        payload = {"base_file": open(self.missing_geom_csv, "rb"), "action": "upload"}
        initial_name = "missing_geom"
        prev_dataset = self._assertimport(
            payload,
            initial_name,
            keep_resource=True,
            assert_payload={
                "subtype": "tabular",
                "resource_type": "dataset",
            },
        )

        payload = {"base_file": open(self.missing_geom_csv, "rb"), "action": "replace"}
        initial_name = "missing_geom"
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated)
        self._cleanup_layers(name="missing_geom")


class ImporterKMLImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_kml(self):
        if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            kml_path, initial_name = self._noxt_create_unique_kml()
        else:
            self._cleanup_layers(name="sample_point_dataset")
            kml_path, initial_name = self.valid_kml, "sample_point_dataset"
        payload = {"base_file": open(kml_path, "rb"), "action": "upload"}
        self._assertimport(payload, initial_name)
        if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            self._cleanup_layers(name="sample_point_dataset")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_kml_overwrite(self):
        initial_name = "sample_point_dataset"

        self._cleanup_layers(name="sample_point_dataset")
        payload = {"base_file": open(self.valid_kml, "rb"), "action": "upload"}
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)

        payload = {"base_file": open(self.valid_kml, "rb"), "action": "replace"}
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(
            payload,
            initial_name,
            overwrite=True,
            last_update=prev_dataset.last_updated,
            assert_failure=True,
            message="The requested action is not implemented yet",
        )
        self._cleanup_layers(name="sample_point_dataset")


class ImporterShapefileImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_shapefile(self):
        if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            shapefile_paths, initial_name = self._noxt_create_unique_shapefile()
        else:
            self._cleanup_layers(name="air_Runways")
            shapefile_paths, initial_name = self.valid_shp, "air_Runways"
        payload = {_filename: open(_file, "rb") for _filename, _file in shapefile_paths.items()}
        payload["action"] = "upload"
        self._assertimport(payload, initial_name)
        if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            self._cleanup_layers(name="air_Runways")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_shapefile_overwrite(self):

        self._cleanup_layers(name="air_Runways")
        payload = {_filename: open(_file, "rb") for _filename, _file in self.valid_shp.items()}
        payload["action"] = "upload"
        initial_name = "air_Runways"
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)
        payload = {_filename: open(_file, "rb") for _filename, _file in self.valid_shp.items()}
        payload["resource_pk"] = prev_dataset.pk
        payload["action"] = "replace"
        self._assertimport(
            payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated, keep_resource=True
        )
        self._cleanup_layers(name="air_Runways")


class ImporterDocumentImportTest(BaseImporterEndToEndTest):
    """
    A document is not published on GeoServer and has no alternate,
    so the assertions are done on the Document itself
    """

    def _assertdocumentimport(self, payload, title, assert_payload=None, keep_resource=False):
        try:
            self.client.force_login(self.admin)

            response = self.client.post(self.url, data=payload)
            self.assertEqual(201, response.status_code, response.json())

            self._wait_execution(response)

            document = Document.objects.filter(title=title)
            self.assertTrue(document.exists())
            document = document.first()

            # the handler must be tracked, is used by the replace and the clone
            self.assertEqual(
                "geonode.upload.handlers.document.handler.DocumentFileHandler",
                document.resourcehandlerinfo_set.first().handler_module_path,
            )

            for key, value in (assert_payload or {}).items():
                self.assertEqual(value, getattr(document, key))

            if keep_resource:
                return document
        finally:
            if not keep_resource:
                Document.objects.filter(title=title).delete()

    @mock.patch.dict(os.environ, {"ASYNC_SIGNALS": "False"})
    @override_settings(ASYNC_SIGNALS=False)
    def test_import_document(self):
        payload = {"base_file": open(self.valid_document, "rb"), "action": "document_upload"}
        assert_payload = {
            "extension": "gif",
            "resource_type": "document",
            "sourcetype": SOURCE_TYPE_LOCAL,
        }
        try:
            document = self._assertdocumentimport(payload, "img.gif", assert_payload=assert_payload, keep_resource=True)
            self.assertTrue(document.files[0].endswith("img.gif"))
        finally:
            Document.objects.filter(title="img.gif").delete()

    @mock.patch.dict(os.environ, {"ASYNC_SIGNALS": "False"})
    @override_settings(ASYNC_SIGNALS=False)
    def test_import_remote_document(self):
        payload = {
            "url": "https://example.org/documents/report.pdf",
            "title": "Remote document",
            "action": "document_upload",
        }
        assert_payload = {
            "doc_url": "https://example.org/documents/report.pdf",
            "extension": "pdf",
            "resource_type": "document",
            "sourcetype": SOURCE_TYPE_REMOTE,
        }
        self._assertdocumentimport(payload, "Remote document", assert_payload=assert_payload)

    @mock.patch.dict(os.environ, {"ASYNC_SIGNALS": "False"})
    @override_settings(ASYNC_SIGNALS=False)
    def test_import_document_replace(self):
        payload = {"base_file": open(self.valid_document, "rb"), "action": "document_upload"}
        document = self._assertdocumentimport(payload, "img.gif", keep_resource=True)
        try:
            payload = {
                "base_file": open(self.valid_document_to_replace, "rb"),
                "resource_pk": document.pk,
                "action": "document_replace",
            }
            self.client.force_login(self.admin)

            response = self.client.post(self.url, data=payload)
            self.assertEqual(201, response.status_code, response.json())

            self._wait_execution(response)

            document.refresh_from_db()
            # the metadata are preserved, only the file is replaced
            self.assertEqual("img.gif", document.title)
            self.assertEqual("pdf", document.extension)
            self.assertEqual(1, Asset.objects.filter(link__resource=document).count())
            self.assertTrue(document.files[0].endswith("pdf_doc.pdf"))
        finally:
            Document.objects.filter(pk=document.pk).delete()


class ImporterRasterImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(
        GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data",
        ASYNC_SIGNALS=False,
        GEOSERVER_SECURITY_ENABLED=True,
        NOXT_GEOSERVER_ACL_ENABLED=True,
    )
    def test_noxt_cloud_raster_is_readable_through_wcs(self):
        if not settings.NOXT_GEOSERVER_CLOUD_ENABLED:
            self.skipTest("This integration test targets GeoServer Cloud WCS.")

        from xml.etree import ElementTree

        layer_name = f"noxt_wcs_raster_{uuid.uuid4().hex[:12]}"
        directory = tempfile.mkdtemp(prefix="noxt-cloud-wcs-raster-")
        raster_path = os.path.join(directory, f"{layer_name}.tif")
        shutil.copy2(self.valid_tif, raster_path)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.addCleanup(self._noxt_cleanup_uploaded_layer, layer_name)

        raster_file = open(raster_path, "rb")
        self.addCleanup(raster_file.close)
        resource = self._assertimport(
            {"base_file": raster_file, "action": "upload"},
            layer_name,
            keep_resource=True,
        )
        resource.set_permissions(
            {
                "users": {
                    "AnonymousUser": ["view_resourcebase", "download_resourcebase"],
                    "admin": [
                        "view_resourcebase",
                        "change_resourcebase_permissions",
                        "download_resourcebase",
                        "publish_resourcebase",
                        "change_resourcebase_metadata",
                    ],
                },
                "groups": {},
            }
        )
        from geonode.geoserver.helpers import acl_client

        acl_rules = acl_client.get_rules()["rules"]
        self.assertTrue(
            any(
                rule.get("workspace") == settings.DEFAULT_WORKSPACE
                and rule.get("layer") == layer_name
                and rule.get("service") == "WCS"
                and rule.get("access") == "ALLOW"
                for rule in acl_rules
            ),
            f"GeoNode did not synchronize an anonymous WCS allow rule for {layer_name}.",
        )
        # WCS 2.0.1 encodes the workspace separator as a double underscore.
        coverage_id = f"{settings.DEFAULT_WORKSPACE}__{layer_name}"
        wcs_url = f"{settings.GEOSERVER_LOCATION}wcs"

        coverage_ids = []
        last_capabilities_error = None
        noxt_catalog_deadline = time.monotonic() + 45
        while time.monotonic() < noxt_catalog_deadline:
            try:
                capabilities = requests.get(
                    wcs_url,
                    params={"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"},
                    timeout=10,
                )
                self.assertEqual(200, capabilities.status_code, capabilities.text[:1000])
                root = ElementTree.fromstring(capabilities.content)
                coverage_ids = [
                    element.text
                    for element in root.iter()
                    if element.tag.rsplit("}", 1)[-1] == "CoverageId" and element.text
                ]
                if coverage_id in coverage_ids:
                    break
                last_capabilities_error = None
            except Exception as exc:
                last_capabilities_error = exc
            time.sleep(1)
        self.assertIn(
            coverage_id,
            coverage_ids,
            f"WCS did not advertise {coverage_id}; last error: {last_capabilities_error}",
        )

        coverage = requests.get(
            wcs_url,
            params={
                "service": "WCS",
                "request": "GetCoverage",
                "version": "2.0.1",
                "coverageId": coverage_id,
                "format": "image/tiff",
            },
            timeout=60,
        )
        self.assertEqual(200, coverage.status_code, coverage.text[:1000])
        self.assertTrue(
            coverage.content.startswith((b"II*\x00", b"MM\x00*")),
            f"WCS did not return a TIFF for {resource.alternate}: {coverage.headers.get('Content-Type')}",
        )

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_raster(self):
        self._cleanup_layers(name="test_raster")

        payload = {"base_file": open(self.valid_tif, "rb"), "action": "upload"}
        initial_name = "test_raster"
        self._assertimport(payload, initial_name)
        self._cleanup_layers(name="test_raster")

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data")
    def test_import_raster_overwrite(self):
        initial_name = "test_raster"

        self._cleanup_layers(name="test_raster")
        payload = {"base_file": open(self.valid_tif, "rb"), "action": "upload"}
        prev_dataset = self._assertimport(payload, initial_name, keep_resource=True)

        payload = {"base_file": open(self.valid_tif, "rb"), "action": "replace"}
        initial_name = "test_raster"
        payload["resource_pk"] = prev_dataset.pk
        self._assertimport(payload, initial_name, overwrite=True, last_update=prev_dataset.last_updated)
        self._cleanup_layers(name="test_raster")


class Importer3dTilesImportTest(BaseImporterEndToEndTest):
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data", ASYNC_SIGNALS=False)
    def test_import_3dtiles(self):
        payload = {
            "url": "https://raw.githubusercontent.com/CesiumGS/3d-tiles-samples/main/1.1/TilesetWithFullMetadata/tileset.json",
            "title": "Remote Title",
            "type": "3dtiles",
            "action": "upload",
        }
        initial_name = "remote_title"
        assert_payload = {
            "subtype": "3dtiles",
            "title": "Remote Title",
            "resource_type": "dataset",
        }
        self._assertimport(payload, initial_name, skip_geoserver=True, assert_payload=assert_payload)

    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data", ASYNC_SIGNALS=False)
    def test_import_3dtiles_overwrite(self):
        payload = {
            "url": "https://raw.githubusercontent.com/CesiumGS/3d-tiles-samples/main/1.1/TilesetWithFullMetadata/tileset.json",
            "title": "Remote Title",
            "type": "3dtiles",
            "action": "upload",
        }
        initial_name = "remote_title"
        assert_payload = {
            "subtype": "3dtiles",
            "title": "Remote Title",
            "resource_type": "dataset",
        }
        # Lets import the resource first but without deleting it
        resource = self._assertimport(
            payload,
            initial_name,
            skip_geoserver=True,
            keep_resource=True,
            assert_payload=assert_payload,
        )
        prev_timestamp = resource.last_updated
        # let's re-import it again with the overwrite mode activated
        assert_payload = {
            "subtype": "3dtiles",
            "resource_type": "dataset",
        }
        payload["action"] = "replace"
        self._assertimport(
            payload,
            initial_name,
            skip_geoserver=True,
            overwrite=True,
            assert_payload=assert_payload,
            last_update=prev_timestamp,
            assert_failure=True,
        )


class ImporterWMSImportTest(BaseImporterEndToEndTest):
    def noxt_geonode_wms_source(self):
        """Publish a temporary source in GeoNode's workspace for remote-WMS tests."""
        geopackage_path, source_name = self._noxt_create_unique_geopackage()
        base_file = open(geopackage_path, "rb")
        self.addCleanup(base_file.close)
        source = self._assertimport(
            {"base_file": base_file, "action": "upload"},
            source_name,
            keep_resource=True,
        )
        source.set_permissions(
            {
                "users": {
                    "AnonymousUser": ["view_resourcebase", "download_resourcebase"],
                    "admin": [
                        "view_resourcebase",
                        "change_resourcebase_permissions",
                        "download_resourcebase",
                        "publish_resourcebase",
                        "change_resourcebase_metadata",
                    ],
                },
                "groups": {},
            }
        )
        from geonode.geoserver.helpers import acl_client

        all_acl_rules = acl_client.get_rules()["rules"]
        acl_rules = [
            rule
            for rule in all_acl_rules
            if rule.get("workspace") == "geonode" and rule.get("layer") == source_name
        ]
        self.assertTrue(
            any(rule.get("service") == "WMS" and rule.get("access") == "ALLOW" for rule in acl_rules),
            f"GeoNode did not synchronize an anonymous WMS allow rule to Cloud ACL: {acl_rules}",
        )
        source.title = "Noxt Remote WMS Source"
        source.save(update_fields=["title"])
        # Keep the temporary Cloud layer's advertised title in sync with the
        # GeoNode fixture so the imported remote resource is distinguishable.
        cloud_resource = self.cat.get_resource(name=source_name, workspace="geonode")
        cloud_resource.title = source.title
        self.cat.save(cloud_resource)
        service_url = (
            f"{settings.OGC_SERVER['default']['LOCATION'].replace('gateway:8080', 'gateway.geonode.internal:8080').rstrip('/')}/geonode/ows"
        )
        matching_layer_names = []
        wms = None
        last_capabilities_error = None
        noxt_catalog_deadline = time.monotonic() + 45
        while time.monotonic() < noxt_catalog_deadline:
            try:
                # GeoNode's WebMapService factory is LRU-cached by URL. Clear it here
                # so a previous test's GetCapabilities document cannot mask this upload.
                WebMapService.cache_clear()
                _, wms = WebMapService(
                    f"{service_url}?service=WMS&version=1.3.0&request=GetCapabilities",
                    timeout=3,
                )
                matching_layer_names = [name for name in wms.contents if name.rsplit(":", 1)[-1] == source_name]
                if matching_layer_names:
                    break
                last_capabilities_error = None
            except Exception as exc:  # Cloud service discovery can briefly return a gateway error.
                last_capabilities_error = exc
            time.sleep(1)
        self.assertTrue(
            matching_layer_names,
            f"Expected Cloud WMS layer {source_name}; available layers: "
            f"{list(wms.contents) if wms else 'unavailable'}; "
            f"last GetCapabilities error: {last_capabilities_error}",
        )
        layer_name = matching_layer_names[0]
        layer = wms[layer_name]
        image = wms.getmap(
            layers=[layer_name],
            srs="EPSG:4326",
            bbox=tuple(layer.boundingBoxWGS84),
            size=(64, 64),
            format="image/png",
            transparent=True,
        )
        self.assertTrue(image.read().startswith(b"\x89PNG\r\n\x1a\n"), "Cloud WMS GetMap did not return a PNG")
        from geonode.thumbs.utils import get_map

        west, south, east, north = layer.boundingBoxWGS84
        thumbnail = get_map(
            service_url,
            [layer_name],
            [west, east, south, north, "EPSG:4326"],
            width=64,
            height=64,
            max_retries=1,
        )
        self.assertTrue(thumbnail, "GeoNode's thumbnail GetMap request did not return an image")
        internal_thumbnail = get_map(
            None,
            [layer_name],
            [west, east, south, north, "EPSG:4326"],
            width=64,
            height=64,
            max_retries=1,
        )
        self.assertTrue(internal_thumbnail, "GeoNode's internal Cloud GetMap request did not return an image")
        return source, source_name, service_url

    @mock.patch("geonode.thumbs.background.OSMTileBackground.fetch", new=mock.Mock(return_value=None))
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(
        GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data",
        ASYNC_SIGNALS=False,
        GEOSERVER_SECURITY_ENABLED=True,
        NOXT_GEOSERVER_ACL_ENABLED=True,
        SAFE_URL_TRUSTED_HOSTS=["gateway.geonode.internal:8080"],
    )
    def test_import_wms(self):
        source, source_name, service_url = self.noxt_geonode_wms_source()
        _, wms = WebMapService(f"{service_url}?service=WMS&version=1.3.0&request=GetCapabilities", timeout=5)
        matching_layers = [name for name in wms.contents if name.rsplit(":", 1)[-1] == source_name]
        self.assertTrue(matching_layers, f"Expected {source_name} in GeoNode WMS capabilities: {list(wms.contents)}")
        resource_to_take = matching_layers[0]
        res = wms[resource_to_take]
        payload = {
            "url": f"{service_url}?service=WMS&version=1.3.0&request=GetCapabilities",
            "title": res.title,
            "type": "wms",
            "identifier": resource_to_take,
            "parse_remote_metadata": True,
            "action": "upload",
        }
        # The source and imported records share the remote service's
        # alternate, so the helper filters by REMOTE sourcetype.
        initial_name = resource_to_take.rsplit(":", 1)[-1]
        assert_payload = {
            "subtype": "remote",
            "title": res.title,
            "resource_type": "dataset",
            "sourcetype": "REMOTE",
            "ptype": "gxp_wmscsource",
        }
        self._assertimport(payload, initial_name, skip_geoserver=True, assert_payload=assert_payload)

    @mock.patch("geonode.thumbs.background.OSMTileBackground.fetch", new=mock.Mock(return_value=None))
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(
        GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data",
        ASYNC_SIGNALS=False,
        GEOSERVER_SECURITY_ENABLED=True,
        NOXT_GEOSERVER_ACL_ENABLED=True,
        SAFE_URL_TRUSTED_HOSTS=["gateway.geonode.internal:8080"],
    )
    def test_import_wms_harvestable_resource_should_be_created(self):
        """
        The WMS import should generate the harvestable resource if the remote service URL
        match with an exists harvester, so next time it will be managed by the harvester itself
        """

        source = None
        source_name = None
        harvester = None
        try:
            # Use a temporary GeoNode layer as the remote WMS source. The Cloud
            # sample workspaces are intentionally outside this integration test.
            source, source_name, service_url = self.noxt_geonode_wms_source()
            _, wms = WebMapService(
                f"{service_url}?service=WMS&version=1.3.0&request=GetCapabilities", timeout=5
            )
            matching_layers = [name for name in wms.contents if name.rsplit(":", 1)[-1] == source_name]
            self.assertTrue(matching_layers, f"Expected {source_name} in GeoNode WMS capabilities: {list(wms.contents)}")
            resource_to_take = matching_layers[0]
            res = wms[resource_to_take]
            geoserver = service_url
            harvester = Harvester.objects.create(
                remote_url=geoserver,
                name="Test",
                default_owner=self.user,
                harvester_type="geonode.harvesting.harvesters.wms.OgcWmsHarvester",
            )
            Service.objects.create(
                owner=self.admin,
                title="Test",
                type=enumerations.WMS,
                method=enumerations.HARVESTED,
                base_url=geoserver,
                name="test-wms-import-service",
                harvester=harvester,
            )
            payload = {
                "url": f"{service_url}?service=WMS&version=1.3.0&request=GetCapabilities",
                "title": res.title,
                "type": "wms",
                "identifier": resource_to_take,
                "parse_remote_metadata": True,
                "action": "upload",
            }
            initial_name = resource_to_take.rsplit(":", 1)[-1]
            assert_payload = {
                "subtype": "remote",
                "title": res.title,
                "resource_type": "dataset",
                "sourcetype": "REMOTE",
                "ptype": "gxp_wmscsource",
            }
            res = self._assertimport(
                payload, initial_name, skip_geoserver=True, assert_payload=assert_payload, keep_resource=True
            )

            # check if the harvestabel resource has been created

            obj = HarvestableResource.objects.filter(geonode_resource=res)

            self.assertTrue(obj.exists())
        except Exception as e:
            raise e
        finally:
            if harvester:
                harvester.delete()

    @mock.patch("geonode.thumbs.background.OSMTileBackground.fetch", new=mock.Mock(return_value=None))
    @mock.patch.dict(os.environ, {"GEONODE_GEODATABASE": "test_geonode_data", "ASYNC_SIGNALS": "False"})
    @override_settings(
        GEODATABASE_URL=f"{geourl.split('/geonode_data')[0]}/test_geonode_data",
        ASYNC_SIGNALS=False,
        GEOSERVER_SECURITY_ENABLED=True,
        NOXT_GEOSERVER_ACL_ENABLED=True,
        SAFE_URL_TRUSTED_HOSTS=["gateway.geonode.internal:8080"],
    )
    def test_noxt_private_layer_bearer_permissions(self):
        """Verify real WMS/WFS Bearer access and denial for a private Cloud layer."""
        if not settings.NOXT_GEOSERVER_CLOUD_ENABLED:
            self.skipTest("This integration test targets GeoServer Cloud ACL.")

        from geonode.geoserver.helpers import _create_acl_client

        # This test is about Cloud's Bearer authorization, not Django's
        # session-cookie middleware. APIClient avoids the test server's
        # session-cookie behavior while preserving the authenticated uploader.
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        source, layer_name, service_url = self.noxt_geonode_wms_source()
        self.addCleanup(source.delete)
        user_model = get_user_model()
        authorized_user, _ = user_model.objects.get_or_create(
            username="noxt-bearer-test",
            defaults={"email": "noxt-bearer-test@example.invalid", "is_active": True},
        )
        unauthorized_user, _ = user_model.objects.get_or_create(
            username="noxt-bearer-outsider",
            defaults={"email": "noxt-bearer-outsider@example.invalid", "is_active": True},
        )

        source.set_permissions(
            {
                "users": {
                    "AnonymousUser": [],
                    authorized_user.username: ["view_resourcebase", "download_resourcebase"],
                },
                "groups": {},
            }
        )

        acl_rules = _create_acl_client().get_rules(workspace="geonode", layer=layer_name)["rules"]
        for service in ("WMS", "WFS"):
            self.assertTrue(
                any(
                    rule.get("service") in (service, None)
                    and rule.get("access") == "ALLOW"
                    and rule.get("user") == authorized_user.username
                    for rule in acl_rules
                ),
                f"GeoNode did not synchronize {authorized_user.username}'s {service} allow rule to Cloud ACL: {acl_rules}",
            )
            self.assertFalse(
                any(
                    rule.get("service") in (service, None)
                    and rule.get("access") == "ALLOW"
                    and rule.get("user") is None
                    and rule.get("role") is None
                    for rule in acl_rules
                ),
                f"Cloud ACL still allows anonymous {service} access to the private layer: {acl_rules}",
            )

        authorized_token_file = os.environ.get("NOXT_GEOSERVER_CLOUD_TEST_AUTHORIZED_TOKEN_FILE")
        unauthorized_token_file = os.environ.get("NOXT_GEOSERVER_CLOUD_TEST_UNAUTHORIZED_TOKEN_FILE")
        self.assertTrue(authorized_token_file, "Set NOXT_GEOSERVER_CLOUD_TEST_AUTHORIZED_TOKEN_FILE for isolated introspection.")
        self.assertTrue(unauthorized_token_file, "Set NOXT_GEOSERVER_CLOUD_TEST_UNAUTHORIZED_TOKEN_FILE for isolated introspection.")
        authorized_token = open(authorized_token_file, encoding="utf-8").read().strip()
        unauthorized_token = open(unauthorized_token_file, encoding="utf-8").read().strip()
        self.assertTrue(authorized_token, "Authorized test token file is empty.")
        self.assertTrue(unauthorized_token, "Unauthorized test token file is empty.")
        wms_params = {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": f"geonode:{layer_name}",
            "styles": "",
            "srs": "EPSG:4326",
            "bbox": ",".join(map(str, source.ll_bbox_polygon.extent)),
            "width": 64,
            "height": 64,
            "format": "image/png",
        }
        wfs_params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"geonode:{layer_name}",
            "count": 1,
            "outputFormat": "application/json",
        }

        allowed_wms = requests.get(
            service_url,
            params=wms_params,
            headers={"Authorization": f"Bearer {authorized_token}"},
            timeout=30,
        )
        self.assertEqual(200, allowed_wms.status_code, allowed_wms.text[:1000])
        self.assertEqual("image/png", allowed_wms.headers.get("Content-Type", "").split(";", 1)[0])

        allowed_wfs = requests.get(
            service_url,
            params=wfs_params,
            headers={"Authorization": f"Bearer {authorized_token}"},
            timeout=30,
        )
        self.assertEqual(200, allowed_wfs.status_code, allowed_wfs.text[:1000])
        self.assertTrue(allowed_wfs.json().get("features"), allowed_wfs.text[:1000])

        for label, headers in (
            ("anonymous", {}),
            ("unauthorized user", {"Authorization": f"Bearer {unauthorized_token}"}),
        ):
            for service_name, params in (("WMS", wms_params), ("WFS", wfs_params)):
                response = requests.get(service_url, params=params, headers=headers, timeout=30)
                if response.status_code in (401, 403):
                    continue

                # OGC services may return HTTP 200 with a protocol-level
                # exception. Treat that as denial only when no protected data
                # is returned.
                self.assertEqual(
                    200,
                    response.status_code,
                    f"{label} {service_name} access should be denied; got {response.status_code}: {response.text[:1000]}",
                )
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                if service_name == "WMS":
                    self.assertNotEqual(
                        "image/png",
                        content_type,
                        f"{label} WMS received a rendered private layer.",
                    )
                    self.assertRegex(
                        response.text,
                        r"ServiceException|ExceptionReport|LayerNotDefined|AccessDenied",
                        f"{label} WMS returned neither a denial status nor an OGC exception: {response.text[:1000]}",
                    )
                elif content_type == "application/json":
                    self.assertFalse(
                        response.json().get("features"),
                        f"{label} WFS returned private features: {response.text[:1000]}",
                    )
                else:
                    self.assertRegex(
                        response.text,
                        r"ServiceException|ExceptionReport|LayerNotDefined|AccessDenied",
                        f"{label} WFS returned neither a denial status nor an OGC exception: {response.text[:1000]}",
                    )
