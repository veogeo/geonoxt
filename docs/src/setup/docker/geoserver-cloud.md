# GeoNode with GeoServer Cloud (cn-vg preview)

This is the local GeoNode + GeoServer Cloud integration. Start the whole stack with `docker-compose.geoserver-cloud-stack.yml`; it includes GeoNode's Compose file, GeoServer Cloud's Compose file, and the Cloud runtime settings. The classic GeoServer service is named `legacy-geoserver` and is behind the `legacy-geoserver` profile, so it is not started. Let's Encrypt remains available behind the `tls` profile. HTTP-only runs leave that profile disabled by default; if the profile is explicitly enabled while `NOXT_LETSENCRYPT_MODE=disabled`, the container stays idle instead of restarting. For HTTPS, set `NOXT_LETSENCRYPT_MODE=staging` or `production`; the upstream certificate entrypoint and renewal cron remain in use.

All integration-owned environment variables and new Python symbols use the `NOXT_`, `noxt_`, or `Noxt_` prefix. Compose translates `NOXT_` inputs into the established names expected by GeoNode, GeoServer Cloud, and Docker Compose inside those services.

## Start the stack

The manual `cn-vg` workflow publishes the complete GHCR image set as immutable `cn-vg-<commit SHA>` tags and moving `latest` and `cn-vg-latest` multi-architecture tags. The integrated Compose runtime defaults every GeoServer Cloud service to `ghcr.io/veogeo` with the `latest` tag; set `NOXT_GEOSERVER_CLOUD_TAG` to an immutable SHA tag to pin a deployment. The unified Compose project keeps both repositories' relative paths and gives the services a shared default network. GeoNode's existing `statics` volume is mounted read-only in Cloud's REST, WMS, WFS, WCS, WPS, GWC, and WebUI containers so imports that reference an uploaded file can read the same file.

Copy `.env.geoserver-cloud.sample` to `.env.geoserver-cloud`, replace the repository paths and secret placeholders, and set `SITEURL` once to the public origin without a trailing slash (for example, `http://localhost` locally or `https://maps.example.org` in production). GeoServer public links and the WebUI proxy base are derived from `SITEURL`. The local OIDC issuer and MapStore callback default from it too; set the `NOXT_*OIDC*` values explicitly when using an external provider. Nginx forwards the request host and scheme, and session and CSRF cookies become secure automatically when `SITEURL` uses HTTPS. Keep the real file untracked:

```bash
cp .env.geoserver-cloud.sample .env.geoserver-cloud
docker compose -p geonode-cloud-oidc --env-file .env_dev \
  --env-file .env.geoserver-cloud -f docker-compose.geoserver-cloud-stack.yml up -d
```

The Compose project owns the GeoNode database, uploads, Cloud catalog, cache, and ACL database volumes. Development and integration tests use the same services and network; `docker compose down --volumes` resets all of those data stores. GeoServer Cloud's Consul, ACL, RabbitMQ, Config, and service containers are included; Consul remains part of this VM deployment. The runtime overlay mounts the Cloud `config/` directory by repository path so it does not depend on the current shell directory.

Set the GeoNode `.env` values for the Cloud profile:

```dotenv
NOXT_GEOSERVER_CLOUD_ENABLED=True
GEOSERVER_LB_HOST_IP=geoserver
GEOSERVER_LB_PORT=9090
GEOSERVER_LOCATION=http://geoserver:9090/geoserver/cloud/
GEOSERVER_PUBLIC_LOCATION={siteurl}/geoserver/
GEOSERVER_WEB_UI_LOCATION={siteurl}/geoserver/cloud/
NOXT_GEOSERVER_ACL_ENABLED=True
NOXT_GEOSERVER_ACL_URL=http://acl:8080/acl/
NOXT_GEOSERVER_ACL_USER=admin
NOXT_GEOSERVER_ACL_PASSWORD=<secret distinct from the GeoServer REST password>
```

Replace `{siteurl}` with the externally reachable GeoNode origin. The gateway base path is `/geoserver/cloud`. The unified Compose names the gateway service `geoserver`; GeoNode and Nginx use `geoserver:9090`. GeoNode publishes `/geoserver/` URLs through Nginx on host port 80. The unified compose starts Django after Cloud's gateway, ACL service, and REST catalog report healthy. To reconcile permissions manually:

```bash
docker compose exec django python manage.py sync_security_rules
```

Individual GeoServer microservices are not exposed on host ports. GeoNode's Django, Celery, and Nginx containers reach Cloud services on the shared Compose network.

## Run the Cloud test suite

Run `./test-cloud.sh` or `make test-cloud` from this repository to execute the full GeoNode suite against GeoServer Cloud. Pass Django test labels after the script (or as `NOXT_TEST_ARGS` in Make) to run a focused subset. The runner loads `.env_test_cloud` itself, verifies the remote Firefox node and both Bearer test tokens, extends expiry only for the two `noxt-bearer-test` and `noxt-bearer-outsider` fixtures, and writes a timestamped log under `/tmp`. It fails before starting tests if a required file or service is missing.

