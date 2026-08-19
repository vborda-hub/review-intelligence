"""
App Store Review Scraper (v2)
Accepts known_ids set from run_weekly.py so deduplication is
handled by the central archive, not this file.
"""
import json, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

APPS = {
    "arena-club": {"name": "Arena Club",      "app_id": "6499444724"},
    "courtyard":  {"name": "Courtyard",        "app_id": "6748155184"},
    "rbt":        {"name": "Rips by Triumph",  "app_id": "6751921248"},
    "icybox":     {"name": "IcyBox",           "app_id": "6758816716"},
}
MAX_PAGES = 20
DELAY = 1.5

def _request(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def fetch_rating(app_id):
    try:
        d = _request(f"https://itunes.apple.com/us/lookup?id={app_id}")
        r = d.get("results", [{}])[0]
        return {"rating": r.get("averageUserRating",0),
                "rating_count": r.get("userRatingCount",0),
                "version": r.get("version",""),
                "updated": r.get("currentVersionReleaseDate","")[:10]}
    except: return {}

def fetch_page(app_id, page):
    url = f"https://itunes.apple.com/us/rss/customerreviews/id={app_id}/page={page}/sortBy=mostRecent/json"
    try:
        data = _request(url)
        entries = data.get("feed",{}).get("entry",[])
        if not entries: return []
        out = []
        for e in entries[1:]:
            try:
                out.append({
                    "id":      e["id"]["label"],
                    "stars":   int(e["im:rating"]["label"]),
                    "title":   e["title"]["label"],
                    "body":    e["content"]["label"],
                    "author":  e["author"]["name"]["label"],
                    "date":    e["updated"]["label"][:10],
                    "version": e.get("im:version",{}).get("label",""),
                })
            except: continue
        return out
    except urllib.error.HTTPError as ex:
        if ex.code == 404: return []
        raise
    except: return []

def classify(stars):
    return "positive" if stars>=4 else "neutral" if stars==3 else "negative"

def run(known_ids: set = None):
    if known_ids is None:
        known_ids = set()
    out = {"fetched_at": datetime.now(timezone.utc).isoformat(),
           "source": "app-store", "brands": {}}
    for brand_id, app in APPS.items():
        print(f"  {app['name']}...")
        rating = fetch_rating(app["app_id"])
        reviews = []
        for page in range(1, MAX_PAGES+1):
            pg = fetch_page(app["app_id"], page)
            if not pg: break
            reviews.extend(pg)
            if page < MAX_PAGES: time.sleep(DELAY)

        tagged, new_count = [], 0
        for rv in reviews:
            rv["sentiment"] = classify(rv["stars"])
            rv["is_new"]    = rv["id"] not in known_ids
            rv["brand"]     = brand_id
            rv["source"]    = "app-store"
            tagged.append(rv)
            if rv["is_new"]: new_count += 1

        out["brands"][brand_id] = {
            "name": app["name"], "app_id": app["app_id"],
            "rating": rating, "total_fetched": len(tagged),
            "new_this_run": new_count, "reviews": tagged,
        }
        print(f"    → {len(tagged)} fetched, {new_count} new  |  {rating.get('rating','?')}★")
    return out

if __name__ == "__main__":
    run()
