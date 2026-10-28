"""Build the Do I Move My Car? static site: NYC alternate side parking (ASP) status and block-by-block schedules.

Today's status comes from the NYC 311 Public API calendar (includes emergency suspensions) when NYC_API_KEY
is set, otherwise from DOT's published holiday calendar (.ics). Block schedules come from NYC Open Data's
Parking Regulation Locations and Signs dataset (street-cleaning "broom" signs). Standard library only.

    python build.py            # fetch everything and build into dist/
    python build.py --offline  # rebuild from data/ without fetching
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
SIGNS = ROOT / "data" / "signs.json"
CALENDAR = ROOT / "data" / "calendar.json"

SIGNS_API = "https://data.cityofnewyork.us/resource/nfid-uabd.json"
SIGNS_WHERE = "record_type='Current' AND sign_description like '%BROOM%'"
ICS_URL = "https://www.nyc.gov/html/dot/downloads/misc/{year}-alternate-side.ics"
NYC311_URL = "https://api.nyc.gov/public/api/GetCalendar"
PAGE_SIZE = 50000
THIN_BLOCKS = 2       # streets with this many blocks or fewer are noindexed (kept on the site and in search)
DAYS_AHEAD = 14       # days shown on the site and in calendar.json
FEED_AHEAD = 89       # days of 311 status fetched for the calendar feed (the API caps ranges at 90)
FEED_URL_PATH = "asp-suspensions.ics"

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_WORDS = {"MON": 0, "MONDAY": 0, "TUE": 1, "TUES": 1, "TUESDAY": 1, "WED": 2, "WEDNESDAY": 2,
             "THU": 3, "THUR": 3, "THURS": 3, "THURSDAY": 3, "FRI": 4, "FRIDAY": 4,
             "SAT": 5, "SATURDAY": 5, "SUN": 6, "SUNDAY": 6}
TIME = r"(\d{1,2}(?::\d{2})?(?:AM|PM)|NOON|MIDNIGHT)"
SIDES = {"N": "North", "S": "South", "E": "East", "W": "West"}
BOROUGH_SLUGS = {"Manhattan": "manhattan", "Bronx": "bronx", "Brooklyn": "brooklyn", "Queens": "queens",
                 "Staten Island": "staten-island"}
UA = {"User-Agent": "doimovemycar.com (static site build)"}


# ---------------------------------------------------------------- fetching

def get(url, headers=None, tries=4):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == tries - 1:
                raise
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise
        time.sleep(2 ** attempt)


def fetch_signs():
    """Street-cleaning signs grouped to one row per block side and schedule."""
    rows, offset = [], 0
    token = os.environ.get("SOCRATA_APP_TOKEN")
    while True:
        params = {"$select": "borough,on_street,from_street,to_street,side_of_street,sign_description,count(*) as n",
                  "$where": SIGNS_WHERE,
                  "$group": "borough,on_street,from_street,to_street,side_of_street,sign_description",
                  "$order": "borough,on_street,from_street,to_street,side_of_street,sign_description",
                  "$limit": PAGE_SIZE, "$offset": offset}
        page = json.loads(get(SIGNS_API + "?" + urllib.parse.urlencode(params),
                              {"X-App-Token": token} if token else None))
        rows += page
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def ics_suspensions(year):
    """{date: reason} from DOT's published calendar for a year (holiday suspensions only)."""
    try:
        text = get(ICS_URL.format(year=year)).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return {}  # next year's calendar isn't always published yet
    text = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))  # unfold continued lines
    out = {}
    for event in text.split("BEGIN:VEVENT")[1:]:
        start = re.search(r"^DTSTART[^:]*:(\d{8})", event, re.M)
        end = re.search(r"^DTEND[^:]*:(\d{8})", event, re.M)
        desc = re.search(r"^DESCRIPTION[^:]*:(.*)$", event, re.M)
        if not start:
            continue
        first = dt.datetime.strptime(start.group(1), "%Y%m%d").date()
        last = dt.datetime.strptime(end.group(1), "%Y%m%d").date() - dt.timedelta(days=1) if end else first
        text_desc = desc.group(1).replace("\\,", ",").replace("\\n", " ") if desc else ""
        reason = re.search(r"suspended for (.+?)\.", text_desc)
        meters = "meters will not be in effect" not in text_desc.lower()
        day = first
        while day <= max(first, last):
            out[day.isoformat()] = {"reason": reason.group(1) if reason else "a holiday", "meters": meters}
            day += dt.timedelta(days=1)
    return out


