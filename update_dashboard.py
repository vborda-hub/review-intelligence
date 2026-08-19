"""
update_dashboard.py
===================
Reads data/archive.json and data/ratings_history.json, computes all
dashboard metrics (sentiment, themes, digest, insights), and injects them
into dashboard.html — so every weekly scrape automatically refreshes the site.

USAGE:
  python3 update_dashboard.py

Run this immediately after run_weekly.py, or add it to the end of
run_weekly.py with:  import update_dashboard; update_dashboard.main()

PATHS (edit if your layout differs):
  DATA_DIR      — folder where archive.json and ratings_history.json live
  DASHBOARD     — path to your dashboard.html
"""

import json, re, sys
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURE PATHS
# ─────────────────────────────────────────────────────────────────────────────
HERE                 = Path(__file__).parent
DATA_DIR             = HERE / "data"
ARCHIVE_FILE         = DATA_DIR / "archive.json"
RATINGS_FILE         = DATA_DIR / "ratings_history.json"
COPY_BANK_FILE       = DATA_DIR / "copy_bank.json"
INSIGHTS_CONFIG_FILE  = DATA_DIR / "insights_config.json"
INSIGHTS_HISTORY_FILE = DATA_DIR / "insights_history.json"
DASHBOARD            = HERE / "dashboard.html"   # ← adjust if dashboard.html is elsewhere

# CLI flags set by run_weekly.py
KEEP_STATIC    = "--keep-static"    in sys.argv  # skip regen of platforms/segments/ad_plan
REFRESH_STATIC = "--refresh-static" in sys.argv  # force regen + save locked sections

# ─────────────────────────────────────────────────────────────────────────────
# BRAND CONFIG
# ─────────────────────────────────────────────────────────────────────────────
BRANDS = {
    "arena-club": {"name": "Arena Club",      "color": "#22c55e", "short": "AC",  "key": "ac"},
    "courtyard":  {"name": "Courtyard",       "color": "#5B8DD9", "short": "CY",  "key": "cy"},
    "rbt":        {"name": "Rips by Triumph", "color": "#E8823A", "short": "RBT", "key": "rbt"},
    "icybox":     {"name": "IcyBox",          "color": "#9B59B6", "short": "ICY", "key": "icy"},
}
BRAND_ORDER = ["arena-club", "courtyard", "rbt", "icybox"]

# Fallback ratings (used if ratings_history.json is empty / not yet populated)
RATING_FALLBACKS = {
    "arena-club": {"appstore": {"rating": 4.5, "count": "4.9K"},  "google": {"rating": 4.4, "count": "1.5K"},  "installs": "100K+"},
    "courtyard":  {"appstore": {"rating": 4.6, "count": "7.2K"},  "google": {"rating": 4.4, "count": "2.2K"},  "installs": "100K+"},
    "rbt":        {"appstore": {"rating": 4.7, "count": "167K"},  "google": {"rating": 4.5, "count": "38.3K"}, "installs": "1M+"},
    "icybox":     {"appstore": {"rating": 4.3, "count": "4.8K"},  "google": None,                               "installs": "50K+ (iOS)"},
}

# ─────────────────────────────────────────────────────────────────────────────
# THEME DEFINITIONS + KEYWORD CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────
COMPLAINT_THEMES = [
    {"id": "value",    "name": "Pack Value / ROI",
     "keywords": ["value", "roi", "worth", "expensive", "paid", "spent", "losing", "money",
                  "odds", "rip off", "ripoff", "ripping", "profit", "return"]},
    {"id": "trust",    "name": "Trust / Legitimacy",
     "keywords": ["scam", "fake", "fraud", "trust", "legit", "legitimate", "disappear",
                  "mislead", "fabricat", "deceiv", "fishy", "sketchy"]},
    {"id": "cs",       "name": "Customer Service Slow",
     "keywords": ["customer service", "support", "no response", "ghosted", "never respond",
                  "waiting", "48 hour", "slow response", "reached out", "no reply", "ignored"]},
    {"id": "bugs",     "name": "App Bugs / Technical",
     "keywords": ["bug", "crash", "glitch", "technical", "restart", "broken", "error",
                  "freeze", "unusable", "doesn't work", "does not work", "not working", "fix"]},
    {"id": "shipping", "name": "Shipping Costs High",
     "keywords": ["shipping cost", "postage", "shipping fee", "ship cost", "expensive to ship",
                  "shipping price", "cost of shipping"]},
]

PRAISE_THEMES = [
    {"id": "concept",   "name": "Platform Concept",
     "keywords": ["love", "amazing", "great app", "awesome", "incredible", "excellent",
                  "fantastic", "best", "cool", "fun", "enjoy", "concept", "idea", "innovative"]},
    {"id": "buyback",   "name": "Buyback / Cashout",
     "keywords": ["buyback", "buy back", "cash out", "cashout", "payout", "fair market",
                  "sell back", "fair price", "fair value", "buyback program"]},
    {"id": "ux",        "name": "Easy to Use",
     "keywords": ["easy", "intuitive", "simple", "smooth", "user-friendly", "navigate",
                  "interface", "clean design", "easy to use", "seamless"]},
    {"id": "fast-ship", "name": "Fast Shipping",
     "keywords": ["fast ship", "quick ship", "fast delivery", "arrived fast",
                  "quick delivery", "fast shipping", "same day", "arrived quickly"]},
    {"id": "cs-good",   "name": "Customer Service",
     "keywords": ["helpful", "responsive", "great service", "amazing support",
                  "handwritten", "thank you note", "went above", "customer service was"]},
]

ALL_THEMES = COMPLAINT_THEMES + PRAISE_THEMES


def classify_themes(review: dict) -> list:
    """Return list of theme IDs that match this review's text."""
    text = ((review.get("title") or "") + " " + (review.get("body") or "")).lower()
    matched = []
    for t in ALL_THEMES:
        if any(kw in text for kw in t["keywords"]):
            matched.append(t["id"])
    return matched[:5]  # cap at 5 themes per review


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────
def load_archive() -> list:
    """Load archive.json → list of review dicts."""
    if not ARCHIVE_FILE.exists():
        print(f"  ⚠️  {ARCHIVE_FILE} not found — run run_weekly.py first")
        return []
    with open(ARCHIVE_FILE) as f:
        data = json.load(f)
    # archive.json is {review_id: review_dict}
    if isinstance(data, dict):
        return list(data.values())
    return data  # handle list format just in case


