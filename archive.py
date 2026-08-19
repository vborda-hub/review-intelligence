"""
Review Archive — Append-Only Historical Database
=================================================
Every review ever seen gets written to archive.json exactly once,
keyed by review ID. Weekly runs only ADD new reviews; nothing is
ever overwritten or deleted.

File structure in data/:
  archive.json          — every review ever seen (grows weekly)
  ratings_history.json  — weekly rating snapshot per brand (for trend charts)
  weekly/
    2026-08-19.json     — only the NEW reviews from each weekly run

This module is called by run_weekly.py — you don't run it directly.
"""

import json
from datetime import date
from pathlib import Path

DATA_DIR    = Path("data")
ARCHIVE     = DATA_DIR / "archive.json"
RATINGS_LOG = DATA_DIR / "ratings_history.json"
WEEKLY_DIR  = DATA_DIR / "weekly"


def _load_archive() -> dict:
    """Load the full archive. Returns {review_id: review_dict}."""
    if not ARCHIVE.exists():
        return {}
    with open(ARCHIVE) as f:
        return json.load(f)


def _save_archive(archive: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(ARCHIVE, "w") as f:
        json.dump(archive, f, indent=2)


def known_ids() -> set:
    """Return the set of all review IDs ever seen."""
    return set(_load_archive().keys())


def add_reviews(new_reviews: list[dict]) -> int:
    """
    Append genuinely new reviews to the archive.
    Returns the count actually added (skips any already present).
    """
    archive = _load_archive()
    added = 0
    for rv in new_reviews:
        rid = rv.get("id") or rv.get("reviewId")
        if not rid:
            continue
        if rid not in archive:
            archive[rid] = rv
            added += 1
    _save_archive(archive)
    return added


def snapshot_ratings(brand_ratings: dict):
    """
    Save a weekly rating snapshot for trend charting.
    brand_ratings: {"arena-club": {"appstore": 4.5, "google": 4.4}, ...}
    """
    DATA_DIR.mkdir(exist_ok=True)
    history = {}
    if RATINGS_LOG.exists():
        with open(RATINGS_LOG) as f:
            history = json.load(f)

    today = date.today().isoformat()
    history[today] = brand_ratings

    with open(RATINGS_LOG, "w") as f:
        json.dump(history, f, indent=2)


def save_weekly_new(new_reviews: list[dict]):
    """Save this week's new reviews to a dated file for reference."""
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = WEEKLY_DIR / f"{today}.json"
    with open(path, "w") as f:
        json.dump({"date": today, "count": len(new_reviews), "reviews": new_reviews}, f, indent=2)
    return path


def get_all_reviews(brand_id: str = None, source: str = None,
                    sentiment: str = None, since: str = None) -> list[dict]:
    """
    Query the archive with optional filters.
    - brand_id:  "arena-club" | "courtyard" | "rbt" | "icybox"
    - source:    "app-store" | "google-play"
    - sentiment: "positive" | "neutral" | "negative"
    - since:     ISO date string "YYYY-MM-DD" — only reviews on/after this date
    """
    archive = _load_archive()
    results = list(archive.values())

    if brand_id:
        results = [r for r in results if r.get("brand") == brand_id]
    if source:
        results = [r for r in results if r.get("source") == source]
    if sentiment:
        results = [r for r in results if r.get("sentiment") == sentiment]
    if since:
        results = [r for r in results if r.get("date", "") >= since]

    return sorted(results, key=lambda r: r.get("date", ""), reverse=True)


def archive_stats() -> dict:
    """Return a summary of what's in the archive."""
    archive = _load_archive()
    reviews = list(archive.values())
    stats = {"total": len(reviews), "by_brand": {}, "by_source": {}, "by_sentiment": {}}

    for rv in reviews:
        b = rv.get("brand", "unknown")
        s = rv.get("source", "unknown")
        senti = rv.get("sentiment", "unknown")
        stats["by_brand"][b]    = stats["by_brand"].get(b, 0) + 1
        stats["by_source"][s]   = stats["by_source"].get(s, 0) + 1
        stats["by_sentiment"][senti] = stats["by_sentiment"].get(senti, 0) + 1

    return stats
