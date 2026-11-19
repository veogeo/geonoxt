# GeoNoXT GeoNode fork: upstream maintenance map

GeoNode Cloud implementation code now lives in the installable `geonoxt`
Python package. GeoNode's core files remain integration points for settings,
authentication, ACL calls, and upload workflows.

## GeoNoXT-owned implementation

- `geonoxt/oauth.py` handles Cloud JWT validation, authentication, user
  mapping, and GeoServer audience token issuance.
- `geonoxt/oauth_validators.py` handles Cloud OAuth and refresh-token policy.
- `geonoxt/middleware.py` keeps the active GeoNode web session when the
  auxiliary GeoServer token cannot be refreshed.
- `geonoxt/geoserver/acl_client.py` and `acl_rules.py` implement the Cloud
  ACL client and rule batching.
- `geonoxt/management/commands/noxt_configure_geoserver_cloud_oauth.py`
  owns Cloud OAuth client setup. `geonoxt/apps.py` registers the standalone
  Django app and command when Cloud mode is enabled.
- `docker/noxt_cloud_test_state.py`, `docker/noxt_seed_cloud_demo.py`, and
  `seed-cloud-demo.sh` hold the opt-in test and demo data setup.

Setuptools now packages `geonoxt*` separately from `geonode*`.

## GeoNode integration seams

- `geonode/settings.py` selects the extension, its environment-backed
  settings, authentication classes, token lifetimes, and GeoServer URLs.
- `geonode/api/authentication.py` and `geonode/base/auth.py` delegate Cloud
  JWT authentication to `geonoxt.oauth`.
- `geonode/geoserver/helpers.py`, `manager.py`, and `security.py` connect
  GeoNode's publishing/ACL hooks to the extension.
- Cloud-specific test hooks in GeoNode's existing test suites verify those
  adapters; retain them when upgrading the corresponding APIs.

These remain the intentional conflict points for an upstream rebase. Core
GeoNode modules contain adapter/configuration changes, not the reusable ACL,
JWT, middleware, or management-command implementation.

## Deployment and data

Compose, GeoServer service configuration, PGConfig startup, and sample data
remain deployment assets in this repository. Demo data is opt-in through the
named seed utility; GeoServer's bundled sample workspaces/layers stay disabled.

## Validation

Python syntax compilation, project metadata parsing, and whitespace checks
pass. The source-mounted GeoNode container discovered the moved management
command. Eighteen focused Django tests passed for ACL, session retention,
workspace setup, and tokeninfo; Django reported no system-check issues. Tests
used an isolated `test_geonode` database, which was removed afterward. No
GeoNode image was rebuilt in this refactor.

The `cn-vg` branch already has eleven commits beyond its upstream baseline.
This package move and report are uncommitted. The local ignored
`.env_test_cloud` was not read or modified.
