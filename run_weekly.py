"""
Weekly Review Intelligence Runner — v3
=======================================
Scrapes App Store + Google Play for Arena Club and 3 competitors,
manages persistent data, regenerates dashboard.html, and pushes to GitHub.

FILES MANAGED:
  data/archive.json            — every review ever (append-only)
  data/ratings_history.json    — weekly rating snapshots
  data/copy_bank.json          — 5-star Arena Club quotes (keep forever, rotate 9)
  data/insights_history.json   — weekly sentiment snapshots + delta tracking
  data/insights_config.json    — locked Platforms/Segments/30-Day Plan sections
  data/weekly/YYYY-MM-DD.json  — this week's new reviews only

SCHEDULE:
  Every Monday at 9am — run automatically via Cowork scheduled task
  (Requires desktop app to be running; scraper needs internet access)

SETUP (one time):
  pip install google-play-scraper
  python3 run_weekly.py --initial   # seed 90-day history on first run
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
WEEKLY_DIR = DATA_DIR / "weekly"
DATA_DIR.mkdir(exist_ok=True)
WEEKLY_DIR.mkdir(exist_ok=True)

ARCHIVE_PATH          = DATA_DIR / "archive.json"
RATINGS_HISTORY_PATH  = DATA_DIR / "ratings_history.json"
COPY_BANK_PATH        = DATA_DIR / "copy_bank.json"
INSIGHTS_HISTORY_PATH = DATA_DIR / "insights_history.json"
INSIGHTS_CONFIG_PATH  = DATA_DIR / "insights_config.json"

# ─── Config ───────────────────────────────────────────────────────────────────
COPY_BANK_SHOW_COUNT = 9      # quotes rotated into the dashboard each week
SWING_THRESHOLD      = 0.10   # +-10% competitor neg-rate swing triggers static regen
GIT_AUTO_PUSH        = True   # set False to skip git push (manual deploys only)

BRAND_NAMES = {
    "arena-club": "Arena Club",
    "courtyard":  "Courtyard",
    "rbt":        "Rips by Triumph",
    "icybox":     "IcyBox",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────
def load_json(path, default):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return default
    return default

def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_archive(path):
    data = load_json(path, [])
    if isinstance(data, dict):
        return list(data.values())
    return data

def merge_new(archive, new_records):
    """Append only records not already in archive."""
    existing_ids = {r.get("id") for r in archive}
    added = 0
    for r in new_records:
        if r.get("id") not in existing_ids:
            archive.append(r)
            existing_ids.add(r.get("id"))
            added += 1
    return archive, added

# ─── Copy Bank ────────────────────────────────────────────────────────────────
def update_copy_bank(all_new_reviews):
    """
    Add new 5-star Arena Club reviews to the permanent bank.
    Rotate 9 quotes into 'in_rotation' for dashboard display.
    Quotes are never deleted. Least-shown rotate in first.
    """
    bank = load_json(COPY_BANK_PATH, {
        "quotes": [], "in_rotation": [],
        "show_count": COPY_BANK_SHOW_COUNT, "updated_at": today_str(),
    })
    existing_ids = {q["id"] for q in bank["quotes"]}
    week = today_str()
    added = 0

    for r in all_new_reviews:
        if (r.get("brand") == "arena-club" and r.get("stars") == 5
                and r.get("id") not in existing_ids
                and len(r.get("body", "").strip()) >= 40):
            bank["quotes"].append({
                "id":          r["id"],
                "text":        r["body"].strip()[:300],
                "author":      r.get("author", "Anonymous"),
                "stars":       5,
                "date":        r.get("date", week),
                "source":      r.get("source", "app-store"),
                "added_week":  week,
                "times_shown": 0,
            })
            existing_ids.add(r["id"])
            added += 1

    # Rotate: least-shown first, then newest
    pool_sorted = sorted(
        bank["quotes"],
        key=lambda q: (q.get("times_shown", 0), q.get("added_week", "") or "")
    )
    in_rotation = pool_sorted[:COPY_BANK_SHOW_COUNT]
    rotation_ids = {q["id"] for q in in_rotation}
    for q in bank["quotes"]:
        if q["id"] in rotation_ids:
            q["times_shown"] = q.get("times_shown", 0) + 1

    bank["in_rotation"] = in_rotation
    bank["updated_at"] = week
    save_json(COPY_BANK_PATH, bank)
    print(f"  Copy bank: {len(bank['quotes'])} total, {added} new, {len(in_rotation)} in rotation")
    return bank

# ─── Sentiment stats ──────────────────────────────────────────────────────────
def compute_brand_stats(all_reviews):
    stats = {}
    for r in all_reviews:
        brand = r.get("brand", "unknown")
        if brand not in stats:
            stats[brand] = {"total": 0, "negative": 0, "positive": 0, "neutral": 0}
        stats[brand]["total"] += 1
        sentiment = r.get("sentiment", "neutral")
        stats[brand][sentiment] = stats[brand].get(sentiment, 0) + 1
    for brand, s in stats.items():
        total = max(s["total"], 1)
        s["negative_rate"] = round(s["negative"] / total, 4)
        s["positive_rate"] = round(s["positive"] / total, 4)
    return stats

# ─── Insights history ─────────────────────────────────────────────────────────
def update_insights_history(current_stats, week):
    history = load_json(INSIGHTS_HISTORY_PATH, {"snapshots": []})
    prev_brands = {}
    if history["snapshots"]:
        prev_brands = history["snapshots"][-1].get("brands", {})
    brands_snapshot = {}
    for brand, s in current_stats.items():
        prev_neg = prev_brands.get(brand, {}).get("negative_rate", s["negative_rate"])
        delta    = round(s["negative_rate"] - prev_neg, 4)
        brands_snapshot[brand] = {
            "total":         s["total"],
            "negative_rate": s["negative_rate"],
            "positive_rate": s["positive_rate"],
            "neg_delta":     delta,
        }
    history["snapshots"].append({"week": week, "brands": brands_snapshot})
    if len(history["snapshots"]) > 52:
        history["snapshots"] = history["snapshots"][-52:]
    save_json(INSIGHTS_HISTORY_PATH, history)
    print(f"  Insights history: {len(history['snapshots'])} weeks logged")
    return history

# ─── Static-section refresh check ────────────────────────────────────────────
def check_static_refresh(current_stats):
    """Triggers regen if any competitor neg rate swings +-10% vs last week."""
    config     = load_json(INSIGHTS_CONFIG_PATH, {"last_negative_rates": {}})
    last_rates = config.get("last_negative_rates", {})
    reasons    = []
    for brand in ["courtyard", "rbt", "icybox"]:
        current  = current_stats.get(brand, {}).get("negative_rate", 0)
        previous = last_rates.get(brand, current)
        delta    = current - previous
        if abs(delta) >= SWING_THRESHOLD:
            direction = "up" if delta > 0 else "down"
            reasons.append(
                f"{BRAND_NAMES.get(brand, brand)} neg rate {direction} "
                f"{abs(delta)*100:.1f}% ({previous*100:.1f}% to {current*100:.1f}%)"
            )
    return len(reasons) > 0, reasons

def update_insights_config(current_stats):
    config = load_json(INSIGHTS_CONFIG_PATH, {})
    config["last_negative_rates"] = {
        brand: current_stats.get(brand, {}).get("negative_rate", 0)
        for brand in ["courtyard", "rbt", "icybox"]
    }
    config["last_updated"] = today_str()
    save_json(INSIGHTS_CONFIG_PATH, config)

# ─── Ratings snapshot ─────────────────────────────────────────────────────────
def save_ratings_snapshot(appstore_data, gplay_data):
    try:
        import archive as arc
        rating_snapshot = {}
        for brand_id, bdata in appstore_data.get("brands", {}).items():
            r = bdata.get("rating", {})
            rating_snapshot.setdefault(brand_id, {})["appstore"] = r.get("rating")
        for brand_id, bdata in gplay_data.get("brands", {}).items():
            r = bdata.get("info", {})
            rating_snapshot.setdefault(brand_id, {})["google"] = r.get("rating")
        arc.snapshot_ratings(rating_snapshot)
        print("  Ratings snapshot saved")
    except Exception as e:
        print(f"  Ratings snapshot skipped: {e}")

# ─── Git auto-push ────────────────────────────────────────────────────────────
def git_push(week):
    """Commit the updated dashboard.html and push to GitHub for Vercel deploy."""
    if not GIT_AUTO_PUSH:
        print("  Git push skipped (GIT_AUTO_PUSH=False)")
        return
    try:
        # Stage only dashboard.html (not data/ — that stays local)
        subprocess.run(["git", "add", "dashboard.html"], cwd=BASE_DIR, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Weekly update {week}"], cwd=BASE_DIR,
                       check=True, capture_output=True)
        result = subprocess.run(["git", "push"], cwd=BASE_DIR, check=True,
                                capture_output=True, text=True)
        print("  Pushed to GitHub — Vercel will deploy automatically")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, "stderr") and e.stderr else ""
        if "nothing to commit" in str(stderr) or "nothing added" in str(stderr):
            print("  No changes to push (dashboard unchanged)")
        else:
            print(f"  Git push failed: {stderr[:200]}")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    initial  = "--initial" in sys.argv
    lookback = 90 if initial else 7
    week     = today_str()

    print("=" * 62)
    print(f"Review Intelligence  --  {datetime.now().strftime('%A %b %d, %Y %H:%M')}")
    print("Initial (90-day seed)" if initial else "Weekly update (7 days)")
    print("=" * 62)

    archive    = load_archive(ARCHIVE_PATH)
    known_ids  = {r.get("id") for r in archive}
    print(f"\nArchive: {len(known_ids)} reviews on file\n")

    # ── App Store ─────────────────────────────────────────────────────────────
    print("APP STORE")
    appstore_data, all_new = {"brands": {}}, []
    try:
        from app_store_scraper import run as run_appstore
        appstore_data = run_appstore(known_ids=known_ids)
        for b in appstore_data["brands"].values():
            all_new.extend([r for r in b.get("reviews", []) if r.get("is_new")])
    except Exception as e:
        print(f"  App Store failed: {e}")

    # ── Google Play ───────────────────────────────────────────────────────────
    print("\nGOOGLE PLAY")
    gplay_data = {"brands": {}}
    try:
        from google_play_scraper_script import run as run_gplay
        gplay_data = run_gplay(known_ids=known_ids)
        for b in gplay_data["brands"].values():
            all_new.extend([r for r in b.get("reviews", []) if r.get("is_new")])
    except Exception as e:
        print(f"  Google Play failed: {e}")

    # ── Persist ───────────────────────────────────────────────────────────────
    archive, added = merge_new(archive, all_new)
    save_json(ARCHIVE_PATH, archive)
    save_ratings_snapshot(appstore_data, gplay_data)

    weekly_path = WEEKLY_DIR / f"{week}.json"
    save_json(weekly_path, {"week": week, "count": len(all_new), "reviews": all_new})

    print(f"\nArchive: +{added} new  ->  {len(archive)} total")

    # ── Copy bank ─────────────────────────────────────────────────────────────
    print("\nCOPY BANK")
    copy_bank = update_copy_bank(all_new)

    # ── Insights history + static refresh check ───────────────────────────────
    print("\nINSIGHTS")
    current_stats = compute_brand_stats(archive)
    history = update_insights_history(current_stats, week)
    should_refresh, reasons = check_static_refresh(current_stats)
    update_insights_config(current_stats)

    if should_refresh:
        print("  Static sections will REFRESH (competitor swing detected):")
        for r in reasons:
            print(f"    * {r}")
        static_flag = "--refresh-static"
    else:
        print("  Static sections stable -- no regen needed")
        static_flag = "--keep-static"

    # ── Regenerate dashboard ──────────────────────────────────────────────────
    print("\nREGENERATING DASHBOARD")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "update_dashboard.py"), static_flag],
            capture_output=True, text=True, cwd=str(BASE_DIR)
        )
        if result.returncode == 0:
            print("  Dashboard updated successfully")
        else:
            print(f"  Dashboard error:\n{result.stderr[:400]}")
    except Exception as e:
        print(f"  Could not run update_dashboard.py: {e}")

    # ── Push to GitHub → Vercel auto-deploys ──────────────────────────────────
    print("\nGIT PUSH")
    git_push(week)

    # ── Digest ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("WEEKLY DIGEST")
    print("=" * 62)
    snapshot_brands = (history["snapshots"][-1]["brands"]
                       if history["snapshots"] else {})
    for brand_id, brand_name in BRAND_NAMES.items():
        s    = current_stats.get(brand_id, {})
        snap = snapshot_brands.get(brand_id, {})
        delta = snap.get("neg_delta", 0)
        print(f"\n  {brand_name}")
        print(f"    Neg rate : {s.get('negative_rate', 0)*100:.1f}%  ({delta*100:+.1f}% vs last week)")
        print(f"    Total    : {s.get('total', 0)} reviews")

    print(f"\n  Week       : {week}")
    print(f"  New reviews: {added}")
    print(f"  Copy bank  : {len(copy_bank['quotes'])} total, "
          f"{len(copy_bank.get('in_rotation', []))} in rotation")
    print(f"  Refresh    : {'YES' if should_refresh else 'No'}")
    print(f"  Vercel     : auto-deploying from GitHub push")
    print("=" * 62)


if __name__ == "__main__":
    main()