def nyc311_days(today, key):
    """({date: parking item}, {date: collection item}, types seen) from the 311 Public API, which includes
    emergency suspensions. The API returns at most ~45 days per request, so ask in 30-day windows."""
    out, trash, types = {}, {}, {}
    for offset in range(0, FEED_AHEAD + 1, 30):
        start = today + dt.timedelta(days=offset)
        end = min(start + dt.timedelta(days=29), today + dt.timedelta(days=FEED_AHEAD))
        params = {"fromdate": start.strftime("%m/%d/%Y"), "todate": end.strftime("%m/%d/%Y")}
        data = json.loads(get(NYC311_URL + "?" + urllib.parse.urlencode(params), {"Ocp-Apim-Subscription-Key": key}))
        if not data.get("days"):
            print(f"  311 calendar: no days for {start}..{end}, top-level keys {sorted(data)[:10]}")
        for day in data.get("days", []):
            date = dt.datetime.strptime(str(day["today_id"]), "%Y%m%d").date().isoformat()
            for item in day.get("items", []):
                kind = item.get("type", "")
                types.setdefault(kind, set()).add(item.get("status", ""))
                entry = {"status": item.get("status", ""), "reason": item.get("exceptionName") or "",
                         "details": item.get("details", "")}
                if "parking" in kind.lower():
                    out[date] = entry
                elif any(w in kind.lower() for w in ("collection", "sanitation", "trash", "garbage")):
                    trash[date] = entry
    return out, trash, {k: sorted(v) for k, v in types.items()}


def fetch_calendar(today):
    key = os.environ.get("NYC_API_KEY")
    holidays = {}
    for year in {today.year, (today + dt.timedelta(days=FEED_AHEAD)).year}:
        holidays.update(ics_suspensions(year))
    live, trash, types = {}, {}, {}
    if key:
        try:
            live, trash, types = nyc311_days(today, key)
        except (urllib.error.URLError, ValueError, KeyError) as e:
            print(f"  311 calendar unavailable ({e}); using the DOT holiday calendar only")
    return {"built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "source": "311" if live else "ics",
            "holidays": holidays, "live": live, "collections": trash, "types": types}


def day_status(day, cal):
    """('in_effect' | 'suspended' | 'sunday', reason) for one date."""
    iso = day.isoformat()
    if day.weekday() == 6:  # no street cleaning on Sundays, whatever the calendar says
        return "sunday", ""
    live = cal["live"].get(iso)
    if live:
        status = live["status"].upper()
        if "SUSPEND" in status or "NOT IN EFFECT" in status:
            return "suspended", live["reason"] or cal["holidays"].get(iso, {}).get("reason", "")
        if "IN EFFECT" in status:
            return "in_effect", ""
    if iso in cal["holidays"]:
        return "suspended", cal["holidays"][iso]["reason"]
    return "in_effect", ""


def collection_status(day, cal):
    """('normal' | 'suspended', reason) for DSNY trash/recycling/compost pickup, or None if 311 didn't say.
    Sundays return None: most of the city has no pickup then anyway."""
    item = cal.get("collections", {}).get(day.isoformat())
    if not item or day.weekday() == 6:
        return None
    status = item["status"].upper()
    if any(w in status for w in ("SUSPEND", "CANCEL", "NOT IN EFFECT", "NO COLLECTION")):
        return "suspended", item["reason"]
    return "normal", ""


def trash_today(state):
    if not state:
        return ""
    if state[0] == "suspended":
        return "Trash & recycling pickup today is suspended" + (f" for {state[1]}" if state[1] else "") + "."
    return "Trash & recycling pickup today is on schedule."


