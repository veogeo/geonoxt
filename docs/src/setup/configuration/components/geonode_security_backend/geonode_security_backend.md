# GeoNode Security Backend

GeoNode is the identity provider for the GeoServer Cloud deployment in this branch. Django and OAuth Toolkit authenticate users; GeoServer Cloud uses OIDC for the WebUI and validates opaque GeoNode OAuth access tokens at the gateway for OGC requests.

GeoNode permissions are synchronized to GeoServer Cloud ACL. User rules use ACL `user`, GeoNode group rules map to ACL roles, and service-specific permissions and spatial limits are represented as ACL rules. ACL is the only resource authorization backend in this branch; GeoFence is not used.

See the [GeoNode + GeoServer Cloud guide](../../../docker/geoserver-cloud.md) for the required environment, OIDC configuration, token introspection, ACL permissions, and test workflow.