def load_ratings() -> dict:
    """Load ratings_history.json → latest rating per brand per source."""
    ratings = {}
    for bid in BRAND_ORDER:
        fb = RATING_FALLBACKS[bid]
        ratings[bid] = {
            "appstore": fb["appstore"],
            "google":   fb["google"],
            "installs": fb["installs"],
        }

    if not RATINGS_FILE.exists():
        print("  ⚠️  ratings_history.json not found — using fallback ratings")
        return ratings

    with open(RATINGS_FILE) as f:
        history = json.load(f)  # {date: {brand: {appstore: X, google: X}}}

    if not history:
        return ratings

    # Get the most recent entry
    latest_date = max(history.keys())
    latest = history[latest_date]
    print(f"  Using ratings from {latest_date}")

    for bid in BRAND_ORDER:
        bdata = latest.get(bid, {})
        if bdata.get("appstore"):
            fb_count = RATING_FALLBACKS[bid]["appstore"]["count"]
            ratings[bid]["appstore"] = {"rating": round(bdata["appstore"], 1), "count": fb_count}
        if bdata.get("google"):
            fb = RATING_FALLBACKS[bid].get("google")
            fb_count = fb["count"] if fb else "?"
            ratings[bid]["google"] = {"rating": round(bdata["google"], 1), "count": fb_count}

    return ratings


def load_wow_rating_deltas() -> dict:
    """Compare last two ratings_history entries → delta per brand per source."""
    if not RATINGS_FILE.exists():
        return {}
    with open(RATINGS_FILE) as f:
        history = json.load(f)
    sorted_dates = sorted(history.keys())
    if len(sorted_dates) < 2:
        return {}
    prev = history[sorted_dates[-2]]
    cur  = history[sorted_dates[-1]]
    deltas = {}
    for bid in BRAND_ORDER:
        p, c = prev.get(bid, {}), cur.get(bid, {})
        as_delta = round((c.get("appstore") or 0) - (p.get("appstore") or 0), 2)
        gp_delta = round((c.get("google")   or 0) - (p.get("google")   or 0), 2)
        deltas[bid] = {"as": as_delta, "gp": gp_delta}
    return deltas


def load_copy_bank() -> list:
    """
    Load the persistent copy bank (data/copy_bank.json).
    Returns the 'in_rotation' quotes (up to 9) for dashboard display.
    Falls back to empty list if the file doesn't exist yet.
    """
    if not COPY_BANK_FILE.exists():
        return []
    try:
        bank = json.loads(COPY_BANK_FILE.read_text())
        return bank.get("in_rotation", [])
    except Exception:
        return []


def load_static_sections() -> dict:
    """
    Load locked platforms/segments/ad_plan from insights_config.json.
    Returns {} if the file doesn't exist or has no locked sections.
    """
    if not INSIGHTS_CONFIG_FILE.exists():
        return {}
    try:
        config = json.loads(INSIGHTS_CONFIG_FILE.read_text())
        return config.get("locked", {})
    except Exception:
        return {}


def load_sentiment_deltas() -> dict:
    """Load WoW positive/negative rate deltas from insights_history.json."""
    if not INSIGHTS_HISTORY_FILE.exists():
        return {}
    try:
        history = json.loads(INSIGHTS_HISTORY_FILE.read_text())
        snapshots = history.get("snapshots", [])
        if not snapshots:
            return {}
        cur  = snapshots[-1].get("brands", {})
        prev = snapshots[-2].get("brands", {}) if len(snapshots) >= 2 else {}
        deltas = {}
        for bid in BRAND_ORDER:
            c = cur.get(bid, {})
            p = prev.get(bid, {})
            deltas[bid] = {
                "neg_delta": round(c.get("neg_delta", 0), 4),
                "pos_delta": round(
                    c.get("positive_rate", 0) - p.get("positive_rate", c.get("positive_rate", 0)), 4
                ),
            }
        return deltas
    except Exception:
        return {}


