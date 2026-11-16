#########################################################################
# Native GeoServer Cloud ACL rule operations for GeoNode.
#########################################################################

import itertools
import logging

logger = logging.getLogger(__name__)


class Noxt_ACLRule:
    """A rule expressed in GeoServer ACL REST field names."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    LIMIT = "LIMIT"
    CM_MIXED = "MIXED"

    def __init__(
        self,
        access,
        priority=None,
        workspace=None,
        layer=None,
        user=None,
        group=None,
        service=None,
        request=None,
        subfield=None,
        geo_limit=None,
        catalog_mode=None,
    ):
        if access is True:
            access = self.ALLOW
        elif access is False:
            access = self.DENY
        self.fields = {}
        for field, value in (
            ("priority", priority),
            ("user", user),
            ("role", f"ROLE_{group.upper()}" if group is not None and group != "*" else group),
            ("service", service),
            ("request", request),
            ("subfield", subfield),
            ("workspace", workspace),
            ("layer", layer),
            ("access", access),
        ):
            if value is not None and value != "*":
                self.fields[field] = value
        limits = {}
        if geo_limit is not None:
            limits["allowedArea"] = {"wkt": geo_limit}
            limits["spatialFilterType"] = "INTERSECT"
        if catalog_mode is not None:
            limits["catalogMode"] = catalog_mode
        if limits:
            self.fields["limits"] = limits

    def set_priority(self, priority):
        self.fields["priority"] = priority


class Noxt_ACLRuleBatch:
    """Ordered ACL changes; the client applies deletes before inserts."""

    def __init__(self, log_name=None):
        self.operations = []
        self.log_name = f'"{log_name}"' if log_name else ""

    def add_delete_rule(self, rule_id):
        self.operations.append({"@type": "delete", "@id": rule_id})

    def add_insert_rule(self, rule):
        self.operations.append({"@type": "insert", "rule": dict(rule.fields)})

    def length(self):
        return len(self.operations)


class Noxt_ACLAutoPriorityBatch(Noxt_ACLRuleBatch):
    def __init__(self, start_priority, log_name=None):
        super().__init__(log_name)
        self.noxt_priorities = itertools.count(start_priority)

    def add_insert_rule(self, rule):
        rule.set_priority(next(self.noxt_priorities))
        super().add_insert_rule(rule)


class Noxt_ACLRuleManager:
    def __init__(self, client):
        self.noxt_client = client

    def delete_all_rules(self):
        batch = Noxt_ACLRuleBatch("Delete all ACL rules")
        for rule in self.noxt_client.get_rules()["rules"]:
            batch.add_delete_rule(rule["id"])
        return self.noxt_client.run_batch(batch) if batch.length() else True

    def collect_delete_layer_rules(self, workspace, layer, batch=None):
        batch = batch or Noxt_ACLRuleBatch(f"Delete ACL rules for {workspace}:{layer}")
        rules = self.noxt_client.get_rules(workspace=workspace, layer=layer)["rules"]
        for rule in rules:
            if rule.get("workspace") == workspace and rule.get("layer") == layer:
                batch.add_delete_rule(rule["id"])
            else:
                logger.warning("Ignoring ACL rule outside requested layer %s:%s: %s", workspace, layer, rule)
        return batch

    def delete_layer_rules(self, workspace, layer):
        batch = self.collect_delete_layer_rules(workspace, layer)
        return self.noxt_client.run_batch(batch) if batch.length() else True

    def get_first_available_priority(self):
        rules = self.noxt_client.get_rules()["rules"]
        return max((int(rule.get("priority", 0)) for rule in rules), default=0) + 1
