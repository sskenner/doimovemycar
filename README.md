# Do I Move My Car?

A static site that answers "Is NYC alternate side parking (ASP) in effect today?" and lists street-cleaning
times for every block, in the spirit of isthelrunning.com. Sister project of [No Heat NYC](https://noheatnyc.com).

- **Home page:** today's yes/no answer, the next 7 days, upcoming holiday suspensions, street search.
- **One page per street** (~3,800), block by block, with today's cleanings highlighted.
- **Money:** AdSense slots and referral links (garage booking, car insurance), shown once filled in `config.json`.

## Data

| What | Source |
|---|---|
| Today's status, incl. snow/emergency suspensions | NYC 311 Public API `GetCalendar` (api.nyc.gov), needs `NYC_API_KEY` |
| Holiday suspensions (fallback) | NYC DOT calendar, `nyc.gov/html/dot/downloads/misc/<year>-alternate-side.ics` |
| Block schedules | NYC Open Data "Parking Regulation Locations and Signs" (`nfid-uabd`), broom signs |

Without `NYC_API_KEY` the site still works from the DOT holiday calendar, but it won't know about weather or emergency suspensions.

## Run it

    python build.py              # fetch and build into dist/
    python build.py --offline    # rebuild from data/
    python -m http.server --directory dist

## Deploy

GitHub Pages via `.github/workflows/build.yml`, rebuilt hourly (signs refreshed daily). Secrets:
`NYC_API_KEY` (free: api-portal.nyc.gov → "NYC 311 Public Developers" product) and optionally `SOCRATA_APP_TOKEN`.

## Bluesky alerts

After each hourly build, `post_bsky.py` posts to [@doimovemycar.com](https://bsky.app/profile/doimovemycar.com) when:

- **Noon–midnight:** tomorrow's ASP is suspended, or ASP is back in effect tomorrow after a suspension.
- **5–11 AM:** ASP is suspended today for a reason that isn't on the holiday calendar (snow, emergencies).

Each alert names its date and is posted once. Needs a Bluesky app password saved as the `BSKY_APP_PASSWORD`
repository secret. Preview: `python post_bsky.py --dry-run --hour 19`.
