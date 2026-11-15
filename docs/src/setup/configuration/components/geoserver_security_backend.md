# GeoServer Cloud Security Backend

This `cn-vg` branch uses GeoServer Cloud with GeoNode as its OIDC provider and GeoServer Cloud ACL as the sole resource authorization backend. The branch does not configure the GeoFence plugin or the classic GeoServer security adapter.

For deployment, environment variables, bearer-token introspection, permission synchronization, and verification steps, follow the [GeoNode + GeoServer Cloud guide](../../docker/geoserver-cloud.md).

GeoNode remains responsible for authentication and resource permissions. ACL rules are synchronized from GeoNode users, groups, service permissions, and spatial limits. The Cloud gateway validates opaque GeoNode OAuth access tokens through the configured token introspection endpoint.