# ---------------------------------------------------------------- shaping

def parse_schedule(desc):
    """(days, start, end) from a sign description, e.g. ((0, 3), '9AM', '10:30AM'); None if unreadable."""
    d = re.sub(r"\(SUPERSED.*?\)", "", desc.upper())
    d = re.sub(r"(\d)\s*([AP])\s*M\b", r"\1\2M", d)  # "10A M" -> "10AM"
    m = re.search(TIME + r"\s*(?:-|TO)\s*" + TIME, d)
    if not m:
        return None
    words = d.split("SYMBOL)")[-1]
    days = set()
    for a, b in re.findall(r"([A-Z]+)\s*(?:-|THRU|THROUGH)\s*([A-Z]+)", words):
        if a in DAY_WORDS and b in DAY_WORDS:
            days |= set(range(DAY_WORDS[a], DAY_WORDS[b] + 1))
    days |= {DAY_WORDS[w] for w in re.findall(r"[A-Z]+", words[:words.find(m.group(0))]) if w in DAY_WORDS}
    if "EXCEPT SUN" in words:
        days = (set(range(6)) - days) if days else set(range(6))
    if not days:
        return None
    return tuple(sorted(days)), m.group(1), m.group(2)


NBSP = chr(0xA0)  # non-breaking space: keeps "9:30 am" from wrapping in narrow table cells


def fmt_time(t):
    if t in ("NOON", "MIDNIGHT"):
        return t.lower()
    return t.replace(":00", "").lower().replace("am", NBSP + "am").replace("pm", NBSP + "pm")


def fmt_range(start, end):
    """'8AM', '9:30AM' -> '8-9:30 am'; keeps both halves when they differ ('11:30 am-1 pm')."""
    a, b = fmt_time(start), fmt_time(end)
    if a[-3:] == b[-3:] and a[-3] == NBSP:
        a = a[:-3]
    return f"{a}–{b}"



