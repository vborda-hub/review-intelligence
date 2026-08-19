"""
Google Play Review Scraper (v2)
Accepts known_ids set from run_weekly.py so deduplication is
handled by the central archive, not this file.
Install: pip install google-play-scraper
"""
import time
from datetime import datetime, timezone

try:
    from google_play_scraper import app as gp_app, reviews as gp_reviews, Sort
except ImportError:
    raise ImportError("Run: pip install google-play-scraper")

APPS = {
    "arena-club": {"name": "Arena Club",      "package": "com.arenaclub.mobile"},
    "courtyard":  {"name": "Courtyard",        "package": "io.courtyard.app"},
    "rbt":        {"name": "Rips by Triumph",  "package": "com.triumpharcade.tcg"},
    # IcyBox: iOS only
}
REVIEWS_PER_APP = 500
DELAY = 2.0

def fetch_info(package):
    try:
        i = gp_app(package, lang="en", country="us")
        return {"rating": round(i.get("score",0),2),
                "rating_count": i.get("ratings",0),
                "installs": i.get("realInstalls",0),
                "version": i.get("version",""),
                "updated": str(i.get("updated",""))}
    except: return {}

def classify(stars):
    return "positive" if stars>=4 else "neutral" if stars==3 else "negative"

def run(known_ids: set = None):
    if known_ids is None:
        known_ids = set()
    out = {"fetched_at": datetime.now(timezone.utc).isoformat(),
           "source": "google-play", "brands": {}}
    for brand_id, app in APPS.items():
        print(f"  {app['name']}...")
        info = fetch_info(app["package"])
        try:
            result, _ = gp_reviews(app["package"], lang="en", country="us",
                                    sort=Sort.NEWEST, count=REVIEWS_PER_APP)
        except Exception as e:
            print(f"    ⚠️  {e}")
            result = []

        tagged, new_count = [], 0
        for r in result:
            rv = {
                "id":       r.get("reviewId",""),
                "stars":    r.get("score",0),
                "title":    r.get("title") or "",
                "body":     r.get("content",""),
                "author":   r.get("userName","Anonymous"),
                "date":     r.get("at").strftime("%Y-%m-%d") if r.get("at") else "",
                "thumbs_up": r.get("thumbsUpCount",0),
                "dev_reply": bool(r.get("replyContent")),
                "brand":    brand_id,
                "source":   "google-play",
            }
            rv["sentiment"] = classify(rv["stars"])
            rv["is_new"]    = rv["id"] not in known_ids
            tagged.append(rv)
            if rv["is_new"]: new_count += 1

        out["brands"][brand_id] = {
            "name": app["name"], "package": app["package"],
            "info": info, "total_fetched": len(tagged),
            "new_this_run": new_count, "reviews": tagged,
        }
        print(f"    → {len(tagged)} fetched, {new_count} new  |  {info.get('rating','?')}★")
        time.sleep(DELAY)

    out["notes"] = ["IcyBox excluded — iOS only."]
    return out

if __name__ == "__main__":
    run()
