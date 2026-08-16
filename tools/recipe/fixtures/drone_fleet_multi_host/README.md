# Drone Fleet multi-host contract fixtures

These files keep the Recipe unit tests independent from sibling repository
checkouts while preserving the public/private boundary of the generator.

- `eu-input-v1.schema.json` is a snapshot of
  `hakoniwa-conductor/schemas/eu-input-v1.schema.json`.
- `eu-input.fleets.json` is the generated reference committed at
  `hakoniwa-conductor-pro/eu-config/eu-input.fleets.json`.

When either authority changes, update its snapshot in the same change that
updates the Business Pack translator contract. Production commands continue
to resolve the public schema and private Conductor PRO checkout explicitly;
these snapshots are test inputs only.
