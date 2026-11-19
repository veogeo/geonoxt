"""GeoNoXT client for the GeoServer Cloud ACL service."""

#########################################################################
# GeoServer Cloud ACL REST client for GeoNode security synchronization.
#########################################################################

import requests
from requests.auth import HTTPBasicAuth


class Noxt_GeoServerACLException(RuntimeError):
    """Raised when GeoNode cannot synchronize a permission with GeoServer ACL."""

    def __init__(self, message, noxt_status_code=None):
        super().__init__(message)
        self.noxt_status_code = noxt_status_code


class Noxt_GeoServerACLClient:
    """Client for GeoServer Cloud's ACL REST API."""

    def __init__(self, baseurl: str, username: str, password: str, timeout: int = 60):
        self.noxt_baseurl = baseurl.rstrip("/") + "/"
        self.noxt_api_url = self.noxt_baseurl + "api/"
        self.noxt_auth = HTTPBasicAuth(username, password)
        self.noxt_timeout = timeout

    def _request(self, method, path, **kwargs):
        response = requests.request(
            method,
            self.noxt_api_url + path.lstrip("/"),
            auth=self.noxt_auth,
            timeout=self.noxt_timeout,
            **kwargs,
        )
        if not response.ok:
            raise Noxt_GeoServerACLException(
                f"GeoServer ACL {method} {path} failed ({response.status_code}): {response.text[:500]}",
                noxt_status_code=response.status_code,
            )
        return response

    @staticmethod
    def _filter_value(value, include_default=False):
        return None if value is None else {"value": value, "includeDefault": include_default}

    def _query_rules(self, filters=None):
        rules, cursor = [], None
        while True:
            params = {"limit": 500}
            if cursor:
                params["nextCursor"] = cursor
            if filters:
                response = self._request("POST", "rules/query", params=params, json=filters)
            else:
                response = self._request("GET", "rules", params=params)
            payload = response.json()
            if isinstance(payload, dict):
                payload = payload.get("rules", payload.get("items", []))
            if not isinstance(payload, list):
                raise Noxt_GeoServerACLException("Unexpected GeoServer ACL rules response")
            rules.extend(dict(rule) for rule in payload)
            cursor = response.headers.get("X-ACL-NEXTCURSOR")
            if not cursor:
                break
        return rules

    def get_rules(self, workspace=None, workspace_any=False, layer=None, layer_any=False):
        filters = {}
        for key, value, include_default in (
            ("workspace", workspace, workspace_any),
            ("layer", layer, layer_any),
        ):
            if value is not None:
                filters[key] = self._filter_value(value, include_default=bool(include_default))
        rules = self._query_rules(filters or None)
        if filters and not rules:
            # Fall back to the paginated listing if the service version does not
            # support the RuleFilter query form.
            rules = self._query_rules()
            for key, value, include_default in (
                ("workspace", workspace, workspace_any),
                ("layer", layer, layer_any),
            ):
                if value is None:
                    continue
                rules = [
                    rule for rule in rules
                    if rule.get(key) == value or (bool(include_default) and rule.get(key) is None)
                ]
        return {"rules": rules}

    def get_rules_count(self):
        count = self._request("POST", "rules/query/count", json={}).json()
        return int(count.get("count", 0) if isinstance(count, dict) else count)

    def get_first_available_priority(self):
        rules = self._query_rules()
        return max((int(rule.get("priority", 0)) for rule in rules), default=0) + 1

    def invalidate_cache(self):
        # ACL changes propagate through GeoServer Cloud's event bus.
        return None

    @staticmethod
    def _rule_identity(rule):
        return tuple(
            (field, repr(rule.get(field)))
            for field in ("user", "role", "service", "request", "subfield", "workspace", "layer", "access", "limits")
        )

    def insert_rule(self, rule):
        fields = rule.fields if hasattr(rule, "fields") else rule
        return self._request("POST", "rules", json=dict(fields))

    def run_batch(self, batch, timeout=None):
        if not batch.length():
            return False
        # ACL has no atomic batch API. Delete first to fail closed during replacement.
        deleted_rules = set()
        for operation in batch.operations:
            if operation.get("@type") != "delete":
                continue
            rule_id = operation["@id"]
            if rule_id in deleted_rules:
                continue
            deleted_rules.add(rule_id)
            try:
                self._request("DELETE", f"rules/id/{rule_id}")
            except Noxt_GeoServerACLException as exc:
                if exc.noxt_status_code != 404 or any(rule.get("id") == rule_id for rule in self._query_rules()):
                    raise
        inserted_rules = set()
        for operation in batch.operations:
            if operation.get("@type") != "insert":
                continue
            rule = dict(operation.get("rule", {}))
            identity = self._rule_identity(rule)
            if identity in inserted_rules:
                continue
            inserted_rules.add(identity)
            try:
                self._request("POST", "rules", json=rule)
            except Noxt_GeoServerACLException as exc:
                if exc.noxt_status_code != 409:
                    raise
                if not any(self._rule_identity(existing) == identity for existing in self._query_rules()):
                    raise
        return True
