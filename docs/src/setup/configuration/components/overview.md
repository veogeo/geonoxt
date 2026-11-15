# GeoNode and GeoServer Cloud Security

This branch integrates GeoNode with GeoServer Cloud. GeoNode's Django OAuth Toolkit provides OIDC identity, and GeoServer Cloud ACL enforces resource permissions synchronized from GeoNode. GeoFence and the classic GeoServer security integration are not part of this branch.

For setup and verification, use the [GeoNode + GeoServer Cloud guide](../../docker/geoserver-cloud.md). It covers the OAuth/OIDC flow, opaque access-token validation at the Cloud gateway, ACL rule mapping, and testing private layers through OGC services.
