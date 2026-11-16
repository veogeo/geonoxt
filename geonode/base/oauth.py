import uuid
from functools import lru_cache

import jwt
from jwcrypto import jwk
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed


def geonode_cloud_access_token_generator(request, refresh_token=False):
    """Create an RS256 API access JWT for an authenticated GeoNode user.

    This is an OAuth access token for GeoServer Cloud, not an OIDC ID token.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        raise ImproperlyConfigured("GeoServer Cloud access tokens require an authenticated user.")

    provider = settings.OAUTH2_PROVIDER
    private_key = provider.get("OIDC_RSA_PRIVATE_KEY")
    issuer = provider.get("OIDC_ISS_ENDPOINT")
    audience = getattr(settings, "NOXT_GEOSERVER_CLOUD_AUDIENCE", "")
    if not private_key or not issuer or not audience:
        raise ImproperlyConfigured(
            "GeoServer Cloud JWT access tokens require an OIDC signing key, issuer, and audience."
        )

    now = int(timezone.now().timestamp())
    expires_in = int(getattr(request, "expires_in", settings.ACCESS_TOKEN_EXPIRE_SECONDS))
    groups = list(user.groups.values_list("name", flat=True))
    if user.is_superuser and "admin" not in groups:
        groups.append("admin")
    if "authenticated" not in groups:
        groups.append("authenticated")

    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": str(user.pk),
        "preferred_username": user.get_username(),
        "groups": groups,
        "roles": groups,
        "iat": now,
        "exp": now + expires_in,
        "jti": str(uuid.uuid4()),
    }
    scopes = getattr(request, "scopes", None)
    if scopes:
        claims["scope"] = " ".join(scopes)
    signing_key = jwk.JWK.from_pem(private_key.encode("utf-8"))
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": signing_key.thumbprint(), "typ": "at+jwt"},
    )

@lru_cache(maxsize=4)
def _cloud_jwks_client(issuer_uri, discovery_uri, jwk_set_uri):
    if not jwk_set_uri:
        response = requests.get(discovery_uri, timeout=10)
        response.raise_for_status()
        metadata = response.json()
        if metadata.get("issuer", "").rstrip("/") != issuer_uri.rstrip("/"):
            raise ValueError("The OIDC discovery issuer does not match the configured issuer.")
        jwk_set_uri = metadata.get("jwks_uri")
        if not jwk_set_uri:
            raise ValueError("The active OIDC discovery document has no jwks_uri.")
    return jwt.PyJWKClient(jwk_set_uri)


def authenticate_cloud_access_token(token):
    """Validate an active-issuer JWT and map its configured username claim to GeoNode."""
    if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
        raise ValueError("Cloud JWT authentication is disabled.")

    issuer_uri = settings.NOXT_GEOSERVER_CLOUD_ISSUER_URI
    jwks_client = _cloud_jwks_client(
        issuer_uri,
        settings.NOXT_GEOSERVER_CLOUD_DISCOVERY_URI,
        settings.NOXT_GEOSERVER_CLOUD_JWK_SET_URI,
    )
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer_uri,
        audience=settings.NOXT_GEOSERVER_CLOUD_AUDIENCE,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    username_claim = settings.NOXT_GEOSERVER_CLOUD_USERNAME_CLAIM
    username = claims.get(username_claim)
    if not isinstance(username, str) or not username:
        raise ValueError("The JWT is missing the configured username claim.")

    user_model = get_user_model()
    user_field = settings.NOXT_GEOSERVER_CLOUD_USER_FIELD
    user = user_model._default_manager.filter(**{user_field: username}).first()
    if user is None or not user.is_active:
        raise ValueError("The JWT identity does not map to an active GeoNode user.")
    return user, claims


class GeoNodeCloudJWTAuthentication(BaseAuthentication):
    """Authenticate Cloud API requests with the active OIDC access-token JWT."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Malformed Bearer authorization header.")
        try:
            user, claims = authenticate_cloud_access_token(parts[1].decode("utf-8"))
        except Exception as error:
            raise AuthenticationFailed("Invalid OIDC JWT access token.") from error
        return user, claims

    def authenticate_header(self, request):
        return "Bearer"


class GeoNodeCloudJWTBackend:
    """Expose active-issuer JWT identities to Django middleware."""

    def authenticate(self, request=None, **kwargs):
        if request is None:
            return None
        auth_header = request.META.get("HTTP_AUTHORIZATION", request.META.get("HTTP_AUTHORIZATION2", ""))
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        try:
            user, _claims = authenticate_cloud_access_token(parts[1])
        except Exception:
            return None
        return user
