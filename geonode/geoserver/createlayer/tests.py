#########################################################################
#
# Copyright (C) 2017 OSGeo
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

import os
import uuid

import psycopg2
from psycopg2 import sql

from django.conf import settings
from django.db import connections
from django.urls import reverse

from geonode.tests.base import GeoNodeBaseTestSupport
from geonode import geoserver
from geonode import GeoNodeException
from geonode.layers.models import Dataset
from geonode.decorators import on_ogc_backend
from geonode.geoserver.signals import gs_catalog

from .utils import create_dataset


def noxt_cleanup_test_dataset(dataset_name):
    """Remove only a temporary layer created by CreateLayerCoreTest."""
    allowed_prefixes = ("noxt_point_dataset_", "noxt_attributes_dataset_")
    if not dataset_name.startswith(allowed_prefixes):
        raise ValueError(f"Refusing to clean non-test dataset {dataset_name!r}")
    try:
        layer = gs_catalog.get_layer(dataset_name)
        if layer is not None:
            gs_catalog.delete(layer)
    finally:
        noxt_drop_test_datastore_table(dataset_name)


def noxt_drop_test_datastore_table(table_name):
    """Drop only a uniquely named table created by this integration test."""
    database = connections["datastore"].settings_dict
    database_name = database["NAME"]
    with psycopg2.connect(
        dbname=database_name,
        user=database["USER"],
        password=database["PASSWORD"],
        host=database["HOST"],
        port=database["PORT"],
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS public.{} CASCADE").format(sql.Identifier(table_name))
            )



