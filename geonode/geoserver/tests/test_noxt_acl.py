from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from geonode.geoserver.acl_rules import Noxt_ACLRule, Noxt_ACLRuleBatch, Noxt_ACLAutoPriorityBatch
from geonode.geoserver.noxt_acl import Noxt_GeoServerACLClient, Noxt_GeoServerACLException
from geonode.geoserver.helpers import _create_acl_client
from geonode.geoserver.management.commands.set_all_datasets_public import Command as SetAllDatasetsPublicCommand


class NoxtACLClientTest(SimpleTestCase):
    def test_acl_rule_uses_native_acl_fields_and_group_role(self):
        rule = Noxt_ACLRule(
            Noxt_ACLRule.LIMIT,
            priority=4,
            workspace="geonode",
            layer="private",
            group="editors",
            service="WMS",
            geo_limit="POLYGON((0 0,1 0,1 1,0 0))",
            catalog_mode=Noxt_ACLRule.CM_MIXED,
        )
        assert rule.fields == {
            "priority": 4,
            "role": "ROLE_EDITORS",
            "service": "WMS",
            "workspace": "geonode",
            "layer": "private",
            "access": "LIMIT",
            "limits": {
                "allowedArea": {"wkt": "POLYGON((0 0,1 0,1 1,0 0))"},
                "spatialFilterType": "INTERSECT",
                "catalogMode": "MIXED",
            },
        }


    @override_settings(
        NOXT_GEOSERVER_ACL_URL="http://acl:8080/acl/",
        NOXT_GEOSERVER_ACL_USER="admin",
        NOXT_GEOSERVER_ACL_PASSWORD="test-password",
    )
    def test_geonode_builds_the_acl_client(self):
        client = _create_acl_client()
        assert isinstance(client, Noxt_GeoServerACLClient)
        assert client.noxt_api_url == "http://acl:8080/acl/api/"
        assert client.noxt_timeout > 0

class NoxtSetAllDatasetsPublicCommandTest(SimpleTestCase):
    @override_settings(GEOSERVER_SECURITY_ENABLED=True)
    def test_command_uses_resource_permissions(self):
        dataset = Mock(name="private_dataset")
        dataset.name = "private_dataset"
        dataset.owner = "alice"
        with patch(
            "geonode.geoserver.management.commands.set_all_datasets_public.Dataset.objects.all", return_value=[dataset]
        ):
            SetAllDatasetsPublicCommand().handle()
        dataset.set_default_permissions.assert_called_once_with()
        permission_spec = dataset.set_permissions.call_args.args[0]
        self.assertEqual(permission_spec["users"]["AnonymousUser"], ["view_resourcebase", "download_resourcebase"])
        self.assertIn("admin", permission_spec["users"])

    @override_settings(GEOSERVER_SECURITY_ENABLED=True)
    def test_command_reports_acl_sync_failures(self):
        dataset = Mock(name="private_dataset")
        dataset.name = "private_dataset"
        dataset.owner = "alice"
        dataset.set_permissions.side_effect = RuntimeError("ACL service unavailable")
        with patch(
            "geonode.geoserver.management.commands.set_all_datasets_public.Dataset.objects.all", return_value=[dataset]
        ):
            with self.assertRaisesRegex(CommandError, "private_dataset"):
                SetAllDatasetsPublicCommand().handle()


