#########################################################################
#
# Copyright (C) 2021 OSGeo
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

import logging
import requests
import traceback
from packaging import version
import re
import typing
import xml.etree.ElementTree as ET

from lxml import etree
from defusedxml import lxml as dlxml
from requests.auth import HTTPBasicAuth
from guardian.shortcuts import get_anonymous_user

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from geonoxt.geoserver.acl_rules import Noxt_ACLRuleBatch, Noxt_ACLRule, Noxt_ACLAutoPriorityBatch
from geonode.geoserver.helpers import acl_client, acl_rule_manager, gs_catalog
from geonode.groups.models import GroupProfile
from geonode.utils import get_dataset_workspace
from geonode.resource.utils import is_remote_resource
from geonode.security.registry import permissions_registry

logger = logging.getLogger(__name__)


def delete_all_acl_rules():
    """purge all ACL rules"""
    if settings.GEOSERVER_SECURITY_ENABLED or getattr(
        settings, "NOXT_GEOSERVER_ACL_ENABLED", False
    ):
        acl_rule_manager.delete_all_rules()


def delete_acl_rules_for_layer(instance):
    """Delete all rules for a given layer
    This function resolves the ACL identity for a Dataset, since it doesn't know about Dataset model
    """
    if settings.GEOSERVER_SECURITY_ENABLED or getattr(
        settings, "NOXT_GEOSERVER_ACL_ENABLED", False
    ):
        resource = instance.get_self_resource()
        workspace_name = get_dataset_workspace(resource.dataset)
        layer_name = (
            resource.dataset.name
            if resource.dataset and hasattr(resource.dataset, "name")
            else resource.dataset.alternate
        )
        logger.debug(f"Removing rules for layer {workspace_name}:{layer_name}")
        acl_rule_manager.delete_layer_rules(workspace_name, layer_name)


def invalidate_acl_cache():
    """invalidate ACL cache"""
    if settings.GEOSERVER_SECURITY_ENABLED:
        try:
            acl_client.invalidate_cache()
            return True
        except Exception:
            tb = traceback.format_exc()
            logger.debug(tb)
            return False


def toggle_dataset_cache(dataset_name, enable=True, filters=None, formats=None):
    """Disable/enable a GeoServer Tiled Dataset Configuration"""
    if settings.GEOSERVER_SECURITY_ENABLED:
        try:
            url = settings.OGC_SERVER["default"]["LOCATION"]
            user = settings.OGC_SERVER["default"]["USER"]
            passwd = settings.OGC_SERVER["default"]["PASSWORD"]
            """
            curl -v -u admin:geoserver -XGET \
                "http://<host>:<port>/geoserver/gwc/rest/layers/geonode:tasmania_roads.xml"
            """
            r = requests.get(f"{url}gwc/rest/layers/{dataset_name}.xml", auth=HTTPBasicAuth(user, passwd))

            if r.status_code < 200 or r.status_code > 201:
                logger.debug(f"Could not Retrieve {dataset_name} Cache.")
                return False
            try:
                xml_content = r.content
                tree = dlxml.fromstring(xml_content)

                gwc_id = tree.find("id")
                tree.remove(gwc_id)

                gwc_enabled = tree.find("enabled")
                if gwc_enabled is None:
                    gwc_enabled = etree.Element("enabled")
                    tree.append(gwc_enabled)
                gwc_enabled.text = str(enable).lower()

                gwc_mimeFormats = tree.find("mimeFormats")
                # Returns an element instance or None
                if gwc_mimeFormats is not None and len(gwc_mimeFormats):
                    tree.remove(gwc_mimeFormats)

                if formats is not None:
                    for format in formats:
                        gwc_format = etree.Element("string")
                        gwc_format.text = format
                        gwc_mimeFormats.append(gwc_format)

                    tree.append(gwc_mimeFormats)

                gwc_parameterFilters = tree.find("parameterFilters")
                if filters is None:
                    tree.remove(gwc_parameterFilters)
                else:
                    for filter in filters:
                        for k, v in filter.items():
                            """
                            <parameterFilters>
                                <styleParameterFilter>
                                    <key>STYLES</key>
                                    <defaultValue/>
                                </styleParameterFilter>
                            </parameterFilters>
                            """
                            gwc_parameter = etree.Element(k)
                            for parameter_key, parameter_value in v.items():
                                gwc_parameter_key = etree.Element("key")
                                gwc_parameter_key.text = parameter_key
                                gwc_parameter_value = etree.Element("defaultValue")
                                gwc_parameter_value.text = parameter_value

                                gwc_parameter.append(gwc_parameter_key)
                                gwc_parameter.append(gwc_parameter_value)
                            gwc_parameterFilters.append(gwc_parameter)

                """
                curl -v -u admin:geoserver -XPOST \
                    -H "Content-type: text/xml" -d @poi.xml \
                        "http://localhost:8080/geoserver/gwc/rest/layers/tiger:poi.xml"
                """
                headers = {"Content-type": "text/xml"}
                payload = ET.tostring(tree)
                r = requests.post(
                    f"{url}gwc/rest/layers/{dataset_name}.xml",
                    headers=headers,
                    data=payload,
                    auth=HTTPBasicAuth(user, passwd),
                )
                if r.status_code < 200 or r.status_code > 201:
                    logger.debug(f"Could not Update {dataset_name} Cache.")
                    return False
            except Exception:
                tb = traceback.format_exc()
                logger.debug(tb)
                return False
            return True
        except Exception:
            tb = traceback.format_exc()
            logger.debug(tb)
            return False


