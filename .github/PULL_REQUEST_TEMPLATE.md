## What & why

<!-- What does this change and why? Link any related issue. -->

## Testing

<!-- How did you verify it? -->
- [ ] `pytest -q tests/` passes
- [ ] `scripts/fhirmini smoke` passes (if services were affected)
- [ ] Updated `docs/DECISIONS.md` for any non-obvious design choice

## Checklist

- [ ] No real PHI in code, tests, fixtures, or logs (synthetic data only)
- [ ] No secrets committed; no machine-specific absolute paths
- [ ] Services still bind to loopback by default
