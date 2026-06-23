# Milestone 3: Change Detection

This milestone adds the first read-only analytical layer on top of daily ETF holdings.

## Added

- `etf_holding_changes` table.
- Indexes for holdings and change lookups.
- `changes.py` module.
- Latest / previous valid holdings date selection.
- Per-ETF rank calculation.
- Outer-join change detection for current vs previous holdings.
- New-position and removed-position detection.
- 1-day weight, share, and rank deltas.
- 3-day / 5-day / 10-day rolling weight deltas.
- Consecutive add/reduce counts.

## Not included yet

Signal generation is intentionally not part of this milestone. It will be implemented in the next milestone after change rows are stable.