def delete_dataset_cache(dataset_name):
    """Delete a GeoServer Tiled Dataset Configuration and all the cache"""
    if settings.GEOSERVER_SECURITY_ENABLED:
        try:
            url = settings.OGC_SERVER["default"]["LOCATION"]
            user = settings.OGC_SERVER["default"]["USER"]
            passwd = settings.OGC_SERVER["default"]["PASSWORD"]
            """
            curl -v -u admin:geoserver -XDELETE \
                "http://<host>:<port>/geoserver/gwc/rest/layers/geonode:tasmania_roads.xml"
            """
            r = requests.delete(f"{url}gwc/rest/layers/{dataset_name}.xml", auth=HTTPBasicAuth(user, passwd))

            if r.status_code < 200 or r.status_code > 201:
                logger.debug(f"Could not Delete {dataset_name} Cache.")
                return False
            return True
        except Exception:
            tb = traceback.format_exc()
            logger.debug(tb)
            return False


def set_geowebcache_invalidate_cache(dataset_alternate, cat=None):
    """invalidate GeoWebCache Cache ACL rules"""
    if dataset_alternate is not None and len(dataset_alternate) and "None" not in dataset_alternate:
        try:
            if cat is None or cat.get_layer(dataset_alternate) is not None:
                url = settings.OGC_SERVER["default"]["LOCATION"]
                user = settings.OGC_SERVER["default"]["USER"]
                passwd = settings.OGC_SERVER["default"]["PASSWORD"]
                """
                curl -v -u admin:geoserver \
                -H "Content-type: text/xml" \
                -d "<truncateLayer><layerName>{dataset_alternate}</layerName></truncateLayer>" \
                http://localhost:8080/geoserver/gwc/rest/masstruncate
                """
                headers = {"Content-type": "text/xml"}
                payload = f"<truncateLayer><layerName>{dataset_alternate}</layerName></truncateLayer>"
                r = requests.post(
                    f"{url.rstrip('/')}/gwc/rest/masstruncate",
                    headers=headers,
                    data=payload,
                    auth=HTTPBasicAuth(user, passwd),
                )
                if r.status_code < 200 or r.status_code > 201:
                    logger.debug(f"Could not Truncate GWC Cache for Dataset '{dataset_alternate}'.")
        except Exception:
            tb = traceback.format_exc()
            logger.debug(tb)


def allow_acl_layer_to_all(instance):
    """assign access permissions to all users

    This method is only relevant to Dataset instances that have their
    underlying data managed by geoserver, meaning:

    * layers that are not associated with a Service
    * layers that are associated with a Service that is being CASCADED through
      geoserver

    """
    resource = instance.get_self_resource()
    logger.debug(f"Inside allow_acl_layer_to_all for instance {instance}")
    workspace = get_dataset_workspace(resource.dataset)
    dataset_name = (
        resource.dataset.name if resource.dataset and hasattr(resource.dataset, "name") else resource.dataset.alternate
    )
    logger.debug(f"Allowing {workspace}:{dataset_name} access to everybody")
    try:
        priority = acl_rule_manager.get_first_available_priority()
        acl_client.insert_rule(Noxt_ACLRule(Noxt_ACLRule.ALLOW, priority=priority, workspace=workspace, layer=dataset_name))
    except Exception as e:
        tb = traceback.format_exc()
        logger.debug(tb)
        raise RuntimeError(f"Could not ADD GeoServer ANONYMOUS ACL rule for Dataset {dataset_name}: {e}")
    finally:
        if not getattr(settings, "DELAYED_SECURITY_SIGNALS", False):
            invalidate_acl_cache()
        else:
            resource.set_dirty_state()


