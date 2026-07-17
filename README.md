# avs-price-tracker

Daily, automated pull of secondary-market pricing for every Colorado Avalanche
home game at Ball Arena, appended to `data/prices.csv`. Event-level data only,
from sanctioned APIs (SeatGeek, Ticketmaster) — no scraping, no headless
browsers, no undocumented endpoints.

## Data sources and current status

**Ticketmaster** — Discovery API v2, confirmed working. The standard/free API
key is **not** authorized for the Inventory Status API
(`/inventory-status/v1/availability` returns `401 Invalid ApiKey for given
resource`), so this project uses the Discovery API v2 `priceRanges` fallback
as directed. Consequence: Discovery API doesn't distinguish primary vs.
resale pricing, so Ticketmaster rows only populate `lowest_price` /
`highest_price`; `primary_min/max` and `resale_min/max` are always empty for
`source=ticketmaster`. Those four columns exist in the schema in case
Inventory Status access is granted later.

**SeatGeek** — not yet tested live; credentials are pending approval. The
code is written against SeatGeek's documented `/2/events` response shape
(`performers.slug`, `venue.slug` filters; `stats.lowest_price`,
`average_price`, `median_price`, `highest_price`, `listing_count`). **Verify
these against a real response as soon as credentials arrive** — run
`pull_prices.py` locally with `SEATGEEK_CLIENT_ID` set and check the log for
warnings about missing fields before trusting the SeatGeek rows.

## Setup

1. `pip install -r requirements.txt` (on Windows this also installs `tzdata`,
   since stdlib `zoneinfo` needs a timezone database and Windows doesn't ship
   one — Linux/macOS and GitHub Actions' `ubuntu-latest` already have one at
   the OS level, so the marker skips it there)
2. Copy `.env` (already gitignored) and fill in:
   - `SEATGEEK_CLIENT_ID`, `SEATGEEK_CLIENT_SECRET`
   - `TICKETMASTER_API_KEY`
3. Fill in your tier assignments (A/B/C/D) for every opponent in
   `config.yaml` under `tiers:` — they're seeded with `TBD` placeholders so
   nothing silently lands as `UNMAPPED` just because you haven't gotten to it
   yet. `UNMAPPED` in the output means an opponent name genuinely wasn't
   found in the map at all (e.g. an API name-parsing mismatch) — that's the
   case worth investigating.
4. `python pull_prices.py`

## Running

```
python pull_prices.py
```

Reads `SEATGEEK_CLIENT_ID` / `SEATGEEK_CLIENT_SECRET` / `TICKETMASTER_API_KEY`
from the environment, pulls all future Avalanche home games from both
sources, and appends new rows to `data/prices.csv`. Safe to re-run manually
any number of times per day — rows are keyed on `(pull_date, game_date,
source)`, so a second run the same day adds zero new rows.

If one source errors, the script logs it and continues with the other
(fail-soft). It only exits nonzero — which shows red in GitHub Actions and
notifies you — if **both** sources fail.

## Output schema (`data/prices.csv`)

One row per `(pull_date, game_date, source)`.

| Column | Notes |
|---|---|
| `pull_date` | YYYY-MM-DD, America/Denver |
| `game_date` | YYYY-MM-DD |
| `game_time_local` | HH:MM, America/Denver |
| `opponent` | e.g. "Chicago Blackhawks" |
| `day_of_week` | Mon–Sun |
| `days_until_game` | integer |
| `tier` | from `config.yaml` tier map; `UNMAPPED` if the opponent name isn't a key at all |
| `source` | `seatgeek` or `ticketmaster` |
| `onsale_status` | Ticketmaster `dates.status.code` (e.g. `offsale`, `onsale`, `cancelled`); empty for SeatGeek, which has no equivalent field. Watch this to see exactly when a game flips to on-sale. |
| `lowest_price` | float or empty |
| `average_price` | float or empty (SeatGeek only) |
| `median_price` | float or empty (SeatGeek only) |
| `highest_price` | float or empty |
| `listing_count` | int or empty (SeatGeek only) |
| `resale_min` / `resale_max` | floats or empty (would require Ticketmaster Inventory Status, which this key doesn't have — always empty for now) |
| `primary_min` / `primary_max` | floats or empty (same — always empty for now) |
| `avg_pct_of_114_face` | `average_price / 118 − 1`, as a percentage, 1 decimal. SeatGeek only, since `average_price` is SeatGeek-only. |
| `avg_pct_of_363_face` | `average_price / 100 − 1`, as a percentage, 1 decimal. SeatGeek only. |

**The `avg_pct_of_*_face` columns are event-wide demand proxies, not your
clearing price.** They're `average_price` (the whole secondary market for
that event) divided against your two sections' face values — useful for
tracking demand trend by opponent/day-of-week, not for predicting what your
Section 114 or 363 seats will actually sell for.

### Why most rows currently have empty price columns

As of this writing, none of the 2026-27 season's individual-game tickets are
on sale yet (Ticketmaster's `sales.public.startDateTime` shows an August 2026
on-sale date). Discovery API only populates `priceRanges` once an event's
`dates.status.code` flips to `onsale`. Until then, every Ticketmaster row
will have empty `lowest_price`/`highest_price` with `onsale_status=offsale` —
that's expected, not a bug. Watch the `onsale_status` column; pricing data
should start showing up automatically once games go on sale, no code changes
needed.

## GitHub Actions

`.github/workflows/daily-pull.yml` runs on a `0 14 * * *` UTC cron (~8am
Denver in winter, ~7am in summer) plus `workflow_dispatch` for manual runs.
It installs dependencies, runs `pull_prices.py` with the three secrets
(`SEATGEEK_CLIENT_ID`, `SEATGEEK_CLIENT_SECRET`, `TICKETMASTER_API_KEY`)
injected as env vars, and commits `data/prices.csv` if it changed.

Add the three secrets under repo Settings → Secrets and variables → Actions.

### Platform gotchas

- **Scheduled runs aren't exact.** GitHub Actions can delay scheduled
  workflows 10–30 minutes under load. Irrelevant for a daily pull — no need
  to engineer around it.
- **GitHub disables scheduled workflows after 60 days of repo inactivity.**
  The daily CSV commit itself keeps the repo active, so this self-mitigates
  during the season. If the season ends and pulls stop for a while, the
  schedule may get disabled — just re-enable it under the Actions tab (or
  push any commit) before next season.

## Project layout

```
avs-price-tracker/
├── .github/workflows/daily-pull.yml
├── config.yaml
├── pull_prices.py
├── requirements.txt
├── data/prices.csv
└── README.md
```