class CreateLayerCoreTest(GeoNodeBaseTestSupport):
    """
    Test createlayer application.
    """

    fixtures = ["initial_data.json", "bobby"]

    def setUp(self):
        super().setUp()
        # createlayer must use postgis as a datastore
        # set temporary settings to use a postgis datastore
        # DATASTORE_URL = 'postgis://geonode:geonode@localhost:5432/datastore'
        # postgis_db = dj_database_url.parse(DATASTORE_URL, conn_max_age=0)
        # settings.DATABASES['datastore'] = postgis_db
        # settings.OGC_SERVER['default']['DATASTORE'] = 'datastore'

    def tearDown(self):
        super(GeoNodeBaseTestSupport, self).tearDown()
        # move to original settings
        # settings.OGC_SERVER['default']['DATASTORE'] = ''
        # del settings.DATABASES['datastore']
        # TODO remove stuff from django and geoserver catalog

    @on_ogc_backend(geoserver.BACKEND_PACKAGE)
    def test_dataset_creation(self):
        """
        Try creating a layer.
        """
        internal_apps_tests = os.environ.get("TEST_RUN_INTERNAL_APPS") or getattr(settings, "internal_apps_tests", False)
        if internal_apps_tests:
            dataset_name = f"noxt_point_dataset_{uuid.uuid4().hex[:8]}"
            self.addCleanup(noxt_cleanup_test_dataset, dataset_name)
            dataset_title = "A layer for points"
            print(settings.DATABASES["datastore"])
            print("#######")
            print(settings.OGC_SERVER["default"]["DATASTORE"])

            create_dataset(dataset_name, dataset_title, "bobby", "Point")

            cat = gs_catalog

            # Check the layer is in the Django database
            layer = Dataset.objects.get(name=dataset_name)

            # check if it is in geoserver
            gs_dataset = cat.get_layer(dataset_name)
            try:
                self.assertIsNotNone(gs_dataset)
                self.assertEqual(gs_dataset.name, dataset_name)

                resource = gs_dataset.resource
                # Cloud exposes the generated primary key (`fid`) as well as the
                # geometry column. Check the data schema without counting the PK.
                attribute_names = set(resource.attributes)
                if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
                    self.assertEqual(attribute_names, {"fid", "the_geom"})
                else:
                    self.assertEqual(attribute_names, {"the_geom"})
                if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
                    self.assertEqual(resource.workspace.name, settings.DEFAULT_WORKSPACE)

                # check layer corrispondence between django and geoserver
                self.assertEqual(resource.title, dataset_title)
                self.assertEqual(resource.projection, layer.srid)

                # check if layer detail page is accessible with client
                response = self.client.get(reverse("dataset_embed", args=(f"{settings.DEFAULT_WORKSPACE}:{dataset_name}",)))
                self.assertEqual(response.status_code, 200)
            finally:
                cat.delete(gs_dataset)
                noxt_drop_test_datastore_table(dataset_name)

    def test_dataset_creation_with_wrong_geometry_type(self):
        """
        Try creating a layer with uncorrect geometry type.
        """
        with self.assertRaises(GeoNodeException):
            create_dataset("wrong_geom_dataset", "A layer with wrong geometry", "bobby", "wrong_geometry")

    @on_ogc_backend(geoserver.BACKEND_PACKAGE)
    def test_dataset_creation_with_attributes(self):
        """
        Try creating a layer with attributes.
        """
        internal_apps_tests = os.environ.get("TEST_RUN_INTERNAL_APPS") or getattr(settings, "internal_apps_tests", False)
        if internal_apps_tests:
            attributes = """
            {
              "field_str": "string",
              "field_int": "integer",
              "field_date": "date",
              "field_float": "float"
            }
            """

            dataset_name = f"noxt_attributes_dataset_{uuid.uuid4().hex[:8]}"
            self.addCleanup(noxt_cleanup_test_dataset, dataset_name)
            dataset_title = "A layer with attributes"

            create_dataset(dataset_name, dataset_title, "bobby", "Point", attributes)

            cat = gs_catalog
            gs_dataset = cat.get_layer(dataset_name)
            try:
                resource = gs_dataset.resource

                # Count geometry and user fields separately from the Cloud PK.
                attribute_names = set(resource.attributes)
                expected_names = {"the_geom", "field_str", "field_int", "field_date", "field_float"}
                if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
                    expected_names.add("fid")
                    self.assertEqual(resource.workspace.name, settings.DEFAULT_WORKSPACE)
                self.assertEqual(attribute_names, expected_names)
            finally:
                cat.delete(gs_dataset)
                noxt_drop_test_datastore_table(dataset_name)

    @on_ogc_backend(geoserver.BACKEND_PACKAGE)
    def test_dataset_creation_with_custom_srs(self):
        """
        Try creating a layer with a custom SRS.
        """
        internal_apps_tests = os.environ.get("TEST_RUN_INTERNAL_APPS", "False").lower() in {"true", "1", "yes"}
        if internal_apps_tests:
            dataset_name = f"noxt_point_dataset_3857_{uuid.uuid4().hex[:8]}"
            self.addCleanup(noxt_cleanup_test_dataset, dataset_name)
            dataset_title = "A layer for points 3857"
            custom_srs = "EPSG:3857"

            create_dataset(dataset_name, dataset_title, "bobby", "Point", srs=custom_srs)

            cat = gs_catalog

            # Check the layer is in the Django database
            layer = Dataset.objects.get(name=dataset_name)

            # check if it is in geoserver
            gs_dataset = cat.get_layer(dataset_name)
            try:
                self.assertIsNotNone(gs_dataset)
                self.assertEqual(gs_dataset.name, dataset_name)

                resource = gs_dataset.resource
                if getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
                    self.assertEqual(set(resource.attributes), {"fid", "the_geom"})
                else:
                    self.assertEqual(len(resource.attributes), 1)

                # check layer corrispondence between Django and GeoServer
                self.assertEqual(resource.title, dataset_title)
                self.assertEqual(resource.projection, custom_srs)
                self.assertEqual(layer.srid, custom_srs)
            finally:
                cat.delete(gs_dataset)
                noxt_drop_test_datastore_table(dataset_name)