# def sync_acl_client_with_guardian(dataset, perms, user=None, group=None, group_perms=None):
def create_acl_rules(layer, perms, user=None, group=None, batch: Noxt_ACLRuleBatch = None):
    """
    Collect ACL rules related to passed perms into an ACL batch.
    If the batch does not exist, it is created and returned.
    """
    if user and group:
        raise ValueError("Both user and group given")

    layer_name = layer.name if layer and hasattr(layer, "name") else layer.alternate
    workspace_name = get_dataset_workspace(layer)
    # Create new rule-set
    acl_services = _get_acl_services(layer, perms)

    username = (user if isinstance(user, str) else user.username) if user else None
    groupname = (group if isinstance(group, str) else group.name) if group else None

    users_geolimits, groups_geolimits, anonymous_geolimits = get_geolimits(layer, username, groupname)

    if not batch:
        batch = Noxt_ACLAutoPriorityBatch(acl_rule_manager.get_first_available_priority(), f"Sync {workspace_name}:{layer_name}")

    # Set global geolimits
    # Anon limits should go at the end, but it's responsibility of the caller to create first user/group rules
    for limits, scope, u, g in (
        (users_geolimits, "USER", username, None),
        (groups_geolimits, "GROUP", None, groupname),
        (anonymous_geolimits, "ANON", None, None),
    ):
        if limits and limits.exists():
            logger.debug(f"Adding ACL {scope} GeoLimit rule: U:{u} G:{g} L:{layer} ")
            wkt = limits.last().wkt
            batch.add_insert_rule(
                Noxt_ACLRule(
                    Noxt_ACLRule.LIMIT,
                    workspace=workspace_name,
                    layer=layer_name,
                    user=u,
                    group=g,
                    geo_limit=wkt,
                    catalog_mode=Noxt_ACLRule.CM_MIXED,
                )
            )

    if username:
        msg = "Adding ACL USER rules: U:{username} S:{service} L:{layer} "
    elif not groupname:
        msg = "Adding ACL ANON rules: S:{service} L:{layer} "
    if groupname:
        msg = "Adding ACL GROUP rules: G:{groupname} S:{service} L:{layer} "

    # Set services rules
    for rule_fields in acl_services:
        if layer and layer_name:
            logger.debug(msg.format(username=username, groupname=groupname, layer=layer_name, **rule_fields))
            batch.add_insert_rule(
                Noxt_ACLRule(
                    user=username,
                    group=groupname,
                    workspace=workspace_name,
                    layer=layer_name,
                    **rule_fields,  # access, service, request, subfield
                )
            )

    return batch


def sync_resources_with_guardian(resource=None, force=False):
    """
    Sync resources with Guardian and clear their dirty state
    """
    from geonode.layers.models import Dataset

    if resource:
        datasets = Dataset.objects.filter(id=resource.id)
    else:
        if force:
            datasets = Dataset.objects.all()
        else:
            datasets = Dataset.objects.filter(dirty_state=True)
    if datasets and datasets.exists():
        logger.debug(" --------------------------- synching with guardian!")

        rules_committed = False
        failed_datasets = []

        for dataset in datasets:
            if is_remote_resource(dataset):
                # No local ACL rules to sync: the data lives on the remote service.
                dataset.clear_dirty_state()
                continue
            try:
                batch = Noxt_ACLAutoPriorityBatch(acl_rule_manager.get_first_available_priority(), f"Sync resources {dataset}")

                acl_rule_manager.collect_delete_layer_rules(get_dataset_workspace(dataset), dataset.name, batch)
                perm_spec = permissions_registry.get_perms(instance=dataset)
                # All the other users
                if "users" in perm_spec:
                    for user, perms in perm_spec["users"].items():
                        user = get_user_model().objects.get(username=user)
                        # Set the ACL User ACL rules
                        acl_client_user = str(user)
                        if "AnonymousUser" in acl_client_user or str(get_anonymous_user()) in acl_client_user:
                            acl_client_user = None
                        create_acl_rules(dataset, perms, user=acl_client_user, batch=batch)
                # All the other groups
                if "groups" in perm_spec:
                    for group, perms in perm_spec["groups"].items():
                        group = Group.objects.get(name=group)
                        if group and group.name and group.name == "anonymous":
                            group = None
                        # Set the ACL Group ACL rules
                        create_acl_rules(dataset, perms, group=group, batch=batch)

                logger.info(f"Going to synch permissions in ACL for resource {dataset}")
                # An empty batch is a successful no-op. For a non-empty batch,
                # only clear the dirty marker after the backend confirms it
                # committed; the ACL REST adapter can fail partway through its
                # non-transactional delete/insert sequence.
                rules_committed = batch.length() == 0 or acl_client.run_batch(batch)
                if rules_committed:
                    dataset.clear_dirty_state()
                else:
                    dataset.set_dirty_state()
                    failed_datasets.append(str(dataset))
            except Exception as e:
                logger.exception(e)
                logger.warning(f"!WARNING! - Failure Synching-up Security ACL rules for Resource [{dataset}]")
                dataset.set_dirty_state()
                failed_datasets.append(str(dataset))

        if rules_committed:
            invalidate_acl_cache()

        if force and failed_datasets:
            failed = ", ".join(failed_datasets)
            raise RuntimeError(f"Security rules failed to synchronize for: {failed}")
        return not failed_datasets

    return True