The runner removes old feature types and ACL rules only from `geonode:test_geonode_data`, verifies that none overlap the persistent `geonode:geonode_data` store, and drops only the disposable `test_geonode` and `test_geonode_data` databases before and after the run. Django keeps those fresh databases through test teardown so GeoServer Cloud’s idle connection cannot make Django’s own `DROP DATABASE` fail; the runner terminates remaining sessions and removes them afterward. It retains both datastore configurations and all persistent volumes. Selenium runs as a service in the same Compose project. The `noxt_seed_cloud_demo.py` helper can be run after startup to recreate the two persistent demo datasets and the reusable bearer test accounts; `.env_test_cloud` contains the local manual-login password and token file paths.

## OAuth and OIDC

Each GeoServer Cloud service validates signed JWT **access tokens** independently when `NOXT_GEOSERVER_CLOUD_BEARER_AUTH_ENABLED=true`. WFS, WMS, WCS, WPS, REST, GWC, and WebUI check the configured issuer, signing key, expiry, and audience. The gateway only routes requests and forwards the original `Authorization` header; it does not validate the token. When the variable is unset or false, service-side bearer validation is disabled. OIDC discovery does not guarantee that an access token is a JWT or contains the GeoServer audience, so configure the active provider to issue a signed JWT access token intended for `NOXT_GEOSERVER_CLOUD_AUDIENCE`. One issuer is active for a deployment at a time. An OIDC ID token or an opaque token is not an OGC credential. The legacy `geonode-oauth2` extension is disabled in this Compose deployment; API and OGC authorization use the active IdP's signed access JWT.

GeoNode is the default issuer. The default issuer is `SITEURL/o/`, the server-side discovery URL is `http://django:8000/o/.well-known/openid-configuration/`, and the browser discovery URL is `SITEURL/o/.well-known/openid-configuration/`. Each enabled GeoServer service reads the advertised JWKS through OIDC discovery unless `NOXT_GEOSERVER_CLOUD_JWK_SET_URI` overrides it with an address reachable from that service. Keep `NOXT_GEOSERVER_CLOUD_ISSUER_URI` byte-for-byte consistent with the issuer value in discovery and the JWT `iss` claim.

MapStore obtains the active provider's access token using Authorization Code with PKCE and sends it as a Bearer token to GeoNode `/api/v2` and GeoServer OGC endpoints. It does not use the GeoNode opaque API token or an ID token. Register the MapStore client as a **public client**, enable Authorization Code and PKCE with `S256`, register the exact redirect URI in `NOXT_MAPSTORE_OIDC_REDIRECT_URI`, and allow the GeoNode site origin to call the provider's token endpoint from the browser. `offline_access` requests refresh tokens when the provider supports them; otherwise MapStore starts a fresh PKCE authorization when the access token expires. GeoNode rotates refresh tokens and enforces a 30-day sliding refresh lifetime; an external provider applies its own refresh-token policy. The MapStore client never has a client secret.

GeoNode validates Bearer JWTs on its REST and middleware-protected APIs against the configured issuer, JWKS, and `NOXT_GEOSERVER_CLOUD_AUDIENCE`. The token's `NOXT_GEOSERVER_CLOUD_USERNAME_CLAIM` (default `preferred_username`) must identify an active, already provisioned GeoNode user using `NOXT_GEOSERVER_CLOUD_USER_FIELD` (default `username`). Cloud mode disables opaque `apikey` query authentication. For an external issuer, configure its access JWT to include that identity claim and provision matching GeoNode users before enabling API access.

Interactive OIDC login for the GeoServer WebUI is a separate opt-in. Set `NOXT_GEOSERVER_CLOUD_OIDC_WEB_LOGIN_ENABLED=true` to enable it; when unset or false, the WebUI OIDC login provider is not loaded. This toggle does not enable or disable Bearer validation in the GeoServer services. When GeoNode is the active issuer and WebUI login is enabled, startup creates or updates the public MapStore client and the confidential GeoServer WebUI client. Set `NOXT_GEOSERVER_CLOUD_OIDC_CLIENT_ID` and `NOXT_GEOSERVER_CLOUD_OIDC_CLIENT_SECRET` for that confidential client. GeoNode signs its access JWTs with the mounted RSA private key and includes the configured audience and group claims. The WebUI filter uses the same issuer's discovery metadata and the callback derived from the `geonode` filter name.

