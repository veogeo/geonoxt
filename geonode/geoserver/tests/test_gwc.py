#########################################################################
#
# Copyright (C) 2016 OSGeo
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

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings

from geoserver.catalog import FailedRequestError
from geonode.geoserver.gwc import GWCClient, noxt_get_default_gridsets
from geonode.geoserver.management.commands.gwc_subcommands.create import CreateTileLayers


class NoxtCloudGWCConfigTest(TestCase):
    def test_default_gridsets_match_cloud_catalog(self):
        with override_settings(NOXT_GEOSERVER_CLOUD_ENABLED=True):
            self.assertEqual(noxt_get_default_gridsets(), ("EPSG:4326", "EPSG:900913"))

    def test_gridsets_are_cloud_only_even_without_legacy_flag(self):
        with override_settings(NOXT_GEOSERVER_CLOUD_ENABLED=False):
            self.assertEqual(noxt_get_default_gridsets(), ("EPSG:4326", "EPSG:900913"))


    def test_noxt_gwc_create_template_uses_cloud_gridsets(self):
        with override_settings(NOXT_GEOSERVER_CLOUD_ENABLED=True):
            root = CreateTileLayers().load_xml_template()

        assert [node.text for node in root.findall(".//gridSetName")] == [
            "EPSG:4326",
            "EPSG:900913",
        ]


    def test_noxt_cloud_gwc_deployment_enables_data_security(self):
        config_path = Path(__file__).resolve().parents[3] / "docker" / "geoserver-cloud-gwc-gs.xml"
        root = ET.parse(config_path).getroot()

        assert root.findtext("securityEnabled") == "true"

class GWCClientTest(TestCase):

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_layer_calls_api(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = GWCClient()
        client.truncate_layer("test_layer1")

        self.assertEqual(mock_post.call_count, 1)

        call_kwargs = mock_post.call_args[1]
        self.assertEqual(
            call_kwargs["data"],
            "<truncateLayer><layerName>geonode:test_layer1</layerName></truncateLayer>",
        )

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_layer_preserves_workspace(self, mock_post):
        """Layer already qualified should not get default workspace prepended."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = GWCClient()
        client.truncate_layer("workspace:test_layer")

        call_kwargs = mock_post.call_args[1]
        self.assertEqual(
            call_kwargs["data"],
            "<truncateLayer><layerName>workspace:test_layer</layerName></truncateLayer>",
        )

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_layer_escapes_xml(self, mock_post):
        """Ensure XML injection is prevented via escaping."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = GWCClient()
        client.truncate_layer("layer&<>name")

        call_kwargs = mock_post.call_args[1]["data"]

        self.assertIn("layer&amp;&lt;&gt;name", call_kwargs)
        self.assertNotIn("<truncateAll>", call_kwargs)

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_layer_raises_on_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        client = GWCClient()

        with self.assertRaises(FailedRequestError):
            client.truncate_layer("test_layer")

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_all_calls_api(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        client = GWCClient()
        client.truncate_all()

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(
            mock_post.call_args[1]["data"],
            "<truncateAll/>",
        )

    @patch("geonode.geoserver.gwc.requests.post")
    def test_truncate_all_raises_on_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        client = GWCClient()

        with self.assertRaises(FailedRequestError):
            client.truncate_all()


class GwcManagementCommandTest(TestCase):

    @patch("geonode.geoserver.management.commands.gwc_subcommands.truncate.GWCClient")
    def test_management_command_layers(self, MockGWCClient):
        """Ensure multiple layers trigger truncate_layer calls."""
        call_command("gwc", "truncate", "-l", "layer1", "-l", "layer2")

        instance = MockGWCClient.return_value

        self.assertEqual(instance.truncate_layer.call_count, 2)
        instance.truncate_layer.assert_any_call("layer1")
        instance.truncate_layer.assert_any_call("layer2")

    @patch("geonode.geoserver.management.commands.gwc_subcommands.truncate.GWCClient")
    def test_management_command_all(self, MockGWCClient):
        """Ensure --all triggers truncate_all."""
        call_command("gwc", "truncate", "--all")

        MockGWCClient.return_value.truncate_all.assert_called_once()

    def test_management_command_error_missing_args(self):
        """Command must fail when no arguments are provided."""
        with self.assertRaises(CommandError) as context:
            call_command("gwc", "truncate")

        self.assertIn(
            "requires either the -l/--layer parameter(s) or the --all flag",
            str(context.exception),
        )

    def test_management_command_error_both_args(self):
        """Command must fail when both layer and --all are used."""
        with self.assertRaises(CommandError) as context:
            call_command("gwc", "truncate", "-l", "layer1", "--all")

        self.assertIn(
            "Cannot use both -l/--layer and --all at the same time",
            str(context.exception),
        )
