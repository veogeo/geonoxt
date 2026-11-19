import logging

from django.conf import settings

from geonode import geoserver
from geonode.base.auth import get_or_create_token, get_token_object_from_session, set_session_token
from geonode.security.middleware import SessionControlMiddleware as GeoNodeSessionControlMiddleware
from geonode.utils import check_ogc_backend

logger = logging.getLogger(__name__)


class SessionControlMiddleware(GeoNodeSessionControlMiddleware):
    """Keep the Django session alive when its auxiliary GeoServer token expires."""

    def process_request(self, request):
        if not settings.NOXT_GEOSERVER_CLOUD_ENABLED:
            return super().process_request(request)

        if request and request.user and not request.user.is_anonymous:
            if not request.user.is_active:
                self.do_logout(request)
            elif check_ogc_backend(geoserver.BACKEND_PACKAGE):
                try:
                    access_token = get_token_object_from_session(request.session)
                    if access_token is None or access_token.is_expired():
                        access_token = get_or_create_token(request.user)
                        if access_token and not access_token.is_expired():
                            set_session_token(request.session, access_token)
                            request.session.modified = True
                        else:
                            logger.warning(
                                "Could not renew the GeoServer access token for active session user %s.",
                                request.user.pk,
                            )
                except Exception:
                    logger.exception(
                        "Could not renew the GeoServer access token for active session user %s.",
                        request.user.pk,
                    )
