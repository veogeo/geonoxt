# GeoNoXT GeoNode overlay

The `cn-vg` branch keeps its committed GeoNode tree at the upstream base. This
directory contains the GeoNoXT sources and a reproducible patch for the
GeoNode integration and deployment files used by the GeoServer Cloud stack.
The patch records full upstream blob IDs; the applicator stops if the base no
longer matches instead of applying changes to unexpected locations.

## Contents

- `src/geonoxt/`: installable Cloud authentication, token/session, ACL, and
  management-command implementation.
- `src/docker/` and `src/seed-cloud-demo.sh`: source for optional clean-state
  checks and example data seeding.
- `patches/geonode-cloud-overlay.patch`: settings, authentication/ACL adapters,
  test coverage, Docker Compose/runtime configuration, and related project
  integration changes.
- `apply-overlay.sh`: applies the patch and copies the source modules into the
  GeoNode build tree; running it twice is safe.

## Build or run the Cloud stack

From the repository root on `cn-vg`, run:

```sh
./veogeo/apply-overlay.sh
```

Then use the repository's documented Docker Compose command. The overlay
includes the PGConfig-backed GeoServer Cloud configuration and keeps demo
seeding opt-in through `seed-cloud-demo.sh`.

After changing files under `src/`, update the integration patch only where
GeoNode extension points need wiring. Keep the upstream checkout clean before
applying the overlay, so the blob check can verify the expected base.

The patch displays removed upstream warning lines ending in a space as
`<SPACE>` so the overlay source itself stays clean; the applicator restores the
original character while applying the patch.

`<VEOGEO_SPACE>`, `<VEOGEO_TAB>`, `<VEOGEO_CR>`, and
`<VEOGEO_BLANK_CONTEXT>` encode whitespace from the source diff so the patch
file itself stays clean. The applicator expands these markers before Git checks
or applies the patch.