def natural(text):
    """Sort key that puts East 99 Street before East 100 Street."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def fmt_days(days):
    if days == tuple(range(6)):
        return "Mon–Sat"
    if days == tuple(range(5)):
        return "Mon–Fri"
    return " & ".join(DAY_SHORT[d] for d in days)


def street_name(raw):
    words = re.sub(r"\s+", " ", (raw or "").strip()).split(" ")
    return " ".join(w if re.match(r"^\d", w) else w.capitalize() for w in words if w)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def shape(rows):
    """{(borough, street): {"name", "path", "blocks": [...]}} with one entry per block side and schedule."""
    streets = {}
    for r in rows:
        boro = street_name(r.get("borough"))
        if boro not in BOROUGH_SLUGS or not r.get("on_street"):
            continue
        name = street_name(r["on_street"])
        s = streets.setdefault((boro, name), {"name": name, "borough": boro, "blocks": [],
                                              "path": f"street/{BOROUGH_SLUGS[boro]}/{slugify(name)}/"})
        sched = parse_schedule(r["sign_description"])
        s["blocks"].append({
            "from": street_name(r.get("from_street")), "to": street_name(r.get("to_street")),
            "side": SIDES.get((r.get("side_of_street") or "").strip().upper(), ""),
            "days": list(sched[0]) if sched else [],
            "when": f"{fmt_days(sched[0])}, {fmt_range(sched[1], sched[2])}" if sched else clean_desc(r["sign_description"]),
            "sort": (sched[1] if sched else ""),
        })
    for s in streets.values():
        # one row per block side + schedule; a long block can repeat the same sign many times
        seen, blocks = set(), []
        for b in sorted(s["blocks"], key=lambda b: (natural(b["from"]), natural(b["to"]), b["side"], b["when"])):
            k = (b["from"], b["to"], b["side"], b["when"])
            if k not in seen:
                seen.add(k)
                blocks.append(b)
        s["blocks"] = blocks
        s["n_blocks"] = len({(b["from"], b["to"]) for b in blocks})
    return streets


def clean_desc(desc):
    d = re.sub(r"\(SUPERSED.*?\)|\(DON'T LITTER\)|<-*>|-->|<--|\(SANITATION BROOM SYMBOL\)", "", desc)
    return re.sub(r"\s+", " ", d).strip().capitalize()


# ---------------------------------------------------------------- html

esc = html.escape


def fmt_date(day):
    return f"{day:%a}, {day:%b} {day.day}"


class Site:
    def __init__(self, cfg, today):
        self.cfg, self.today, self.urls = cfg, today, []
        self.base = cfg["base_path"].rstrip("/") + "/"

    def url(self, path=""):
        return self.base + path

    def ad(self, slot_key):
        client, slot = self.cfg.get("adsense_client"), self.cfg.get(slot_key)
        if not (client and slot):
            return ""
        return (f'<ins class="adsbygoogle ad" style="display:block" data-ad-client="{esc(client)}" data-ad-slot="{esc(slot)}"'
                ' data-ad-format="auto" data-full-width-responsive="true"></ins><script>(adsbygoogle=window.adsbygoogle||[]).push({});</script>')

    def shop(self, heading="Gear for NYC street parkers"):
        tag = self.cfg.get("amazon_tag")
        items = []
        for p in self.cfg.get("amazon_products", []):
            q = {"k": p["query"]}
            if tag:
                q["tag"] = tag
            href = "https://www.amazon.com/s?" + urllib.parse.urlencode(q)
            items.append(f'<li><a href="{esc(href)}" rel="sponsored nofollow noopener" target="_blank">'
                         f'{esc(p["label"])}</a><span>{esc(p["note"])}</span></li>')
        for r in self.cfg.get("referrals", []):
            if r.get("url"):
                items.append(f'<li><a href="{esc(r["url"])}" rel="sponsored nofollow noopener" target="_blank">'
                             f'{esc(r["label"])}</a><span>{esc(r["note"])}</span></li>')
        if not items:
            return ""
        # Amazon's Operating Agreement requires this exact sentence wherever Associates links appear
        amazon = " As an Amazon Associate I earn from qualifying purchases." if tag else ""
        return (f'<aside class="card shop"><h2>{esc(heading)}</h2><ul>{"".join(items)}</ul>'
                "<p class=\"fine\">We may earn a commission from these links. It doesn't change what you pay."
                f'{amazon}</p></aside>')

    def page(self, path, title, description, body, index=True):
        cfg = self.cfg
        canonical = cfg["base_url"].rstrip("/") + "/" + path
        head = ""
        if cfg.get("adsense_client"):
            head += (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js'
                     f'?client={esc(cfg["adsense_client"])}" crossorigin="anonymous"></script>')
        if cfg.get("ga4_id"):
            gid = esc(cfg["ga4_id"])
            head += (f'<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>'
                     "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}"
                     f"gtag('js',new Date());gtag('config','{gid}');</script>")
        social = "".join(f' · <a href="{esc(s["url"])}" rel="me">{esc(s["label"])}</a>' for s in cfg.get("social", []))
        doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image" content="{esc(cfg['base_url'].rstrip('/'))}/static/share.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="{self.url('favicon.ico')}" sizes="any">
<link rel="icon" href="{self.url('static/icon.svg')}" type="image/svg+xml">
<link rel="apple-touch-icon" href="{self.url('static/apple-touch-icon.png')}">
<link rel="stylesheet" href="{self.url('static/style.css')}">
{head}{"" if index else '<meta name="robots" content="noindex,follow">'}
</head>
<body data-base="{self.base}">
<header class="top"><a class="brand" href="{self.url()}">{esc(cfg['site_name'])}</a>
<nav><a href="{self.url('#search')}">Find your street</a><a href="{self.url('rules/')}">The rules</a></nav></header>
<main>
{body}
</main>
<footer>
<p>Suspensions from NYC 311 and the NYC DOT calendar; street-cleaning signs from <a href="https://opendata.cityofnewyork.us/">NYC Open Data</a>,
updated {fmt_date(self.today)}. Always follow the signs posted on your block. This site is not affiliated with the City of New York.</p>
<p><a href="{self.url('about/')}">About</a> · <a href="{self.url('privacy/')}">Privacy</a> · <a href="mailto:{esc(cfg['contact_email'])}">Contact</a>{social}</p>
</footer>
<script src="{self.url('static/app.js')}" defer></script>
</body>
</html>
"""
        out = DIST / path / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(doc, encoding="utf-8")
        if index:
            self.urls.append(canonical)