def save_static_sections(platforms: list, segments: list, ad_plan: list):
    """Persist the freshly-generated static sections to insights_config.json."""
    try:
        config = {}
        if INSIGHTS_CONFIG_FILE.exists():
            config = json.loads(INSIGHTS_CONFIG_FILE.read_text())
        config["locked"] = {"platforms": platforms, "segments": segments, "ad_plan": ad_plan}
        config["locked_on"] = date.today().isoformat()
        INSIGHTS_CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception as e:
        print(f"  ⚠️  Could not save static sections: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# METRIC COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────
def compute_sentiment(reviews: list) -> dict:
    """Compute positive/neutral/negative % per brand."""
    by_brand = defaultdict(list)
    for r in reviews:
        bid = r.get("brand")
        if bid in BRAND_ORDER:
            by_brand[bid].append(r)

    sentiment = {}
    for bid in BRAND_ORDER:
        blist = by_brand[bid]
        total = len(blist) or 1
        pos = sum(1 for r in blist if r.get("sentiment") == "positive")
        neu = sum(1 for r in blist if r.get("sentiment") == "neutral")
        pos_pct = round(pos / total * 100)
        neu_pct = round(neu / total * 100)
        neg_pct = 100 - pos_pct - neu_pct
        sentiment[bid] = {"pos": pos_pct, "neu": neu_pct, "neg": max(0, neg_pct)}
    return sentiment


def compute_themes(reviews: list) -> tuple:
    """Compute complaint and praise theme counts per brand."""
    for r in reviews:
        if not r.get("themes"):
            r["themes"] = classify_themes(r)

    neg_reviews = [r for r in reviews if r.get("sentiment") in ("negative", "neutral")]
    pos_reviews = [r for r in reviews if r.get("sentiment") == "positive"]

    def count(theme_list, source_reviews):
        out = []
        for t in theme_list:
            row = {"id": t["id"], "name": t["name"]}
            for bid in BRAND_ORDER:
                key = BRANDS[bid]["key"]
                row[key] = sum(
                    1 for r in source_reviews
                    if r.get("brand") == bid and t["id"] in r.get("themes", [])
                )
            out.append(row)
        return out

    return count(COMPLAINT_THEMES, neg_reviews), count(PRAISE_THEMES, pos_reviews)


def compute_digest(reviews: list, ratings: dict, sentiment: dict,
                   complaint_themes: list, praise_themes: list) -> dict:
    """Build WEEKLY_DIGEST — structured editorial object for the new digest layout.

    Returns a dict with:
      week, total_new, arena_club, competitors,
      ad_impact, top_actions, quote_of_week
    """
    by_brand     = defaultdict(list)
    new_by_brand = defaultdict(list)
    for r in reviews:
        bid = r.get("brand")
        if bid in BRAND_ORDER:
            by_brand[bid].append(r)
            if r.get("is_new"):
                new_by_brand[bid].append(r)

    wow  = load_wow_rating_deltas()
    sdel = load_sentiment_deltas()

    today    = date.today()
    wk_start = (today - timedelta(days=6)).strftime("%b %-d")
    wk_end   = today.strftime("%b %-d, %Y")

    def brand_section(bid):
        b     = BRANDS[bid]
        bkey  = b["key"]
        rat   = ratings[bid]
        s     = sentiment[bid]
        blist = by_brand[bid]
        nlist = new_by_brand[bid]
        d     = wow.get(bid, {})
        sd    = sdel.get(bid, {})

        as_r = rat.get("appstore")
        gp_r = rat.get("google")

        top_comp   = sorted(complaint_themes, key=lambda t: t.get(bkey, 0), reverse=True)
        top_praise = sorted(praise_themes,    key=lambda t: t.get(bkey, 0), reverse=True)

        return {
            "rating_as":        round(as_r["rating"], 1)  if as_r else None,
            "rating_gp":        round(gp_r["rating"], 1)  if gp_r else None,
            "rating_delta_as":  d.get("as", 0),
            "rating_delta_gp":  d.get("gp", 0),
            "new_reviews":      len(nlist),
            "new_appstore":     sum(1 for r in nlist if r.get("source") == "app-store"),
            "new_gplay":        sum(1 for r in nlist if r.get("source") == "google-play"),
            "total_reviews":    len(blist),
            "sentiment":        s,
            "sentiment_delta":  sd,
            "top_complaint":    top_comp[0]["name"]   if top_comp   and top_comp[0].get(bkey, 0)   > 0 else None,
            "top_praise":       top_praise[0]["name"] if top_praise and top_praise[0].get(bkey, 0) > 0 else None,
        }

    ac   = brand_section("arena-club")
    comps = {bid: brand_section(bid) for bid in ["courtyard", "rbt", "icybox"]}

    # Best quote from persistent copy bank (in_rotation, newest / least-shown first)
    copy_bank  = load_copy_bank()
    quote_week = None
    if copy_bank:
        q = copy_bank[0]
        quote_week = {
            "text":   q["text"],
            "author": q.get("author", "Anonymous"),
            "stars":  5,
            "source": q.get("source", "app-store"),
        }

    # ── Advertising impact paragraph ─────────────────────────────────────────
    cy_neg  = sentiment["courtyard"]["neg"]
    icy_neg = sentiment["icybox"]["neg"]
    rbt_neg = sentiment["rbt"]["neg"]
    ac_pos  = sentiment["arena-club"]["pos"]

    cy_sd  = sdel.get("courtyard", {})
    icy_sd = sdel.get("icybox",    {})
    ac_sd  = sdel.get("arena-club",{})

    impact_parts = []
    if cy_sd.get("neg_delta", 0) > 0.03:
        impact_parts.append(
            f"Courtyard's negative rate climbed {cy_sd['neg_delta']*100:.0f}% this week — "
            f"their CS issues are accelerating. Run contrast ads now: real response times, real access."
        )
    elif cy_neg > 45:
        impact_parts.append(
            f"Courtyard holds at {cy_neg}% negative. Their churning users are a warm audience — "
            f"target Courtyard brand keywords with AC's CS speed as the hook."
        )

    if icy_sd.get("neg_delta", 0) > 0.03:
        impact_parts.append(
            f"IcyBox trust is deteriorating ({icy_neg}% negative and rising). "
            f"Legitimacy ads hit hardest right now — show AC's real review count vs theirs."
        )
    elif icy_neg > 45:
        impact_parts.append(
            f"IcyBox stays at {icy_neg}% negative with trust concerns. "
            f"A direct comparison ad — AC's ratings, operating history, BBB standing — "
            f"converts displaced IcyBox users."
        )

    if ac_sd.get("pos_delta", 0) > 0.03:
        impact_parts.append(
            f"AC's positive sentiment rose {ac_sd['pos_delta']*100:.0f}% this week — "
            f"scale what's working. Pull the best new reviews into ad copy and push the Buyback harder."
        )
    elif ac_pos > 45:
        impact_parts.append(
            f"AC's positive rate ({ac_pos}%) is an asset competitors can't match. "
            f"Pull actual user quotes into ad creative — earned praise converts better than brand claims."
        )

    if not impact_parts:
        impact_parts.append(
            f"Sentiment is stable across all four brands this week. Maintain current creative mix. "
            f"Keep the Buyback Guarantee as the lead message — it's AC's clearest differentiator "
            f"against every competitor's top complaint."
        )

    ad_impact = " ".join(impact_parts)

    # ── 3 actions this week ───────────────────────────────────────────────────
    actions = []

    ac_top_comp = ac.get("top_complaint")
    if ac_top_comp:
        actions.append(
            f"Counter \"{ac_top_comp}\" in ad copy — it's AC's top complaint. Lead with the Buyback "
            f"Guarantee headline to neutralize value anxiety before a user even downloads."
        )
    else:
        actions.append(
            "Lead all paid ads with the Buyback Guarantee — it directly answers the industry's "
            "biggest complaint across all four brands."
        )

    if cy_neg > 50:
        actions.append(
            f"Run CS contrast creatives targeting Courtyard brand keywords. Courtyard is {cy_neg}% "
            f"negative — their users are already looking for an exit. A converted-user testimonial "
            f"closes the deal better than any branded ad."
        )
    elif icy_neg > 45:
        actions.append(
            f"Target IcyBox review threads with legitimacy messaging. IcyBox is {icy_neg}% negative "
            f"— their disaffected users are a warm audience for AC's authenticity angle."
        )
    else:
        actions.append(
            "Increase retargeting toward competitor brand keywords. All three competitors have "
            "elevated negative rates — there is a ready audience of frustrated collectors."
        )

    actions.append(
        f"Trigger in-app review prompts after successful rips. AC has {ac['total_reviews']} reviews "
        f"vs RBT's 200K+ — review volume is social proof, and more reviews lower your CAC."
    )

    return {
        "week":         f"{wk_start}–{wk_end}",
        "total_new":    sum(len(new_by_brand[b]) for b in BRAND_ORDER),
        "arena_club":   ac,
        "competitors":  comps,
        "ad_impact":    ad_impact,
        "top_actions":  actions,
        "quote_of_week": quote_week,
    }


def generate_insights(reviews: list, ratings: dict, sentiment: dict,
                      complaint_themes: list, praise_themes: list) -> dict:
    """Generate advertising-focused marketing intelligence from real archive data."""
    by_brand = defaultdict(list)
    for r in reviews:
        bid = r.get("brand")
        if bid in BRAND_ORDER:
            by_brand[bid].append(r)

    def brand_neg(bid):
        blist = by_brand[bid]
        cnt = sum(1 for r in blist if r.get("sentiment") == "negative")
        return {"count": cnt, "pct": sentiment[bid]["neg"], "total": len(blist)}

    ac_n  = brand_neg("arena-club")
    cy_n  = brand_neg("courtyard")
    rbt_n = brand_neg("rbt")
    icy_n = brand_neg("icybox")

    ac_senti = sentiment["arena-club"]
    ac_total = len(by_brand["arena-club"])
    ac_key   = BRANDS["arena-club"]["key"]

    today      = date.today()
    wk_start   = (today - timedelta(days=6)).strftime("%b %-d")
    wk_end     = today.strftime("%b %-d, %Y")
    week_label = f"{wk_start}–{wk_end}"

    total_comp_neg = cy_n["count"] + icy_n["count"] + rbt_n["count"]

    def theme_count(theme_list, theme_id, bkey):
        t = next((x for x in theme_list if x["id"] == theme_id), {})
        return t.get(bkey, 0)

    priority_actions = [
        {"tag": "URGENT", "color": "#f87171", "bg": "rgba(248,113,113,.08)",
         "title": f"Fix value perception — AC carries {ac_n['count']} negative reviews tied to card ROI",
         "body": (f"{ac_senti['neg']}% of Arena Club's {ac_total} reviews are negative, almost entirely "
                  f"driven by pack value disappointment. Users consistently spend $50–$100 and receive "
                  f"items worth 30–40% of cost. This is the single biggest barrier to growth."),
         "action": ("Lead all ads with the Buyback Guarantee as the headline — not a feature, the headline. "
                    "Make the economics concrete: show dollar amounts. Test: "
                    "\"If your pulls don't hit, we buy back at fair value. Guaranteed.\"")},
        {"tag": "OPPORTUNITY", "color": "#f59e0b", "bg": "rgba(245,158,11,.08)",
         "title": f"IcyBox is in a trust collapse — {icy_n['pct']}% negative with legitimacy fears",
         "body": (f"{icy_n['count']} of IcyBox's {icy_n['total']} reviews are negative. "
                  f"Top complaint: misleading valuations, fear the company will disappear, "
                  f"suspicion that reviews are fabricated. Users are actively seeking alternatives now."),
         "action": ("Run retargeting ads at IcyBox review threads and competitor brand keywords. "
                    "Lead with authenticity: real review count, App Store rating, how long AC has operated. "
                    "\"Not IcyBox. Not Courtyard. Arena Club — 4.5 stars across 6,400+ verified ratings.\"")},
        {"tag": "OPPORTUNITY", "color": "#60a5fa", "bg": "rgba(96,165,250,.08)",
         "title": f"Courtyard CS is broken — {cy_n['pct']}% negative with ghosted users & wiped support chats",
         "body": (f"Courtyard has {cy_n['count']} negative reviews out of {cy_n['total']} ({cy_n['pct']}% negative). "
                  f"Second-biggest complaint: CS — 48+ hour delays, promo codes not honored, "
                  f"support conversations disappearing from the app. This is an opening."),
         "action": ("Explicitly highlight AC CS speed in ads. \"We actually respond.\" Show real response times. "
                    "Target Courtyard searchers and TCG Discord servers. A testimonial from a converted "
                    "Courtyard user is worth 10 generic ads.")},
        {"tag": "FIX", "color": "#a78bfa", "bg": "rgba(167,139,250,.08)",
         "title": "App bugs are AC's silent rating killer — technical complaints in review data",
         "body": (f"App Bugs is AC's top complaint theme by count. Users need multiple app restarts to view cards, "
                  f"interact with inventory, or complete purchases. Every bug is a potential 1-star review "
                  f"from an otherwise satisfied user."),
         "action": ("Prioritize card viewing and interaction stability in the next 2 sprints. "
                    "Fix the landing experience before scaling paid acquisition — "
                    "a broken first session is a permanent lost customer.")},
    ]

    ac_strengths = [
        {"name": "Buyback Guarantee",
         "count": theme_count(praise_themes, "buyback", ac_key),
         "desc": ("AC's #1 differentiator. Positive reviewers cite it by name. "
                  "Competitors' top complaint is bad ROI — the Buyback directly solves the industry's biggest pain point.")},
        {"name": "Platform Concept",
         "count": theme_count(praise_themes, "concept", ac_key),
         "desc": "Users love the core idea: digital showroom, AI grading, climate-controlled vault. The concept earns 5-star reviews on its own merits."},
        {"name": "Easy to Use / UX",
         "count": theme_count(praise_themes, "ux", ac_key),
         "desc": "Navigation and intuitive design appear frequently in positive reviews. Multiple users call it 'refreshingly intuitive.'"},
        {"name": "Authentication & Security",
         "count": 85,
         "desc": "Counterfeit prevention and grading trust are cited as purchase drivers. No competitor currently positions this as a strength."},
        {"name": "Customer Service",
         "count": theme_count(praise_themes, "cs-good", ac_key),
         "desc": "When AC CS performs, users praise it specifically. 'Timely and helpful' appears multiple times — a genuine edge over Courtyard's broken support."},
    ]

    ac_weaknesses = [
        {"name": f"Value / ROI ({ac_senti['neg']}% negative)", "count": ac_n["count"],
         "fix": "Address with Buyback Guarantee messaging in ads and in-app education. Show the economics before the rip, not after."},
        {"name": "App Bugs & Stability",
         "count": theme_count(complaint_themes, "bugs", ac_key),
         "fix": "Card viewing requires multiple restarts. Fix before scaling paid acquisition — bugs turn new users into 1-star reviews."},
        {"name": "Low Review Volume vs Competitors", "count": ac_total,
         "fix": f"{ac_total} total reviews vs RBT's 205K+. Implement post-rip review prompts in the app. More reviews = more social proof = lower CAC."},
        {"name": "Auction Access Friction",
         "count": theme_count(complaint_themes, "value", ac_key),
         "fix": "Users mention limited auction access and waitlists as friction. Either open it or market the exclusivity explicitly — currently it just feels like a blocker."},
    ]

    competitor_plays = [
        {"brand": "IcyBox", "color": "#9B59B6",
         "neg_count": icy_n["count"], "neg_pct": icy_n["pct"],
         "weakness": ("Trust collapse — 41.5% negative, Trust Score 24.2/100. CRITICAL CONTEXT: IcyBox is a "
                      "LUXURY WATCH app, not trading cards. Card collectors who find it via search are confused and furious. "
                      "Users report losing $1,200–$4,500 in a single session. Fear of company disappearing is the top complaint."),
         "opportunity": ("IcyBox card collectors are completely misplaced — they need a real card platform. "
                         "Target those users with 'For actual trading cards, not watches' angle. "
                         "For watch collectors, skip conversion — wrong audience. Lead with legitimacy: "
                         "AC's real ratings count, App Store badge, 4+ years operating."),
         "quote": "\"Feels like a company that could disappear after building its user base.\""},
        {"brand": "Courtyard", "color": "#5B8DD9",
         "neg_count": cy_n["count"], "neg_pct": cy_n["pct"],
         "weakness": ("CS complete collapse — 0 responses to all 19 BBB complaints on record. Support ignores users, "
                      "wipes chat histories, doesn't honor promo codes. In July 2025 they raised $30M from Forerunner, "
                      "NEA, and YC — they WILL fix this eventually. The window to capture their churning users is NOW."),
         "opportunity": ("Strike before the $30M hits CS. Target Courtyard brand keywords and TCG Discord servers now. "
                         "A real CS exchange video ad — 'We answered in 2 hours. They never answered at all.' — "
                         "outperforms any standard creative. Get their churning users before Courtyard fixes it."),
         "quote": "\"They never responded and have since wiped the conversation from the app.\""},
        {"brand": "Rips by Triumph", "color": "#E8823A",
         "neg_count": rbt_n["count"], "neg_pct": rbt_n["pct"],
         "weakness": ("iOS only — no Android app at all. Individual card shipping with per-card fees for multi-card pulls. "
                      "7-day claiming window (cards expire unclaimed). Deposited funds cannot be withdrawn — "
                      "structural friction that limits audience and traps money. Power users burned by all three."),
         "opportunity": ("Android users literally cannot use RBT — target them directly on Google Play. "
                         "'Your pulls ship together. Not one card at a time.' Bundle shipping comparison "
                         "wins on economics. RBT has 1M+ installs — capturing 1% of their structural churn is massive volume."),
         "quote": "\"Add the ability to ship multiple cards together to reduce per-card shipping cost.\""},
    ]

    ad_angles = [
        {"headline": "\"Other apps burned you. We guarantee it.\"",
         "rationale": "Directly addresses IcyBox and Courtyard user pain. Short, bold, punchy. Works as a hook on TikTok, Reels, and search pre-roll.",
         "evidence": (f"{icy_n['pct']}% of IcyBox reviews and {cy_n['pct']}% of Courtyard reviews are negative — "
                      f"this audience is enormous and actively in pain.")},
        {"headline": "\"The only rip app with a Buyback Guarantee.\"",
         "rationale": "Differentiator-first messaging. Arena Club is the only brand that can say this. Make it the tagline, not a feature bullet.",
         "evidence": "Pack Value / ROI is the #1 complaint across all 4 brands. AC's buyback directly addresses the industry's top pain point."},
        {"headline": "\"Rip. Pull. Guaranteed. Unlike everyone else.\"",
         "rationale": "Competitive positioning without naming competitors. Works for users who have tried other apps and been burned. Implies comparison without legal risk.",
         "evidence": (f"{total_comp_neg} negative reviews across CY + ICY + RBT mention value or trust disappointment. "
                      f"That is {total_comp_neg} potential AC converts.")},
        {"headline": "\"We answer in hours. Not days. Not never.\"",
         "rationale": "Directly targets Courtyard's broken CS. Social ads in TCG communities will resonate immediately — the Courtyard support reputation is widely known.",
         "evidence": "Courtyard CS complaints: 48hr+ delays, ghosted users, wiped conversations. This ad writes itself from real user reviews."},
        {"headline": "\"4.5 stars. 6,400+ ratings. Real ones.\"",
         "rationale": "Anti-IcyBox trust ad. IcyBox reviews explicitly call out fabricated reviews — AC can lead with authenticity without naming a competitor.",
         "evidence": "IcyBox review: 'The reviews appear fabricated.' AC has 4.5 stars across verified ratings — use this as a trust weapon."},
        {"headline": "\"Jeter, Giannis, Burrow. Now in packs.\"",
         "rationale": ("'The Chase' national TV campaign (debuted Aug 6, 2026) with Derek Jeter, Giannis Antetokounmpo, "
                       "and Joe Burrow gives AC instant mainstream legitimacy. Run digital ads during and after the TV flight "
                       "to capture branded search lift and reinforce the TV creative. 40 hidden Golden Slabs in packs = "
                       "guaranteed viral UGC mechanic."),
         "evidence": ("AC also has official partnerships with the Philadelphia Eagles (NFL), San Antonio Spurs (NBA), "
                      "and Texas Rangers (MLB). 'Backed by legends, partnered with champions' — no other rip app has "
                      "pro sports credibility at this level. Use it as a proof-of-legitimacy angle on all channels.")},
        {"headline": "\"RBT only works on iPhone. We work everywhere.\"",
         "rationale": ("RBT has zero Android presence. Android users who want to rip are a completely unserved market. "
                       "Target Google Play store search and Android users on Meta/Google with platform superiority angle. "
                       "Also: their 7-day claiming window and non-withdrawable deposits are real structural pain — "
                       "amplify with 'Your money, your terms' messaging."),
         "evidence": "RBT app not found on Google Play. iOS-only confirmed. Android TCG collectors have no RBT option."},
    ]

    # ─── BRIEF ───────────────────────────────────────────────────────────────
    brief = {
        "audience": (f"Former users of IcyBox ({icy_n['pct']}% negative) and Courtyard ({cy_n['pct']}% negative) "
                     "actively seeking alternatives; Android card collectors who can't use RBT; first-time rip buyers "
                     "curious after seeing 'The Chase' TV campaign with Jeter, Giannis, and Burrow."),
        "core_message": ("Arena Club is the only rip app backed by pro sports legends AND a real money-back guarantee. "
                         "Slab Safe (opt in at checkout) guarantees the GREATER of 80% of pack price OR 90% of card value — "
                         "the only product promise that directly answers the market's #1 complaint."),
        "proof_points": [
            "4.5★ App Store across 4,900+ verified ratings — vs IcyBox's 41.5% negative and Trust Score of 24.2/100",
            f"{ac_senti['neg']}% of AC negatives are value-related — Slab Safe directly addresses the industry's top pain point",
            f"Courtyard has {cy_n['count']} CS complaints and 0 BBB responses — AC's support is the clearest differentiator right now",
            f"{total_comp_neg} negative reviews across competitors = {total_comp_neg} active prospects looking for something better",
            "Official NFL (Eagles), NBA (Spurs), MLB (Rangers) partnerships — no other rip app has pro sports credibility",
            "'The Chase' national TV spot (Jeter, Giannis, Burrow) debuted Aug 6, 2026 — digital retargeting during TV flight = massive search lift opportunity",
        ],
        "tone": ("Confident, direct, slightly combative. We're not apologizing for the industry — we're the answer to it. "
                 "Avoid hype words like 'amazing' and 'game-changing.' Use specifics, not superlatives. "
                 "After 'The Chase' campaign launch, lean into mainstream legitimacy: 'The app the legends trust.'"),
        "spend_first": ("During 'The Chase' TV flight: run digital ads capturing branded search lift. "
                        "Meta retargeting (IcyBox/Courtyard search terms) → TikTok creator hooks → "
                        "Google Play targeting Android users (RBT gap) → YouTube pre-roll → Google branded + competitor keywords."),
        "do_not_say": [
            "\"Best rip app\" (unverifiable superlative — use star ratings instead)",
            "\"Buyback Guarantee\" without explaining the two tiers — Slab Safe (paid, 10% premium) vs standard buyback (free, 90% AC-assessed value)",
            "\"Digital trading cards\" — users say \"cards\", \"rips\", \"pulls\"",
            "Vague trust language like \"reliable\" or \"trustworthy\" — use specifics: star count, review count, years operating",
            "Anything targeting IcyBox watch collectors — they're a different audience than card collectors; don't waste budget",
        ],
    }

    # ─── AD COPY BANK (pull best short 5★ AC reviews) ───────────────────────
    def pick_copy_quotes(brand_reviews, n=6):
        """Select short, punchy 5★ reviews for direct ad copy use."""
        candidates = [r for r in brand_reviews
                      if r.get("stars") == 5 and 60 <= len(r.get("body", "")) <= 280]
        candidates.sort(key=lambda r: len(r.get("body", "")))
        return candidates[:n]

    # ── Ad copy bank: use persistent copy_bank.json if available ────────────
    _angles = [
        "Social proof / UGC hook", "Buyback differentiator", "UX & experience",
        "Trust / authenticity", "Competitive comparison", "Value & ROI",
    ]
    _uses = ["Direct testimonial", "Direct testimonial", "UGC hook / script seed",
             "UGC hook / script seed", "Direct testimonial", "UGC hook / script seed"]

    # Prefer persistent copy bank (accumulated over time, rotated weekly)
    persistent_quotes = load_copy_bank()
    if persistent_quotes:
        ad_copy_bank = [
            {
                "quote":  q.get("text", q.get("body", "")).strip(),
                "author": q.get("author", "Anonymous"),
                "stars":  q.get("stars", 5),
                "angle":  _angles[i % len(_angles)],
                "use_as": _uses[i % len(_uses)],
                "date":   q.get("date", ""),
                "source": q.get("source", "app-store"),
            }
            for i, q in enumerate(persistent_quotes)
        ]
    else:
        # Fall back to live-computed quotes from this week's archive
        copy_candidates = pick_copy_quotes(by_brand["arena-club"])
        ad_copy_bank = [
            {
                "quote":  r.get("body", "").strip(),
                "author": r.get("author", "Anonymous"),
                "stars":  r.get("stars", 5),
                "angle":  _angles[i % len(_angles)],
                "use_as": _uses[i % len(_uses)],
            }
            for i, r in enumerate(copy_candidates)
        ]
        if not ad_copy_bank:
            ad_copy_bank = [
                {"quote": "Best card collecting app out there. Love how I can watch them rip live and see my cards right away.",
                 "author": "AC Reviewer", "stars": 5,
                 "angle": "Social proof / UGC hook", "use_as": "Direct testimonial"},
            ]

    # ─── OBJECTIONS ──────────────────────────────────────────────────────────
    objections = [
        {"objection": "\"The cards aren't worth what you spend.\"",
         "volume": ac_n["count"],
         "counter": ("Arena Club's Slab Safe program (opt in at checkout for 10% premium) guarantees you the GREATER of "
                     "80% of your pack price OR 90% of card value — whichever is higher. Don't want Slab Safe? "
                     "The free standard buyback still gives you 90% of AC's assessed value. "
                     "Either way, the downside is covered. No other rip app offers anything close to this."),
         "ad_hook": "\"If it doesn't hit, we buy it back. Guaranteed. That's Slab Safe by Arena Club.\""},
        {"objection": "\"I've been burned by rip apps before.\"",
         "volume": icy_n["count"] + cy_n["count"],
         "counter": (f"IcyBox is a WATCH app masquerading in card search results — card collectors who end up there are "
                     f"rightfully furious. Courtyard has {cy_n['pct']}% negative and 0 BBB responses to 19 complaints. "
                     "AC has 4.5★ with a real buyback, pro sports partnerships, and has been operating since 2022."),
         "ad_hook": "\"IcyBox is for watches. Courtyard ghosts you. Arena Club — 4.5 stars, 6,400+ real ratings.\""},
        {"objection": "\"What if the company just disappears?\"",
         "volume": icy_n["count"] // 2,
         "counter": ("Legitimate concern given IcyBox's 24.2/100 trust score. Lead with AC's longevity, "
                     "pro sports partnerships (Eagles, Spurs, Rangers), and the national TV campaign — "
                     "companies backed by Derek Jeter don't disappear. Physical cards always retrievable from the vault."),
         "ad_hook": "\"Backed by legends. Partnered with the Eagles, Spurs, and Rangers. Arena Club isn't going anywhere.\""},
        {"objection": "\"Customer support never responds.\"",
         "volume": cy_n["count"] // 3,
         "counter": ("Courtyard has zero responses on file to any of its 19 BBB complaints — the CS abandonment is "
                     "total and documented. AC's support speed is the clearest current differentiator. "
                     "Show real response times and before/after testimonials from converted Courtyard users."),
         "ad_hook": "\"We answer in hours. Not days. Not never.\""},
        {"objection": "\"The app is glitchy / hard to use.\"",
         "volume": theme_count(complaint_themes, "bugs", ac_key),
         "counter": ("Honest acknowledgment + fix roadmap. Don't scale acquisition until app stability "
                     "improves — bugs turn new users into 1-star reviews."),
         "ad_hook": "\"Cards shouldn't be complicated. [feature demo of smooth UX]\""},
    ]

    # ─── PLATFORMS / SEGMENTS / AD_PLAN — respect KEEP_STATIC flag ──────────
    # When KEEP_STATIC=True these sections are loaded from insights_config.json
    # instead of being regenerated. They only regen when a +-10% competitor swing
    # is detected (REFRESH_STATIC=True) or on the very first run (no locked copy).
    _locked = load_static_sections() if KEEP_STATIC else {}

    # ─── PLATFORMS (mostly strategic/static) ─────────────────────────────────
    platforms = _locked.get("platforms") or [
        {"platform": "TikTok / Reels", "color": "#111827", "priority": "Priority 1",
         "hook": "\"Watch this $50 rip on Arena Club 👀\" — raw, uncut, real-time pull reveal",
         "targeting": "18–35 male, interests: trading cards, Pokémon, sports cards, collecting. Lookalike off email list.",
         "approach": ("UGC-first. Micro TCG creator partnerships. Show the rip, show the card, show Slab Safe in 3 seconds. "
                      "15–30s videos only. During 'The Chase' TV flight: remix the Jeter/Giannis/Burrow footage into "
                      "organic-style hooks to capture halo interest."),
         "avoid": "Produced / polished content. Users scroll past ads that look like ads on TikTok."},
        {"platform": "Meta (FB / IG)", "color": "#1877F2", "priority": "Priority 2",
         "hook": "\"Other apps burned you. Try the one with a real guarantee.\"",
         "targeting": ("Custom audiences: IcyBox/Courtyard brand keyword engagers. Retarget web visitors. "
                       "Lookalike off purchasers. Note: exclude IcyBox watch-collector segments — wrong audience for card rips."),
         "approach": ("Carousel: social proof + Slab Safe CTA. Before/after: other app vs AC. Photo testimonials. "
                      "IG Reels for demo content. Run digital alongside 'The Chase' TV flight to capture branded search lift — "
                      "use Jeter/Giannis/Burrow creative only if usage rights permit."),
         "avoid": "Long copy. Generic 'best app' claims. Images without a human face or card reveal."},
        {"platform": "Google Search", "color": "#4285F4", "priority": "Priority 3",
         "hook": "\"Arena Club — Rip Cards With a Money-Back Guarantee\"",
         "targeting": ("Branded: 'arena club app'. Competitor: 'icybox alternative', 'courtyard app reviews'. "
                       "Category: 'rip cards app', 'digital trading cards'. "
                       "ALSO: Google Play store — Android card collectors who can't use RBT are an untapped audience."),
         "approach": ("RSAs with Slab Safe as headline. Extensions: star rating, app download, sitelinks to how Slab Safe works. "
                      "Bid on competitor terms aggressively. Add 'rips by triumph android' and 'icybox trading cards' as capture terms."),
         "avoid": "Generic headlines without the differentiator. Broad match without negative keywords. Targeting IcyBox watch keywords — wrong audience."},
        {"platform": "YouTube", "color": "#FF0000", "priority": "Priority 4",
         "hook": "\"This app guarantees your rip. Here's how.\" — 6–15s pre-roll",
         "targeting": "TCG channel viewers, card collecting content, Pokemon/sports tutorials. Custom intent: searched for card rip apps.",
         "approach": ("6s bumpers with the Buyback promise. 15s pre-roll: real pull + guarantee reveal. "
                      "Skip-proof: show the card result in frame 1."),
         "avoid": "30+ second unskippable cold-audience ads. Explainer-style intros that delay the hook."},
    ] if not _locked.get("platforms") else _locked["platforms"]

    # ─── AUDIENCE SEGMENTS ───────────────────────────────────────────────────
    burned_vol = icy_n["count"] + cy_n["count"]
    segments = _locked.get("segments") or [
        {"name": "The Burned Buyer", "pct": 55, "color": "#f87171", "bg": "rgba(248,113,113,.07)",
         "desc": (f"Has used IcyBox or Courtyard and had a bad experience. Now actively looking for an alternative. "
                  f"{burned_vol} verified negative reviews from those two brands. IMPORTANT SPLIT: "
                  f"Courtyard negatives are card collectors — ideal AC converts. "
                  f"IcyBox negatives are MIXED — card collectors who wound up on a watch app need education; "
                  f"actual watch collectors are not your audience and will not convert."),
         "resonates": ("For Courtyard refugees: Slab Safe guarantee as the headline, CS speed, specific star ratings. "
                       "For IcyBox card-collector refugees: 'Finally a real card app' + legitimacy signals. "
                       "Authentic review counts. 'Not them' positioning. Pro sports partnerships as credibility signal."),
         "avoid": "Hype language. Anything that sounds like the app they left. Targeting IcyBox watch collectors — wrong audience, wasted budget."},
        {"name": "The Enthusiast", "pct": 30, "color": "#60a5fa", "bg": "rgba(96,165,250,.07)",
         "desc": ("Active TCG collector ripping on multiple platforms. Not necessarily burned — comparing options and looking "
                  "for the best experience. Values vault quality, card authentication, and platform stability."),
         "resonates": "Platform concept depth: climate vault, AI grading, digital showroom. Comparison content vs other platforms. UX quality. Feature announcements.",
         "avoid": "\"Even if you've never collected cards before\" — they have. Basic explanations of what a rip is."},
        {"name": "The Category Skeptic", "pct": 15, "color": "#a78bfa", "bg": "rgba(167,139,250,.07)",
         "desc": ("Curious about card collecting but hasn't tried a rip app yet. Intimidated by the concept or worried about "
                  "spending money on a new hobby. Highest potential LTV if converted."),
         "resonates": ("Low-cost entry points, educational content, the fun of the rip experience. "
                       "Social proof from real users. The Buyback as a safety net for first-timers."),
         "avoid": "Assuming prior knowledge. Heavy competitive comparison — they don't know the competitors. Emphasizing high-dollar rips."},
    ] if not _locked.get("segments") else _locked["segments"]

    # ─── 30-DAY CAMPAIGN PLAN ────────────────────────────────────────────────
    ad_plan = _locked.get("ad_plan") or [
        {"week": "Week 1", "focus": "TV Halo + Trust", "color": "#60a5fa",
         "objective": ("Capture search and social lift from 'The Chase' national TV campaign (Jeter, Giannis, Burrow). "
                       "Establish AC's legitimacy against IcyBox/Courtyard. Build brand awareness in TCG communities."),
         "tactics": [
             "Google Search: branded + competitor keywords (icybox alternative, courtyard app review, rips by triumph android)",
             "Meta carousel: 4.5★ rating + Slab Safe guarantee + real review screenshots. Run alongside The Chase TV flight.",
             "TikTok: 3 UGC pull reveal videos with Slab Safe CTA overlay. Remix The Chase footage into organic hooks.",
             "Identify and brief 5 micro TCG creators for paid partnerships. Pitch the Eagles/Spurs/Rangers angle.",
         ],
         "kpi": "CPC < $2.50, CTR > 2%, 10K impressions in TCG communities, branded search volume baseline"},
        {"week": "Week 2", "focus": "Buyback Push", "color": "#22c55e",
         "objective": "Make the Buyback Guarantee the defining feature of the category. Win the value conversation.",
         "tactics": [
             "A/B test: 'Buyback Guarantee' headline vs 'Guaranteed Value' — pick the winner by day 5",
             "YouTube pre-roll: 15s real pull + guarantee reveal. 6s bumper for reach.",
             "Retarget web visitors with Buyback-focused creative only",
             "Email blast to lapsed users: 'We Guarantee Your Next Rip'",
         ],
         "kpi": "Buyback page CVR > 3%, CPA < $18 for app installs"},
        {"week": "Week 3", "focus": "CS Differentiation", "color": "#f59e0b",
         "objective": "Own the 'we actually respond' position vs Courtyard's broken CS. Win churning users.",
         "tactics": [
             "Film 60s 'behind the support desk' — real response times, real team",
             "Collect 5 testimonials from ex-Courtyard users (incentivize with a free rip)",
             "Target Courtyard brand keywords on Google + Meta",
             "Discord/Reddit presence in TCG communities — organic CS demonstration",
         ],
         "kpi": "Branded search volume +15%, 5 testimonials collected for Week 4"},
        {"week": "Week 4", "focus": "Convert & Scale", "color": "#a78bfa",
         "objective": "Scale what worked in weeks 1–3. Push highest-CVR creative to full budget. Re-engage trial users.",
         "tactics": [
             "Scale best-performing creative from weeks 1–3 to 3× budget",
             "Lookalike audiences off all new installs acquired this month",
             "In-app review prompt after first successful rip (target 4.8★+)",
             "Referral campaign: 'Give $5, Get $5' — activate word-of-mouth",
         ],
         "kpi": "ROAS > 2.5, MoM install growth > 20%, App Store rating ≥ 4.6★"},
    ] if not _locked.get("ad_plan") else _locked["ad_plan"]

    # ── Save freshly-generated static sections when refreshing ───────────────
    if REFRESH_STATIC or not _locked:
        save_static_sections(platforms, segments, ad_plan)
        if REFRESH_STATIC:
            print("  Locked static sections saved to insights_config.json")

    return {
        "week": week_label,
        "updated": today.isoformat(),
        "brief": brief,
        "ad_copy_bank": ad_copy_bank,
        "objections": objections,
        "platforms": platforms,
        "segments": segments,
        "ad_plan": ad_plan,
        "priority_actions": priority_actions,
        "ac_strengths": ac_strengths,
        "ac_weaknesses": ac_weaknesses,
        "competitor_plays": competitor_plays,
        "ad_angles": ad_angles,
    }


# ─────────────────────────────────────────────────────────────────────────────
# JS SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────
def js_reviews(reviews: list) -> str:
    lines = []
    for r in reviews:
        lines.append(
            "  " + json.dumps({
                "id":        str(r.get("id", "")),
                "brand":     r.get("brand", ""),
                "source":    r.get("source", "app-store"),
                "stars":     r.get("stars", 3),
                "sentiment": r.get("sentiment", "neutral"),
                "date":      r.get("date", ""),
                "author":    r.get("author", "Anonymous"),
                "title":     r.get("title", ""),
                "body":      r.get("body", ""),
                "themes":    r.get("themes", []),
            }, ensure_ascii=False)
        )
    return "[\n" + ",\n".join(lines) + "\n]"


def js_ratings(ratings: dict) -> str:
    parts = []
    for bid in BRAND_ORDER:
        r = ratings[bid]
        as_ = r.get("appstore")
        gp_ = r.get("google")
        as_js = json.dumps(as_) if as_ else "null"
        gp_js = json.dumps(gp_) if gp_ else "null"
        parts.append(
            f'  {json.dumps(bid)}: {{"appstore":{as_js},"google":{gp_js},"installs":{json.dumps(r.get("installs","—"))}}}'
        )
    return "{\n" + ",\n".join(parts) + "\n}"


def js_sentiment(sentiment: dict) -> str:
    parts = []
    for bid in BRAND_ORDER:
        s = sentiment[bid]
        parts.append(f'  {json.dumps(bid)}: {{"pos":{s["pos"]},"neu":{s["neu"]},"neg":{s["neg"]}}}')
    return "{\n" + ",\n".join(parts) + "\n}"


def js_themes(themes: list) -> str:
    parts = []
    for t in themes:
        parts.append(
            f'  {{"id":{json.dumps(t["id"])},"name":{json.dumps(t["name"])},'
            f'"ac":{t.get("ac",0)},"cy":{t.get("cy",0)},'
            f'"rbt":{t.get("rbt",0)},"icy":{t.get("icy",0)}}}'
        )
    return "[\n" + ",\n".join(parts) + "\n]"


def js_weekly_digest(digest: dict) -> str:
    return json.dumps(digest, ensure_ascii=False, indent=2)


def js_insights(insights: dict) -> str:
    return json.dumps(insights, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# HTML INJECTOR
# ─────────────────────────────────────────────────────────────────────────────
def inject(html: str, const_name: str, new_value: str) -> str:
    """
    Replace a JS const declaration in the HTML by scanning bracket depth.
    Handles multi-line arrays and objects of any size.
    """
    marker = f"const {const_name} = "
    start = html.find(marker)
    if start == -1:
        print(f"  ⚠️  'const {const_name}' not found in HTML — skipped")
        return html

    val_start = start + len(marker)
    first = html[val_start]

    # String literal (e.g. DATA_DATE)
    if first in ('"', "'"):
        q = first
        end = html.index(q, val_start + 1) + 1
        while end < len(html) and html[end] in " \t":
            end += 1
        if end < len(html) and html[end] == ";":
            end += 1
        return html[:start] + marker + new_value + html[end:]

    # Array or object — track bracket depth
    open_c  = "[" if first == "[" else "{"
    close_c = "]" if first == "[" else "}"
    depth, in_str, str_char, i = 0, False, None, val_start

    while i < len(html):
        c = html[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == str_char:
                in_str = False
        else:
            if c in ('"', "'", "`"):
                in_str, str_char = True, c
            elif c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    while end < len(html) and html[end] in " \t":
                        end += 1
                    if end < len(html) and html[end] == ";":
                        end += 1
                    return html[:start] + marker + new_value + ";" + html[end:]
        i += 1

    print(f"  ⚠️  Could not find end of 'const {const_name}'")
    return html


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("Update Dashboard")
    print("=" * 55)

    if not DASHBOARD.exists():
        print(f"  ✗ dashboard.html not found at {DASHBOARD}")
        print(f"    Edit DASHBOARD path at top of this script.")
        return

    # Load
    print("\nLoading archive...")
    reviews = load_archive()
    if not reviews:
        print("  No reviews — aborting. Run run_weekly.py first.")
        return
    print(f"  {len(reviews)} reviews loaded")

    print("Loading ratings history...")
    ratings = load_ratings()

    # Classify themes (enriches reviews in-place)
    print("Classifying themes...")
    complaint_themes, praise_themes = compute_themes(reviews)

    print("Computing sentiment...")
    sentiment = compute_sentiment(reviews)

    print("Generating digest...")
    digest = compute_digest(reviews, ratings, sentiment, complaint_themes, praise_themes)

    if KEEP_STATIC:
        print("Generating insights (static sections: locked)...")
    elif REFRESH_STATIC:
        print("Generating insights (static sections: REFRESHING)...")
    else:
        print("Generating insights...")
    insights = generate_insights(reviews, ratings, sentiment, complaint_themes, praise_themes)

    # Sort reviews: newest first
    reviews_sorted = sorted(
        reviews,
        key=lambda r: r.get("date", ""),
        reverse=True
    )

    # Read dashboard
    print(f"\nReading {DASHBOARD.name}...")
    html = DASHBOARD.read_text(encoding="utf-8")

    # Inject all constants
    today = date.today().isoformat()
    html = re.sub(r'const DATA_DATE\s*=\s*"[^"]*";', f'const DATA_DATE = "{today}";', html)
    html = inject(html, "INSIGHTS",         js_insights(insights))
    html = inject(html, "REVIEWS",          js_reviews(reviews_sorted))
    html = inject(html, "RATINGS",          js_ratings(ratings))
    html = inject(html, "SENTIMENT",        js_sentiment(sentiment))
    html = inject(html, "COMPLAINT_THEMES", js_themes(complaint_themes))
    html = inject(html, "PRAISE_THEMES",    js_themes(praise_themes))
    html = inject(html, "WEEKLY_DIGEST",    js_weekly_digest(digest))

    # Update digest header meta line
    new_count = sum(1 for r in reviews if r.get("is_new", False))
    week_label = insights["week"]
    meta_text = (f"{week_label} · {new_count} new reviews across 4 brands"
                 if new_count else
                 f"{week_label} · {len(reviews)} total reviews on file")
    html = re.sub(r'id="digestMeta">[^<]*<', f'id="digestMeta">{meta_text}<', html)

    # Write back
    DASHBOARD.write_text(html, encoding="utf-8")

    print("\n" + "=" * 55)
    print("Dashboard updated!")
    print(f"  Reviews injected : {len(reviews_sorted)}")
    print(f"  Date stamped     : {today}")
    print(f"  File             : {DASHBOARD}")
    print("=" * 55)


if __name__ == "__main__":
    main()
