import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from oauth2_provider.models import AccessToken, Application


class Command(BaseCommand):
    help = "Create the Cloud OAuth client; WebUI configures its optional OIDC login."

    def handle(self, *args, **options):
        if not getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
            raise CommandError("Set NOXT_GEOSERVER_CLOUD_ENABLED=True before configuring the Cloud OAuth client.")
        client_id = os.getenv("NOXT_GEOSERVER_CLOUD_OIDC_CLIENT_ID", "").strip()
        client_secret = os.getenv("NOXT_GEOSERVER_CLOUD_OIDC_CLIENT_SECRET", "")
        redirect_uri = settings.SITEURL.rstrip("/") + "/geoserver/cloud/web/login/oauth2/code/geonode__oidc"
        if not client_id or not client_secret:
            raise CommandError("Set the Cloud OIDC client id and client secret.")

        self.ensure_access_token_storage_capacity()
        active_issuer = settings.NOXT_GEOSERVER_CLOUD_ISSUER_URI.rstrip("/")
        geonode_issuer = settings.OAUTH2_PROVIDER["OIDC_ISS_ENDPOINT"].rstrip("/")
        if active_issuer == geonode_issuer:
            with transaction.atomic():
                Application.objects.update_or_create(
                    client_id=client_id,
                    defaults={
                        "name": "GeoServer Cloud",
                        "client_secret": client_secret,
                        "client_type": Application.CLIENT_CONFIDENTIAL,
                        "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                        "redirect_uris": redirect_uri,
                        "algorithm": "RS256",
                        "skip_authorization": True,
                    },
                )
                Application.objects.update_or_create(
                    client_id=settings.NOXT_MAPSTORE_OIDC_CLIENT_ID,
                    defaults={
                        "name": "MapStore OIDC",
                        "client_secret": "",
                        "client_type": Application.CLIENT_PUBLIC,
                        "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                        "redirect_uris": settings.NOXT_MAPSTORE_OIDC_REDIRECT_URI,
                        "algorithm": "RS256",
                        "skip_authorization": True,
                    },
                )
        else:
            self.stdout.write("Using the configured external OIDC issuer; clients must be registered there.")

        if os.getenv("NOXT_GEOSERVER_CLOUD_OIDC_WEB_LOGIN_ENABLED", "false").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            self.stdout.write("GeoServer Cloud WebUI OIDC login is enabled; WebUI configures its filter on startup.")
        else:
            self.stdout.write("GeoServer Cloud WebUI OIDC login is disabled.")
        self.stdout.write(self.style.SUCCESS("GeoServer Cloud OIDC clients are configured."))

    def ensure_access_token_storage_capacity(self):
        if connection.vendor == "sqlite":
            # SQLite does not enforce CharField max_length for stored values.
            return
        if connection.vendor != "postgresql":
            raise CommandError("Cloud JWT access-token storage is supported on PostgreSQL or SQLite.")

        table = AccessToken._meta.db_table
        column = AccessToken._meta.get_field("token").column
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                [table, column],
            )
            result = cursor.fetchone()
            if not result:
                raise CommandError("Could not find the OAuth access-token column in the current database schema.")
            if result[0] != "text":
                cursor.execute(
                    f"ALTER TABLE {connection.ops.quote_name(table)} "
                    f"ALTER COLUMN {connection.ops.quote_name(column)} TYPE text"
                )