def week_strip(site, cal):
    cells = []
    for i in range(7):
        day = site.today + dt.timedelta(days=i)
        status, reason = day_status(day, cal)
        label = {"in_effect": "In effect", "suspended": "Suspended", "sunday": "No ASP"}[status]
        note = esc(reason) if reason else ("Sunday" if status == "sunday" else "Move for the signs")
        cells.append(f'<li class="d {status}" data-date="{day.isoformat()}"><b>{"Today" if i == 0 else "Tomorrow" if i == 1 else f"{day:%a}"}</b>'
                     f'<span class="dt">{day:%b} {day.day}</span><span class="st">{label}</span><span class="why">{note}</span>'
                     + ('<span class="tr">🗑️ No trash pickup</span>' if (collection_status(day, cal) or ("",))[0] == "suspended" else "")
                     + '</li>')
    return f'<ol class="week">{"".join(cells)}</ol>'


def answer_block(site, cal):
    status, reason = day_status(site.today, cal)
    if status == "in_effect":
        a, why = "YES", "Alternate side parking is in effect today. Move your car for the times on your block's signs."
    elif status == "suspended":
        a, why = "NO", f"Alternate side parking is suspended today for {reason}." if reason else "Alternate side parking is suspended today."
    else:
        a, why = "NO", "Alternate side parking isn't in effect on Sundays."
    trash = trash_today(collection_status(site.today, cal))
    trash_html = (f'<p class="trash" data-trash>🗑️ {esc(trash)}</p>' if trash
                  else '<p class="trash" data-trash hidden></p>')
    return (f'<section class="hero" id="answer"><p class="q">Do I have to move my car today for alternate side parking?</p>'
            f'<p class="a {"yes" if a == "YES" else "no"}" data-live>{a}</p><p class="why" data-why>{esc(why)}</p>'
            f'{trash_html}</section>')


RULES = """<ul class="rules">
<li><b>Signs rule.</b> Street cleaning signs (a red <b>P</b> with a broom through it) show the days and times you can't park on that side of the block.</li>
<li><b>Suspensions.</b> ASP is suspended on dozens of legal and religious holidays a year, and sometimes for snow or emergencies. On a suspension day you don't have to move.</li>
<li><b>Meters.</b> On major legal holidays (like Thanksgiving and Christmas) parking meters are suspended too. On other suspension days, meters stay in effect.</li>
<li><b>Sundays.</b> Street cleaning rules don't apply on Sundays.</li>
</ul>"""


def build_home(site, cal, streets):
    upcoming = sorted((d, h) for d, h in cal["holidays"].items()
                      if d >= site.today.isoformat() and dt.date.fromisoformat(d).weekday() != 6)[:8]  # Sundays are off anyway
    hol = "".join(f'<li><b>{fmt_date(dt.date.fromisoformat(d))}</b> {esc(h["reason"])}'
                  f'<span class="muted">{" · meters in effect" if h["meters"] else " · meters suspended too"}</span></li>'
                  for d, h in upcoming)
    boros = "".join(f'<li><a href="{site.url("street/" + slug + "/")}">{b}</a> <span class="muted">'
                    f'{n_streets(streets, b)}</span></li>' for b, slug in BOROUGH_SLUGS.items())
    body = f"""{answer_block(site, cal)}
{week_strip(site, cal)}
<section class="card search" id="search"><h2>When do I move my car on my street?</h2>
<input type="search" id="q" placeholder="e.g. Morris Ave or 82 St" autocomplete="off" aria-label="Search by street name">
<ul id="results"></ul>
<p class="fine">Street-cleaning times for {sum(s['n_blocks'] for s in streets.values()):,} blocks on {len(streets):,} streets, from the city's sign records.</p></section>
{site.ad("adsense_slot_top")}
<section class="card"><h2>Upcoming suspensions</h2><ul class="hols">{hol or '<li>No suspensions on the city calendar in the next weeks.</li>'}</ul>
<p class="fine">Snow and emergency suspensions are announced by 311, sometimes late the day before. This page updates every hour.</p></section>
{feed_card(site)}
<section class="card"><h2>Browse by borough</h2><ul class="zips">{boros}</ul></section>
<section class="card"><h2>The rules</h2>{RULES}</section>
{site.shop()}
<script type="application/json" id="cal">{json.dumps(calendar_json(site, cal))}</script>"""
    site.page("", f"{site.cfg['site_name']} Is alternate side parking in effect today in NYC?",
              "Is NYC alternate side parking in effect today? A live yes/no answer, this week's suspensions, "
              "and street-cleaning times for every block.", body)


