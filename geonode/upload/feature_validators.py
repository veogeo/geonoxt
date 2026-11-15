#########################################################################
#
# Copyright (C) 2025 OSGeo
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
from urllib.parse import urlencode
from xml.etree.ElementTree import ParseError, fromstring

from geonode.geoserver.helpers import gs_catalog
from django.core.exceptions import ValidationError
from django.conf import settings

from geoserver.resource import FeatureType, parse_restrictions

logger = logging.getLogger(__name__)


class BaseFeatureValidator:
    """
    Base class for feature validators.
    """

    def __init__(self, dataset):
        self.dataset = dataset

    def validate(self, feature):
        """
        Validates the feature.
        Should raise an exception if validation fails.
        """
        raise NotImplementedError


class GeoserverFeatureValidator(BaseFeatureValidator):
    """
    Validates features against constraints defined in GeoServer's DescribeFeatureType.
    """

    def __init__(self, dataset):
        super().__init__(dataset)
        self.restrictions = {}
        if self.dataset.subtype == "vector":
            feature_type = self._get_feature_type(self.dataset)
            if feature_type:
                self.restrictions = self._get_restrictions(feature_type)

    def validate(self, feature):
        """
        Validates the feature against GeoServer constraints.
        """
        if not self.restrictions:
            return

        errors = []
        for attribute_name, value in feature.items():
            if attribute_name in self.restrictions:
                try:
                    self._validate_attribute(attribute_name, value, self.restrictions[attribute_name])
                except ValidationError as e:
                    if hasattr(e, "messages"):
                        errors.extend(e.messages if isinstance(e.messages, list) else [e.messages])
                    else:
                        errors.append(str(e))
        if errors:
            raise ValidationError(errors)

    def _get_feature_type(self, dataset):
        try:
            return gs_catalog.get_resource(name=dataset.name, store=dataset.store, workspace=dataset.workspace)
        except Exception as e:
            logger.error(f"Error fetching feature type from GeoServer: {e}")
            return None

    def _get_restrictions(self, feature_type):
        if settings.NOXT_GEOSERVER_CLOUD_ENABLED and isinstance(feature_type, FeatureType):
            return self._noxt_get_cloud_wfs_restrictions(feature_type)
        return getattr(feature_type, "attributes_restrictions", None)

    def _noxt_get_cloud_wfs_restrictions(self, feature_type):
        """Fetch schema restrictions from Cloud's WFS endpoint.

        geoserver-restconfig derives `/wfs` from the REST URL, but Cloud exposes
        REST through RestConfig and WFS through the gateway. Keep the classic
        path unchanged and use the explicit Cloud WFS URL for this request.
        """
        wfs_url = getattr(settings, "NOXT_GEOSERVER_CLOUD_WFS_URL", "")
        if not wfs_url:
            logger.warning("NOXT_GEOSERVER_CLOUD_WFS_URL is not configured; skipping Cloud schema restrictions")
            return None

        type_name = f"{feature_type.workspace.name}:{feature_type.name}"
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "DescribeFeatureType",
            "typeName": type_name,
        }
        response = feature_type.catalog.http_request(f"{wfs_url}?{urlencode(params)}")
        if response.status_code != 200:
            logger.error(
                "Cloud DescribeFeatureType failed for %s: %s %s",
                type_name,
                response.status_code,
                response.text,
            )
            return None
        try:
            return parse_restrictions(fromstring(response.content))
        except ParseError:
            logger.exception("Could not parse Cloud DescribeFeatureType response for %s", type_name)
            return None

    def _validate_attribute(self, attribute_name, value, restriction_info):
        if "restrictions" in restriction_info:
            if "enumeration" in restriction_info["restrictions"]:
                self._validate_enumeration(attribute_name, value, restriction_info["restrictions"]["enumeration"])
            if "range" in restriction_info["restrictions"]:
                self._validate_range(attribute_name, value, restriction_info["restrictions"]["range"])

    def _validate_enumeration(self, attribute_name, value, enumeration):
        if str(value) not in enumeration:
            raise ValidationError(
                f'Invalid value for attribute "{attribute_name}". '
                f'Value "{value}" is not in the allowed enumeration: {enumeration}.'
            )

    def _validate_range(self, attribute_name, value, range_info):
        min_val = range_info.get("min")
        max_val = range_info.get("max")
        try:
            value_as_float = float(value)
        except (ValueError, TypeError):
            raise ValidationError(
                f'Value "{value}" for attribute "{attribute_name}" is not a valid number for range validation.'
            )
        if min_val is not None and value_as_float < float(min_val):
            raise ValidationError(
                f'Invalid value for attribute "{attribute_name}". '
                f"Value {value} is less than the minimum allowed value of {min_val}."
            )
        if max_val is not None and value_as_float > float(max_val):
            raise ValidationError(
                f'Invalid value for attribute "{attribute_name}". '
                f"Value {value} is greater than the maximum allowed value of {max_val}."
            )