def get_geolimits(layer, username, groupname):
    users_geolimits = None
    groups_geolimits = None
    anonymous_geolimits = None

    if username:
        users_geolimits = layer.users_geolimits.filter(user=get_user_model().objects.get(username=username))

    if groupname:
        if GroupProfile.objects.filter(group__name=groupname).exists():
            groups_geolimits = layer.groups_geolimits.filter(group=GroupProfile.objects.get(group__name=groupname))

    if not username and not groupname:
        anonymous_geolimits = layer.users_geolimits.filter(user=get_anonymous_user())

    return users_geolimits, groups_geolimits, anonymous_geolimits


def has_geolimits(layer, user, group):
    if user:
        _user = user if isinstance(user, str) else user.username
        users_geolimits = layer.users_geolimits.filter(user=get_user_model().objects.get(username=_user))
        return users_geolimits.exists()

    if group:
        _group = group if isinstance(group, str) else group.name
        if GroupProfile.objects.filter(group__name=_group).count() == 1:
            groups_geolimits = layer.groups_geolimits.filter(group=GroupProfile.objects.get(group__name=_group))
            return groups_geolimits.exists()

    if not user and not group:
        anonymous_geolimits = layer.users_geolimits.filter(user=get_anonymous_user())
        return anonymous_geolimits.exists()


def _get_acl_services(layer, perms):
    edit = "change_dataset_data" in perms
    download = "download_resourcebase" in perms
    view = "view_resourcebase" in perms

    acl_services = []

    # view services
    if view or "change_dataset_style" in perms:
        acl_services.append({"service": "WMS", "access": True})
        acl_services.append({"service": "GWC", "access": True})

    # WPS
    if view or download or edit:
        if not download:
            if geoserver_allows_wps_rules(gs_catalog.get_version()):
                acl_services.append({"service": "WPS", "subfield": "GS:DOWNLOAD", "access": False})
        acl_services.append({"service": "WPS", "access": True})

    if download and not edit and layer.is_vector():
        for request in ("TRANSACTION", "LOCKFEATURE", "GETFEATUREWITHLOCK"):
            acl_services.append({"service": "WFS", "request": request, "access": False})

    if download or edit:
        service = "WFS" if layer.is_vector() else "WCS"
        acl_services.append({"service": service, "access": True})

    # TODO: check if this rule is really needed
    if download and (view or edit):
        acl_services.append({"service": "*", "access": True})

    return acl_services


def geoserver_allows_wps_rules(ver: str) -> bool:
    try:
        s = re.search(r"^[0-9]\.[0-9]*", ver)
        if s:
            ver_clean = s.group()
            return version.parse(ver_clean) >= version.parse("2.23.0")
        else:
            logger.warning(f'Unparsable GeoServer version string "{ver}"')

    except Exception as e:
        logger.warning(f'Error evaluating GeoServer version "{ver}": {e}')

    return False


def _get_gwc_filters_and_formats(disable_cache: bool) -> typing.Tuple[list, list]:
    filters = [{"styleParameterFilter": {"STYLES": ""}}]
    formats = [
        "application/json;type=utfgrid",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/png8",
        "image/vnd.jpeg-png",
        "image/vnd.jpeg-png8",
    ]
    if disable_cache:
        filters = None
        formats = None
    return filters, formats