To use an external OIDC provider, register both clients at that provider: a public PKCE client for MapStore and a confidential client for the GeoServer WebUI callback `SITEURL/geoserver/cloud/web/login/oauth2/code/geonode__oidc`. Set `NOXT_GEOSERVER_CLOUD_ISSUER_URI`, `NOXT_GEOSERVER_CLOUD_DISCOVERY_URI`, `NOXT_GEOSERVER_CLOUD_JWK_SET_URI` when needed, `NOXT_GEOSERVER_CLOUD_AUDIENCE`, `NOXT_MAPSTORE_OIDC_DISCOVERY_URI`, `NOXT_MAPSTORE_OIDC_CLIENT_ID`, `NOXT_MAPSTORE_OIDC_REDIRECT_URI`, and the GeoServer client ID/secret. Configure the provider to issue an access JWT with that exact issuer and audience, and configure its role claim with `NOXT_GEOSERVER_CLOUD_ROLES_CLAIM` plus explicit role mappings. If the provider supports RFC 8707 resource indicators, set `NOXT_MAPSTORE_OIDC_RESOURCE`; otherwise configure its client/audience policy so the access JWT contains the GeoServer audience. Its authorization and token endpoints must be reachable from the browser and the Cloud services.

The default role claim is `groups`, and the sample explicitly maps `admin`, `authenticated`, and `private_readers`. Add a mapping for each group or role the provider emits; unmapped claims do not grant GeoServer roles. GeoNode's legacy `api/o/v4/tokeninfo` endpoint is not wired to the Cloud gateway and no shared introspection key is configured.

The Compose overlay mounts the RSA key as a read-only secret into Django and Celery at `/run/secrets/geonode-oidc-private-key`; set `NOXT_GEONODE_OIDC_PRIVATE_KEY_SOURCE` to a private key file outside the repository and keep `NOXT_GEONODE_OIDC_PRIVATE_KEY_FILE` at that in-container path. The key is used by GeoNode when GeoNode itself is the active issuer. Do not rely on GeoNode's legacy development key.

## WPS remote inputs

The runtime overlay mounts `docker/geoserver-cloud-wps-urlchecks.xml` into WPS. It permits remote WPS inputs only from `http://geoserver:9090/geoserver/cloud/wfs`, keeping the URL check enabled and preventing the WPS process from fetching arbitrary hosts. For layer inputs, request GeoJSON from WFS and declare `mimeType="application/json"`; the bearer token must be forwarded as an `Authorization` header in the WPS `Reference`. This avoids relaxing GeoServer's XML schema resolution restrictions. WPS verification for this deployment must use a private layer published in the `geonode` workspace.

## ACL mapping and validation

GeoServer ACL is the only resource authorization backend on this Cloud branch. It maps GeoNode's existing permission synchronization rules to the GeoServer ACL REST API. User permissions map to ACL `user`; GeoNode groups map to `ROLE_<GROUP>`; WMS/WFS/WCS/WPS/GWC service, request, workspace, layer, and spatial-limit fields are preserved where supported.

GeoServer Cloud's sample `gwc-gs.xml` has `<securityEnabled>false</securityEnabled>` by default. With that value, GeoWebCache skips GeoServer's per-layer access checks for cached tiles. The runtime overlay therefore mounts `docker/geoserver-cloud-gwc-gs.xml` into the Cloud WMS and GWC services with `securityEnabled=true`, so WMTS/GWC tiles and tiled WMS requests are checked against ACL. The mount is read-only to keep this protection enforced; change the overlay file and redeploy WMS/GWC to change GWC global defaults. GeoNode's tile-layer template selects the gridsets advertised by this Cloud deployment (`EPSG:4326` and `EPSG:900913`) when `NOXT_GEOSERVER_CLOUD_ENABLED=True`; classic GeoServer keeps its existing `EPSG:3857` names. `WebMercatorQuad` is not advertised by the current Cloud GWC configuration, so assigning it makes WMTS tile requests fail.

ACL has no atomic batch endpoint. Replacing a layer's rules deletes the prior rules before inserting the replacement set, so a failed synchronization is fail-closed but may temporarily deny access. GeoServer Cloud's ACL event bus propagates rule changes to the running services.

Permission changes that cannot be applied immediately stay marked as pending and are queued on GeoNode's `security` Celery queue; the worker retries failed synchronization. Deleted datasets use a separate queued cleanup with the saved workspace/layer names, so ACL rules can still be removed after the GeoNode database row is gone. At startup, `sync_security_rules` performs a forced reconciliation and exits with an error if any dataset fails; GeoNode therefore does not start while its initial ACL reconciliation is incomplete.

Before using this outside the local development environment, validate all of the following:

1. OIDC discovery issuer, JWKS, code flow, callback, logout, and GeoNode user/group claims.
2. Valid, expired, wrong-issuer, wrong-audience, invalid-signature, and opaque Bearer tokens at the gateway; only the correctly signed JWT access token should pass.
3. Public and private layers through WMS, WFS, WCS, WPS (if enabled), GWC, REST, and WebUI.
4. GeoNode user/group permission changes, permission deletion, spatial restrictions, imports, updates, and asynchronous Celery tasks.
5. Backups and restores for the Cloud catalog and GWC cache. Keep the old GeoServer volume untouched until these pass.

Keycloak runs outside this Compose project and is retained when the GeoNode/GeoServer Cloud Compose volumes are reset.