def n_streets(streets, boro):
    n = sum(1 for (b, _) in streets if b == boro)
    return f"{n:,} street{'s' if n != 1 else ''}"


def calendar_json(site, cal):
    live = sorted(cal["live"])
    out = {"_meta": {"source": cal["source"], "built": cal["built"],  # which feed answered; app.js ignores it
                     "live_days": len(live), "live_last": live[-1] if live else None,
                     "collection_days": len(cal.get("collections", {})), "types": cal.get("types", {})}}
    for i in range(DAYS_AHEAD):
        day = site.today + dt.timedelta(days=i)
        status, reason = day_status(day, cal)
        trash = collection_status(day, cal)
        out[day.isoformat()] = [status, reason, list(trash) if trash else None]
    return out


CRLF = "\r\n"  # iCalendar line ending


def ics_escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line):
    """RFC 5545: lines over 75 octets continue on the next line after a leading space."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return CRLF.join(out)


def write_feed(site, cal):
    """Subscribable calendar of upcoming ASP suspensions (holidays plus any 311 snow/emergency ones)."""
    base = site.cfg["base_url"].rstrip("/") + "/"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    days = sorted({dt.date.fromisoformat(d) for d in list(cal["holidays"]) + list(cal["live"]) + list(cal.get("collections", {}))
                   if dt.date.fromisoformat(d) >= site.today - dt.timedelta(days=7)})
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{site.cfg['domain']}//ASP suspensions//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:NYC Alternate Side Parking & Trash Pickup Suspensions",
             "X-WR-CALDESC:" + ics_escape(f"Days NYC alternate side parking is suspended, from NYC 311 and DOT. {base}"),
             "X-WR-TIMEZONE:America/New_York", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    n = 0
    for day in days:
        trash = collection_status(day, cal)
        if trash and trash[0] == "suspended":
            lines += ["BEGIN:VEVENT", f"UID:trash-{day:%Y%m%d}@{site.cfg['domain']}", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
                      "SUMMARY:" + ics_escape("🗑️ No trash pickup: " + (trash[1] or "suspended")),
                      "DESCRIPTION:" + ics_escape(f"NYC Sanitation trash, recycling and compost pickup is suspended. {base}"),
                      f"URL:{base}", "TRANSP:TRANSPARENT", "END:VEVENT"]
            n += 1
        status, reason = day_status(day, cal)
        if status != "suspended":
            continue
        meters = cal["holidays"].get(day.isoformat(), {}).get("meters")
        meter_note = {True: " Parking meters are in effect.", False: " Parking meters are suspended too.", None: ""}[meters]
        desc = f"No need to move your car for alternate side parking.{meter_note} Always check your block's signs. {base}"
        lines += ["BEGIN:VEVENT", f"UID:asp-{day:%Y%m%d}@{site.cfg['domain']}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
                  "SUMMARY:" + ics_escape("🚗 No ASP: " + (reason or "suspended")),
                  "DESCRIPTION:" + ics_escape(desc), f"URL:{base}", "TRANSP:TRANSPARENT", "END:VEVENT"]
        n += 1
    lines.append("END:VCALENDAR")
    (DIST / FEED_URL_PATH).write_text(CRLF.join(ics_fold(line) for line in lines) + CRLF, encoding="utf-8", newline="")
    return n


def feed_card(site):
    host = site.cfg["base_url"].split("//", 1)[1].rstrip("/")
    webcal = f"webcal://{host}/{FEED_URL_PATH}"
    google = "https://calendar.google.com/calendar/render?cid=" + urllib.parse.quote(webcal, safe="")
    https = f"https://{host}/{FEED_URL_PATH}"
    bsky = next((s["url"] for s in site.cfg.get("social", []) if s["label"] == "Bluesky"), "")
    bluesky = f'follow us on <a href="{esc(bsky)}">Bluesky</a>' if bsky else "check this page"
    return f"""<section class="card feed" id="calendar"><h2>Get suspensions in your calendar</h2>
