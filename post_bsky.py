"""Post Do I Move My Car? alerts to Bluesky.

Runs after every hourly build and decides for itself whether anything is worth posting:

  tomorrow  6 PM-midnight: tomorrow's ASP is suspended ("no need to move your car tomorrow").
  back      6 PM-midnight: today was suspended and ASP is back tomorrow (people forget after a snow week).
  today     5-11 AM: ASP is suspended today but tomorrow-night's post couldn't have known (snow/emergency).

Each alert names its date, and the script skips any alert already in the account's recent posts, so the
hourly runs post it once. Reads data/calendar.json from the build. Needs BSKY_APP_PASSWORD (an app password,
not the account password); without it, it prints what it would post. Standard library only.

    python post_bsky.py --dry-run            # what would be posted right now
    python post_bsky.py --dry-run --hour 19  # ...as if it were 7 PM in New York
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from build import CALENDAR, ROOT, day_status

PDS = "https://bsky.social/xrpc/"
EVENING = range(18, 24)
MORNING = range(5, 12)


def nyc_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # Windows without tzdata
        return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)


def label(day):
    return f"{day:%a}, {day:%b} {day.day}"


def alerts(cal, today, hour):
    """[(text, dedupe_key)] that should be posted now."""
    tomorrow = today + dt.timedelta(days=1)
    t_status, t_reason = day_status(today, cal)
    n_status, n_reason = day_status(tomorrow, cal)
    out = []
    if hour in EVENING and n_status == "suspended":
        why = f" for {n_reason}" if n_reason else ""
        out.append((f"🚗 No need to move your car tomorrow ({label(tomorrow)}): NYC alternate side parking is suspended{why}.",
                    f"tomorrow ({label(tomorrow)})"))
    if hour in EVENING and t_status == "suspended" and n_status == "in_effect":
        out.append((f"⚠️ Heads up: NYC alternate side parking is back in effect tomorrow ({label(tomorrow)}). "
                    "Check the times on your block's signs.", f"back in effect tomorrow ({label(tomorrow)})"))
    if hour in MORNING and t_status == "suspended" and today.isoformat() not in cal["holidays"]:
        why = f" ({t_reason})" if t_reason else ""
        out.append((f"❄️ NYC alternate side parking is suspended today, {label(today)}{why}. You don't need to move your car.",
                    f"suspended today, {label(today)}"))
    return out


def intro(cal, today):
    """One-time welcome post naming the next suspension."""
    nxt = next((today + dt.timedelta(days=i) for i in range(1, 60)
                if day_status(today + dt.timedelta(days=i), cal)[0] == "suspended"), None)
    tail = ""
    if nxt:
        reason = day_status(nxt, cal)[1]
        tail = f" Next up: {reason + ', ' if reason else ''}{label(nxt)}."
    return ("👋 Do I Move My Car? answers the question every NYC driver asks: is alternate side parking in effect today? "
            "We'll post here the evening before every suspension, and when snow or an emergency suspends it." + tail,
            "answers the question every NYC driver asks")


def with_link(text, site):
    """Append the site link and return (text, facets); facet offsets are UTF-8 byte positions."""
    link = site.split("//", 1)[1].rstrip("/")
    head = text + "\n\nYour block's street-cleaning times: "
    start = len(head.encode("utf-8"))
    full = head + link
    return full, [{"index": {"byteStart": start, "byteEnd": start + len(link.encode("utf-8"))},
                   "features": [{"$type": "app.bsky.richtext.facet#link", "uri": site.rstrip("/") + "/"}]}]


def call(method, token=None, body=None, raw=None, content_type="application/json", query=None):
    url = PDS + method + ("?" + urllib.parse.urlencode(query) if query else "")
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print what would be posted, send nothing")
    parser.add_argument("--hour", type=int, help="pretend it's this hour in New York (testing)")
    parser.add_argument("--intro", action="store_true", help="post the one-time welcome post (checks posting works)")
    args = parser.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cal = json.loads(CALENDAR.read_text(encoding="utf-8"))
    now = nyc_now()
    hour = now.hour if args.hour is None else args.hour
    todo = [intro(cal, now.date())] if args.intro else alerts(cal, now.date(), hour)
    print(f"{now:%Y-%m-%d} hour {hour}: {len(todo)} alert(s) due (status source: {cal['source']})")
    for text, _ in todo:
        print(" ", text)

    password = os.environ.get("BSKY_APP_PASSWORD")
    if not todo or args.dry_run or not password:
        if todo and not args.dry_run:
            print("BSKY_APP_PASSWORD not set; not posting.")
        return

    session = call("com.atproto.server.createSession", body={"identifier": cfg["bluesky_handle"], "password": password})
    token, did = session["accessJwt"], session["did"]
    feed = call("app.bsky.feed.getAuthorFeed", token, query={"actor": did, "limit": 30, "filter": "posts_no_replies"})
    recent = [item["post"]["record"].get("text", "") for item in feed.get("feed", [])]
    thumb = None
    for text, key in todo:
        if any(key in r for r in recent):
            print(f"Already posted: {key}")
            continue
        if thumb is None:
            thumb = call("com.atproto.repo.uploadBlob", token, raw=(ROOT / "static" / "share.png").read_bytes(),
                         content_type="image/png")["blob"]
        full, facets = with_link(text, cfg["base_url"])
        record = {
            "$type": "app.bsky.feed.post", "text": full, "facets": facets, "langs": ["en"],
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "embed": {"$type": "app.bsky.embed.external", "external": {
                "uri": cfg["base_url"].rstrip("/") + "/", "title": cfg["site_name"],
                "description": "Is NYC alternate side parking in effect today? Live answer + every block's cleaning times.",
                "thumb": thumb}},
        }
        result = call("com.atproto.repo.createRecord", token,
                      body={"repo": did, "collection": "app.bsky.feed.post", "record": record})
        print(f"Posted: https://bsky.app/profile/{cfg['bluesky_handle']}/post/{result['uri'].rsplit('/', 1)[1]}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        sys.exit(f"Bluesky API error {e.code}: {e.read().decode(errors='replace')[:500]}")
