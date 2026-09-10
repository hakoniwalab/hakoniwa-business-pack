# Drone Fleet multi-host contract fixtures

These files keep the Recipe unit tests independent from sibling repository
checkouts while preserving the public input contract.

- `eu-input-v1.schema.json` is a snapshot of
  `hakoniwa-conductor/schemas/eu-input-v1.schema.json`.
- `eu-input.fleets.json` is the legacy 1-by-1 fleet input used to verify the
  Business Pack translation contract.

When the public contract changes, update its snapshot in the same change that
updates the Business Pack translator contract. Production commands resolve
the schema and generated runtime fixtures from the public
`hakoniwa-conductor` checkout; these snapshots are test inputs only.