<p>Subscribe once and every alternate side parking and trash pickup suspension shows up on your phone, including snow and emergency ones when 311 announces them.</p>
<p class="feed-links"><a class="btn" href="{esc(google)}" rel="noopener" target="_blank">Add to Google Calendar</a>
<a class="btn" href="{esc(webcal)}">Add to Apple / Outlook</a></p>
<p class="fine">Other apps: subscribe to <code>{esc(https)}</code>. Calendar apps refresh on their own schedule (Google can take hours),
so for last-minute changes {bluesky}.</p></section>"""


def build_street(site, s):
    rows = "".join(
        f'<tr data-days="{",".join(map(str, b["days"]))}"><td>{esc(b["from"])} – {esc(b["to"])}</td>'
        f'<td>{esc(b["side"])}</td><td>{esc(b["when"])}</td></tr>' for b in s["blocks"])
    boro_slug = BOROUGH_SLUGS[s["borough"]]
    body = f"""<p class="crumbs"><a href="{site.url()}">Home</a> › <a href="{site.url('street/' + boro_slug + '/')}">{s['borough']}</a> › {esc(s['name'])}</p>
<h1>Alternate side parking on {esc(s['name'])}<span class="sub">{s['borough']}</span></h1>
<div class="card status" data-street-status><p><b data-today-label>Today:</b> <span data-today-text>checking…</span></p></div>
<p class="lede">Street-cleaning times for {s['n_blocks']:,} block{'s' if s['n_blocks'] != 1 else ''} of {esc(s['name'])}, from the city's sign records.
Rows highlighted are cleaned today.</p>
{site.ad("adsense_slot_inline")}
<section class="card"><h2>Block-by-block schedule</h2><div class="scroll"><table class="sched">
<thead><tr><th>Block</th><th>Side</th><th>No parking</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="fine">From NYC DOT sign records. Signs change; the sign on your block always wins.</p></section>
<section class="card"><h2>The rules</h2>{RULES}</section>
{site.shop()}"""
    site.page(s["path"], f"{s['name']}, {s['borough']} alternate side parking schedule",
              f"When to move your car for street cleaning on {s['name']} in {s['borough']}: block-by-block alternate side parking times.",
              body, index=s["n_blocks"] > THIN_BLOCKS)


def build_borough(site, boro, members):
    items = "".join(f'<li><a href="{site.url(s["path"])}">{esc(s["name"])}</a></li>'
                    for s in sorted(members, key=lambda s: s["name"]))
    body = f"""<p class="crumbs"><a href="{site.url()}">Home</a> › {boro}</p>
<h1>Alternate side parking in {boro}<span class="sub">{len(members):,} streets</span></h1>
<section class="card"><ul class="zips">{items}</ul></section>"""
    site.page(f"street/{BOROUGH_SLUGS[boro]}/", f"{boro} alternate side parking by street",
              f"Street-cleaning schedules for every street in {boro} with alternate side parking signs.", body)


def build_static_pages(site):
    site.page("rules/", "NYC alternate side parking rules", "How NYC alternate side parking works: signs, suspensions, meters and fines.",
              f"<h1>NYC alternate side parking rules</h1><section class=\"card\">{RULES}</section>")
    site.page("about/", "About", "Where this site's alternate side parking data comes from.", f"""<h1>About</h1>