class NoxtACLNativeAPIContractTest(SimpleTestCase):
    def test_get_rules_sends_native_filter_and_preserves_acl_response(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        response = Mock(ok=True)
        response.json.return_value = [{"id": "r1", "priority": 7, "user": "alice", "layer": "private"}]
        response.headers = {}
        with patch("geonode.geoserver.noxt_acl.requests.request", return_value=response) as request:
            result = client.get_rules(workspace="ws", layer="private")
        assert result == {"rules": [{"id": "r1", "priority": 7, "user": "alice", "layer": "private"}]}
        assert request.call_args.kwargs["json"] == {
            "workspace": {"value": "ws", "includeDefault": False},
            "layer": {"value": "private", "includeDefault": False},
        }
        assert request.call_args.args[:2] == ("POST", "http://acl:8080/acl/api/rules/query")


    def test_get_rules_count_uses_acl_count_endpoint(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        response = Mock(ok=True)
        response.json.return_value = {"count": 3}
        with patch("geonode.geoserver.noxt_acl.requests.request", return_value=response) as request:
            assert client.get_rules_count() == 3
        assert request.call_args.args[:2] == ("POST", "http://acl:8080/acl/api/rules/query/count")


    def test_run_batch_deletes_before_creating_replacement_rules(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        batch = Noxt_ACLAutoPriorityBatch(10)
        batch.add_delete_rule("old-rule")
        batch.add_insert_rule(Noxt_ACLRule(Noxt_ACLRule.ALLOW, workspace="geonode", layer="private", user="alice"))
        with patch.object(client, "_request") as request:
            assert client.run_batch(batch) is True
        assert [call.args[:2] for call in request.call_args_list] == [
            ("DELETE", "rules/id/old-rule"), ("POST", "rules")
        ]


    def test_run_batch_makes_deletion_idempotent_and_deduplicates_ids(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        batch = Noxt_ACLRuleBatch()
        batch.add_delete_rule("gone")
        batch.add_delete_rule("gone")
        with patch.object(client, "_request", side_effect=Noxt_GeoServerACLException("missing", noxt_status_code=404)), \
             patch.object(client, "_query_rules", return_value=[]) as query:
            assert client.run_batch(batch) is True
        assert query.call_count == 1


    def test_run_batch_deduplicates_identical_acl_rules(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        batch = Noxt_ACLAutoPriorityBatch(10)
        rule = Noxt_ACLRule(Noxt_ACLRule.ALLOW, workspace="geonode", layer="private", user="alice", service="WMS")
        batch.add_insert_rule(rule)
        batch.add_insert_rule(rule)
        with patch.object(client, "_request") as request:
            assert client.run_batch(batch) is True
        request.assert_called_once()


    def test_run_batch_only_accepts_conflict_when_same_acl_rule_exists(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        batch = Noxt_ACLRuleBatch()
        rule = Noxt_ACLRule(Noxt_ACLRule.ALLOW, workspace="geonode", layer="private", user="alice", service="WMS")
        batch.add_insert_rule(rule)
        with patch.object(client, "_request", side_effect=Noxt_GeoServerACLException("duplicate", noxt_status_code=409)), \
             patch.object(client, "_query_rules", return_value=[dict(rule.fields)]):
            assert client.run_batch(batch) is True
        with patch.object(client, "_request", side_effect=Noxt_GeoServerACLException("conflict", noxt_status_code=409)), \
             patch.object(client, "_query_rules", return_value=[]):
            with self.assertRaises(Noxt_GeoServerACLException):
                client.run_batch(batch)


    def test_get_rules_falls_back_to_listing_when_filter_query_empty(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        all_rules = [
            {"id": "target", "workspace": "sf", "layer": "roads"},
            {"id": "other-layer", "workspace": "sf", "layer": "sfdem"},
            {"id": "other-workspace", "workspace": "topp", "layer": "roads"},
        ]
        with patch.object(client, "_query_rules", side_effect=[[], all_rules]) as query:
            assert client.get_rules(workspace="sf", layer="roads") == {"rules": [all_rules[0]]}
        assert query.call_args_list[1].args == ()


    def test_priority_is_greater_than_current_maximum(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        with patch.object(client, "_query_rules", return_value=[{"priority": 3}, {"priority": 19}, {"priority": 7}]):
            assert client.get_first_available_priority() == 20


    def test_get_rules_follows_acl_cursor_pages(self):
        client = Noxt_GeoServerACLClient("http://acl:8080/acl", "admin", "password")
        first, second = Mock(ok=True), Mock(ok=True)
        first.json.return_value = {"rules": [{"id": "r1"}]}
        first.headers = {"X-ACL-NEXTCURSOR": "page-2"}
        second.json.return_value = {"rules": [{"id": "r2"}]}
        second.headers = {}
        with patch("geonode.geoserver.noxt_acl.requests.request", side_effect=[first, second]) as request:
            assert client.get_rules() == {"rules": [{"id": "r1"}, {"id": "r2"}]}
        assert request.call_count == 2
        assert request.call_args_list[1].kwargs["params"]["nextCursor"] == "page-2"

    def test_layer_rule_cleanup_requires_exact_workspace_and_layer(self):
        from geonode.geoserver.acl_rules import Noxt_ACLRuleManager

        client = Mock()
        client.get_rules.return_value = {"rules": [
            {"id": "same", "workspace": "other", "layer": "private"},
            {"id": "match", "workspace": "geonode", "layer": "private"},
        ]}
        manager = Noxt_ACLRuleManager(client)
        batch = manager.collect_delete_layer_rules("geonode", "private")
        self.assertEqual(batch.operations, [{"@type": "delete", "@id": "match"}])

