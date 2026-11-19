"""OAuth validator used only by the GeoNoXT GeoServer Cloud integration."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from oauth2_provider.oauth2_validators import OAuth2Validator


class GeoNodeCloudOAuth2Validator(OAuth2Validator):
    """Enforce a sliding lifetime for rotated Cloud refresh tokens."""

    def validate_silent_login(self, request):
        """Leave silent-login session checks to GeoNode's authorization view.

        OAuthLib calls this before it attaches the Django session user to its
        request. Returning True here allows the view to return the appropriate
        OIDC ``login_required`` response when the session has expired.
        """
        return True

    def validate_silent_authorization(self, request):
        """Leave silent-authorization consent handling to GeoNode's view."""
        return True

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        if not super().validate_refresh_token(refresh_token, client, request, *args, **kwargs):
            return False

        expire_after = settings.OAUTH2_PROVIDER.get("REFRESH_TOKEN_EXPIRE_SECONDS")
        if not expire_after:
            return True
        if not isinstance(expire_after, timedelta):
            expire_after = timedelta(seconds=int(expire_after))

        from oauth2_provider.models import RefreshToken

        token = RefreshToken.objects.filter(token=refresh_token).first()
        if token is None:
            return False
        if timezone.now() - token.created >= expire_after:
            token.revoke()
            return False
        return True