<section class="card"><p>{esc(site.cfg['site_name'])} answers one question: do you have to move your car today? It checks the NYC 311 calendar
(which includes snow and emergency suspensions) every hour, with the NYC DOT holiday calendar as a backup, and lists street-cleaning
times for every block from the city's sign records on NYC Open Data.</p>
<p>It's an independent project, not affiliated with the City of New York. Data can be wrong or out of date. Always check the signs on your block.</p>
<p>Some links go to Amazon or other partners, and we may earn a commission at no cost to you. As an Amazon Associate I earn from qualifying purchases.</p>
<p>Questions or errors? Email <a href="mailto:{esc(site.cfg['contact_email'])}">{esc(site.cfg['contact_email'])}</a>.</p></section>""")
    site.page("privacy/", "Privacy policy", "What this site collects.", """<h1>Privacy policy</h1>
<section class="card"><p>This site has no accounts and doesn't ask for personal information. Street searches run in your browser.</p>
<p>We may use Google AdSense to show ads and Google Analytics to count visits. Google and its partners use cookies to serve ads
based on your visits to this and other sites. You can opt out of personalized ads at
<a href="https://www.google.com/settings/ads">Google Ads Settings</a>.</p></section>""")


def build(cal, rows, cfg, today):
    streets = shape(rows)
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(ROOT / "static", DIST / "static")
    # browsers request /favicon.ico at the site root regardless of <link> tags
    shutil.copy(ROOT / "static" / "favicon.ico", DIST / "favicon.ico")
    site = Site(cfg, today)
    build_home(site, cal, streets)
    build_static_pages(site)
    by_boro = {}
    for s in streets.values():
        build_street(site, s)
        by_boro.setdefault(s["borough"], []).append(s)
    for boro, members in by_boro.items():
        build_borough(site, boro, members)
    (DIST / "calendar.json").write_text(json.dumps(calendar_json(site, cal)), encoding="utf-8")
    n_feed = write_feed(site, cal)
    index = [[s["name"], s["borough"], s["path"]] for s in sorted(streets.values(), key=lambda s: -s["n_blocks"])]
    (DIST / "search.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    urls = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{today.isoformat()}</lastmod></url>" for u in site.urls)
    (DIST / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                                      f"{urls}</urlset>", encoding="utf-8")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {cfg['base_url'].rstrip('/')}/sitemap.xml\n")
    if cfg.get("adsense_client"):
        (DIST / "ads.txt").write_text(f"google.com, {cfg['adsense_client'].replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n")
    (DIST / ".nojekyll").write_text("")
    if cfg.get("domain"):
        (DIST / "CNAME").write_text(cfg["domain"] + "\n")
    print(f"Built {len(streets):,} street pages ({sum(s['n_blocks'] for s in streets.values()):,} blocks); "
          f"status source: {cal['source']}; today: {day_status(today, cal)[0]}; {n_feed} suspensions in the calendar feed")


def nyc_today():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # Windows without tzdata; EST is close enough for a date
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="rebuild from data/ without fetching")
    parser.add_argument("--keep-signs", action="store_true", help="reuse data/signs.json if it exists (hourly status builds)")
    args = parser.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    today = nyc_today()
    SIGNS.parent.mkdir(exist_ok=True)
    if args.offline or (args.keep_signs and SIGNS.exists()):
        rows = json.loads(SIGNS.read_text(encoding="utf-8"))
    else:
        rows = fetch_signs()
        SIGNS.write_text(json.dumps(rows), encoding="utf-8")
    if args.offline:
        cal = json.loads(CALENDAR.read_text(encoding="utf-8"))
    else:
        cal = fetch_calendar(today)
        CALENDAR.write_text(json.dumps(cal), encoding="utf-8")
    build(cal, rows, cfg, today)


if __name__ == "__main__":
    main()
