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

import json, re, sys, urllib.request, time
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
COPY_BANK_FILE        = DATA_DIR / "copy_bank.json"
INSIGHTS_CONFIG_FILE  = DATA_DIR / "insights_config.json"
INSIGHTS_HISTORY_FILE = DATA_DIR / "insights_history.json"
DYNAMIC_THEMES_FILE   = DATA_DIR / "dynamic_themes.json"
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

APP_STORE_URLS = {
    "arena-club": "https://apps.apple.com/us/app/arena-club-sports-tcg-card/id6499444724",
    "courtyard":  "https://apps.apple.com/us/app/courtyard-tcg-watches-cards/id6748155184",
    "rbt":        "https://apps.apple.com/us/app/rips-by-triumph/id6751921248",
    "icybox":     "https://apps.apple.com/us/app/icybox/id6758816716",
}

# Google Play package IDs (IcyBox is iOS-only — excluded)
GOOGLE_PLAY_PACKAGES = {
    "arena-club": "com.arenaclub.mobile",
    "courtyard":  "io.courtyard.app",
    "rbt":        "com.triumpharcade.tcg",
}

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
    # ── Rigged / Fixed Algorithm ─────────────────────────────────────────────
    {"id": "rigged", "name": "Rigged / Fixed Algorithm",
     "keywords": [
         "feels rigged", "feel rigged", "it's rigged", "its rigged", "this is rigged",
         "pulls are rigged", "algorithm is rigged", "rigged algorithm", "rigged pulls",
         "clearly rigged", "obviously rigged", "they're rigged", "must be rigged",
         "predetermined", "pre-determined", "pre determined",
         "hand picked", "hand-picked", "cherry picked", "cherry-picked",
         "not random", "isn't random", "never random",
         "weighted toward", "always get the same", "always pull the same",
         "always get commons", "always get base", "never get anything good",
         "computer picks", "they decide what you get", "algorithm decides",
         "manipulated pull", "manipulated pulls", "they manipulate",
         # "Odds are rigged" — direct accusation phrasing
         "odds are rigged", "the odds are rigged", "pulls are pre-determined",
         "results are predetermined", "results are pre-determined",
         # "This game is rigged"
         "this game is rigged", "the game is rigged", "game is clearly rigged",
         # "Designed so that you lose money" — manipulative algorithm accusation
         "designed so that you lose", "designed to make you lose",
         "set up to make you lose", "set up so you lose",
         # "Odds plummet" — sharp drop in odds after initial wins
         "odds plummet", "odds drop", "odds tank", "odds crater",
         "good at first but odds", "win at first then odds",
         # "House odds win again" — gambling-frame language
         "house odds", "house always wins",
         # "You win then it makes you lose it all" — win-to-drain bait pattern
         "you win then it makes you lose", "win then it makes you lose",
         "win then you lose it all", "win a little then lose it all",
         "gives you good cards at first then", "good cards at first then",
         "good results at first then", "starts good then goes downhill",
         "goes absolutely downhill after", "goes downhill after",
         # "Rigged pack opening" — different word order
         "rigged pack opening", "pack openings are rigged",
         # "Percentages are predetermined" / fixed percentages
         "percentages are predetermined", "percentages are pre-determined",
         "percentages are fixed", "percentages are rigged",
         # "Rigged game" — standalone compound noun
         "rigged game", "this is a rigged game", "it's a rigged game",
         # "Pad the grail section" — fake item pool manipulation
         "pad the grail section", "padding the grail section",
         "padded the grail section", "pad the grail category",
         "pad the category with junk", "filled with junk watches",
         # "AI generated to make you lose" — algorithm accusation
         "ai generated to make you lose", "ai generated to make u lose",
         "designed by ai to make you lose",
         # "Usual winners every time" / same community members win
         "usual winners every time", "same people always win",
         "same members always win", "same winners every time",
         # "Haven't been able to win since the update"
         "haven't been able to win since", "havent been able to win since",
         "unable to win since the update", "can't win since the update",
         # "Manually changing my odds" — targeted manipulation accusation
         "manually changed my odds", "manually changing my odds",
         "manually started changing", "they manually changed",
         "changed the algorithm to be more greedy", "changed to be more greedy",
         "pull rates getting worse", "pull rates have gotten worse",
         "pull rates are getting worse",
         # "Set up to pay you bare minimum" — systematic underpayment
         "set up to pay you bare minimum", "pay you bare minimum",
         "pays you the bare minimum", "designed to pay bare minimum",
         # "Only get grey cards" / "only getting grey cards" — no value hits
         "only get grey cards", "only getting grey cards",
         "always get grey cards", "nothing but grey cards",
         "only gives grey cards", "only gives you grey cards",
         # "Pulling the same cards multiple times in a row" — no variety / fixed
         "pulling the same cards multiple times in a row",
         "pulling the same cards multiple times",
         "get the same cards multiple times", "same cards over and over",
         "same card multiple times in a row",
         # "Designed so you lose" — bare causal form (no "that")
         "designed so you lose", "built so you lose", "set up so you can't win",
         # "The more they take from you" — algorithmic value drain
         "the more they take from you", "more they take from you",
         "more you play the more they take", "the more you play the more they steal",
         # "Only influencers get the big hits" — rigged for sponsored players
         "only influencers get", "influencers always get the good pulls",
         "only influencers win", "influencers get the hits",
         "influencers get all the good cards",
         # "Lowest-priced cards over and over" — only hitting cheap cards
         "lowest-priced cards", "lowest priced cards over and over",
         "keep getting the lowest", "always the lowest value",
         # "Gradually lose it all" — slow algorithmic drain after initial wins
         "gradually lose it all", "gradually start losing",
         "gradually you lose", "slowly lose it all",
         # "Nothing but bottom level cards" — only hitting cheapest tier
         "nothing but bottom level cards", "nothing but bottom tier",
         "nothing but bottom-level", "only bottom level cards",
         "all bottom level cards",
         # "Cycle through the same few cheap commons" — repeat low-value results
         "cycle through the same few", "cycling through the same cards",
         "cycles through the same", "cycles through cheap commons",
         # "Favored to the house" — house-odds accusation
         "favored to the house", "game is favored to the house",
         "always favored to the house",
         # "Starts eating it" / "starts eating your money" — drain after bait
         "starts eating it", "starts eating your money", "starts eating my money",
         "starts eating into my",
         # "Reel you in" — bait-and-switch wins early
         "reel you in", "reel u in", "reeled me in",
         "they reel you in", "to reel you in",
         # "1000% rigged" — superlative rigged claim
         "1000% rigged", "1000 percent rigged",
         # "So rigged" — bare informal superlative form
         "so rigged", "it's so rigged", "this is so rigged",
         "its so rigged", "game is so rigged", "app is so rigged",
         # "Keep hitting the same cards" — informal progressive repeat-pull complaint
         "keep hitting the same cards", "literally keep hitting the same cards",
         "always hitting the same cards",
         # "Pretending a gray is a win" — low-value card counted as win to avoid gambling laws
         "pretending a gray is a win", "count a gray as a win",
         "grey counts as a win", "gray counts as a win",
         # "Turns back to a different card" — animation rollback (rigged perception)
         "turns back to a different card", "flips to a different card",
         "turns over to a different card",
         # "Gives common every time" — always landing on lowest value card
         "gives common every time", "gives you a common every time",
         "gives a common every time", "gives commons every time",
         # "Only losses since update" / "only loses since update" — post-update complaint
         "only loses since update", "only losses since update",
         "only losses since the update", "only loses since the update",
     ]},

    # ── Poor Odds / Never Hit ────────────────────────────────────────────────
    {"id": "odds", "name": "Poor Odds / Never Hit",
     "keywords": [
         "horrible odds", "terrible odds", "bad odds", "worst odds", "awful odds",
         "impossible odds", "odds are terrible", "odds are awful", "odds are horrible",
         "odds are bad", "odds are trash", "odds are garbage", "the odds suck",
         "never hit a grail", "never hit anything", "never pull a grail",
         "not a single high tier", "not a single high-tier", "not one high tier",
         "high tier grail", "high end grail", "high-end grail",
         "never a grail", "zero grails",
         "zero good cards", "0 good cards", "zero good pulls",
         "zero good cards ever", "not one good card",
         "never win anything", "never win", "can't win anything", "can't hit anything",
         "impossible to hit", "impossible to pull", "impossible to win",
         "opened 50 packs", "opened 100 packs", "opened 150 packs",
         "opened 200 packs", "opened 300 packs", "opened 400 packs", "opened 500 packs",
         "bought 50 packs", "bought 100 packs", "bought 200 packs",
         "all junk", "always junk", "only get junk", "only garbage", "all garbage",
         "all commons", "only commons", "only base cards", "never get a hit",
         "hit rate is terrible", "hit rate is awful", "hit rate is horrible",
         "zero hits", "no hits at all", "nothing good ever",
         # Drop rates language
         "drop rates suck", "drop rates are terrible", "drop rates are awful",
         "drop rates are horrible", "drop rates are bad", "drop rates are trash",
         "drop rate is", "drop rates so bad",
         # "Didn't hit" / consecutive packs with no result
         "didn't hit grail", "didn't hit anything", "didn't hit once",
         "not a single good pull", "not one good pull", "zero good pulls",
         "consecutive packs", "packs in a row", "in a row and nothing",
         "100 packs and", "200 packs and", "300 packs and", "400 packs and",
         "opened 30 packs", "opened 40 packs",
         "30 consecutive", "40 consecutive", "50 consecutive",
         # Pull/hit rate language
         "terrible pull rates", "bad pull rates", "pull rates are terrible",
         "pull rates are awful", "pull rates are bad", "pull rates suck",
         "pull rate is terrible", "pull rate is awful", "pull rate is bad",
         "hit rates are bad", "hit rates are terrible",
         # Common typo variants (no apostrophe)
         "cant hit anything", "cant win anything", "cant pull anything",
         # "All bricks" / generic empty-pull slang
         "all bricks", "all duds", "nothing but bricks",
         # "Never made money back" / profitability
         "never made my money back", "never made money back", "didn't profit off",
         "never profited", "can't profit", "never once profited",
         # Bigger pack-count patterns
         "opened 60 packs", "opened 70 packs", "opened 80 packs", "opened 90 packs",
         "bought 150 packs", "bought 300 packs",
         "15 packs and", "20 packs and", "25 packs and",
         # "I opened X packs and got nothing" — short-form common misses
         "got nothing good", "got absolutely nothing", "got zero good pulls",
         "not one good card", "not a single hit",
         # "Yet to hit" — ongoing failure across many attempts
         "yet to hit a grail", "yet to hit anything", "yet to hit a single",
         "have yet to hit", "still yet to hit",
         # "Losses in a row" — sustained losing streaks
         "losses in a row", "loss in a row", "losing streak",
         "12 losses", "10 losses", "15 losses", "20 losses",
         # "No good hits" / "not good pulls"
         "no good hits ever", "no good hits", "no good hit",
         "not good pulls", "terrible cards", "all terrible cards",
         "pulling terrible cards", "got terrible cards",
         # Missing pack-count patterns with filler words ("about", "over", "around")
         "about 60 packs", "about 50 packs", "about 100 packs", "about 200 packs",
         "over 100 1", "over 100 packs", "over 200 packs", "over 50 packs",
         "around 100 packs", "around 50 packs",
         # "100 rips" — using "rips" as the unit for pack opens
         "over 100 rips", "100 rips and", "100 rips later",
         "50 rips and", "200 rips and", "150 rips and",
         "done 100 rips", "done 50 rips", "over 50 rips",
         "60 packs and not", "50 packs and not", "20 packs and not", "30 packs and not",
         "no good card", "no good cards", "not one card worth",
         # "Never hits big" — common informal phrase
         "never hits big", "never hit big", "never hit anything big",
         # "Didn't give me my free pack" → false free pack promised
         "didn't give me my free pack", "didn't give me a free pack",
         "free pack they promised", "never got the free pack",
         # "Nothing worth keeping"
         "nothing worth keeping", "nothing worth having",
         # Referral / promo pack never received
         "referral links dont work", "referral link doesn't work",
         "referral link not working", "referral code didn't work",
         # "Super low hit rate"
         "super low hit rate", "very low hit rate", "extremely low hit rate",
         # "Odds suck"
         "odds suck", "odds suck on this", "the odds suck on",
         # "Rates are awful" (bare rates — bad pull ratio)
         "rates are awful", "rates are terrible", "rates are horrible",
         "poor pack odds", "poor pull odds",
         # "Win nothing good"
         "win nothing good", "won nothing good", "you win nothing",
         # "Worse than slots / a slot machine"
         "worse than slots", "worse than a slot machine", "worse than the casino",
         "worse than gambling at a casino", "at a casino you have a chance",
         "can't even call it gambling", "can't even be described as gambling",
         # "Low win percentage" — explicit percentage framing
         "low win percentage", "extremely low win percentage",
         "very low win percentage", "win percentage rates are low",
         # "Going to lose everything" / "gonna lose everything"
         "gonna lose everything", "going to lose everything",
         "will lose everything", "you'll lose everything",
         # "Guaranteeing constant losses" — fee-stacking argument
         "guaranteeing constant losses", "guarantees you lose", "guarantee you'll lose",
         "constantly guaranteeing losses", "guarantees losses",
         # "Odds changed for the worst" — post-update complaint
         "odds changed for the worst", "odds changed for the worse",
         "odds got worse", "odds have gotten worse", "odds went downhill",
         # "Just giving them money" — all-loss framing
         "just giving them money", "just giving away money",
         "just giving your money away", "just giving money away",
         "throwing money at them", "handing them money",
         # "Never pulled anything more than half of what i paid"
         "never pulled anything more than half", "never got more than half",
         "never get more than half of what", "always get less than half",
         # "At no point are you ever profitable"
         "at no point are you ever profitable", "at no point are you profitable",
         "never once profitable", "you'll never be profitable",
         "you will never be profitable",
         # "Odds are bs" — crude but common dismissal
         "odds are bs", "odds are total bs", "the odds are bs",
         "odds are absolute bs", "odds are complete bs",
         "percentages are bs", "the percentages are bs",
         # "Loss on almost every pull" / "losing on every pull"
         "loss on almost every", "loss on every pull", "loss every pull",
         "losing on every pull", "losing on almost every pull",
         "loss almost every time", "lose almost every pull",
         # "Dud after dud" — stacking loss streak
         "dud after dud", "brick after brick", "common after common",
         "miss after miss", "garbage after garbage",
         # "Never once pulled one" — specific miss phrase
         "never once pulled one", "never once hit one",
         "never once pulled anything", "never once got anything",
         # "Hit rates were horrible" — past-tense plural
         "hit rates were horrible", "hit rates were terrible",
         "hit rates were awful", "hit rates were bad",
         # "Not hit even a dollar" — couldn't pull a dollar's worth
         "not hit even a dollar", "never hit even a dollar",
         "couldn't hit a dollar", "cant hit a dollar",
         # "Went X/50 on" — fractional miss patterns (common gaming slang)
         "went 1/50", "went 0/50", "went 1/100", "went 0/100",
         "went 0/", "0 for 50", "0 for 100",
         # "Grail rates have been slashed" — post-update complaint
         "grail rates have been slashed", "grail rates were slashed",
         "grail rates slashed", "grail rates dropped",
         # "50/50 odds never got it" — advertised vs real odds
         "50/50 odds never", "advertised 50/50",
         "advertised odds never", "promised odds never",
         # "All you get are doubles" — post-update junk pulls
         "all you get are doubles", "only get doubles",
         "nothing but doubles", "only doubles",
         # "Don't get the max odds" — personalized odds complaints
         "don't get the max odds", "dont get the max odds",
         "never get the max odds", "never got max odds",
         # "Only 50% of users receive it" — personalized odds discrimination
         "only 50% of users receive", "not all users get max odds",
         # "Poor pull rates" — missing compound phrase
         "poor pull rates", "poor pull rate", "pull rates are poor",
         # "Adjust odds based on your account" — account-age manipulation
         "adjust odds based on your account", "based on account age",
         "odds based on account", "adjusts odds based on",
         # "Never seen anything over $5" — max value cap
         "never seen anything over", "never pulled anything over",
         "never got anything over", "never hit anything over",
         # "Odds are way off" — general misalignment
         "odds are way off", "odds way off", "odds are totally off",
         "odds on the pulls are way off",
         # "Barely getting anything good" — ongoing poor results
         "barely getting anything good", "barely ever get anything good",
         "rarely get anything good",
         # "Odds are intended to deceive"
         "odds are intended to deceive", "intended to deceive",
         # "No good pulls" — bare form
         "no good pulls", "no good pull",
         # "Always getting commons" — present-progressive form
         "always getting commons", "always getting base cards",
         "always getting duds", "always getting junk",
         # "Yet to see anything over" — threshold miss
         "yet to see anything over", "yet to pull anything over",
         # "None of them have been worth" — batch value disappointment
         "none of them have been worth", "not one of them was worth",
         "not a single one was worth", "none were worth",
         # "Opened 45 packs" / small counts not yet covered
         "opened 45 packs", "opened 27 packs", "opened 35 packs",
         "opened 25 packs", "45 packs and", "27 packs and",
         # "3K packs and no hits"
         "3k packs", "3000 packs", "close to 3k packs",
         # "Lucky if you break even" — profit is luck, not design
         "lucky if you break even", "lucky to break even",
         "lucky if you even break even",
         # "No hits" — bare form (covers "going to 3k packs and no hits")
         "3k packs and no hits", "3000 packs and no hits",
         # "Haven't been able to win since the update"
         "haven't been able to win since", "havent been able to win since",
         "unable to win since the update", "can't win since the update",
         # "The more you play the worse your odds get"
         "the more you play the worse your odds", "more you play the worse your odds",
         "odds get worse the more you play", "odds get worse as you play",
         # "Just buy a lotto ticket" — external gambling comparison
         "just buy a lotto ticket", "better off buying a lottery ticket",
         "buy a lotto ticket", "buy a lottery ticket instead",
         # "Haven't won anything" — running zero outcome
         "haven't won anything", "havent won anything",
         "haven't won a single thing", "haven't won once",
         # "More losing than winning"
         "more losing than winning", "more losses than wins",
         "lose more than you win",
         # "Worse than playing slots"
         "worse than playing slots", "worse than slots",
         "worse than a slot machine",
         # "Haven't won nothing" — double-negative informal form
         "haven't won nothing", "havent won nothing",
         "never won nothing",
         # "Lost more than I win" / "lose more than I win"
         "lost more than i win", "lose more than i win",
         "i lose more than i win", "lost more than i've won",
         # "Better odds on" [competitor] — comparative odds complaint
         "better odds on crowncards", "better odds on other apps",
         "better odds elsewhere", "better odds somewhere else",
         # "Luck is horrible" / "horrible luck"
         "luck is horrible", "luck was horrible", "luck is terrible",
         "luck is awful", "horrible luck", "terrible luck",
         "awful luck on this app",
         # "Nothing good in packs" — general empty-pull complaint
         "nothing good in packs", "nothing really good in packs",
         "nothing good ever comes from packs",
         "there's nothing good in the packs",
         # "400 deep" / "N deep" — deep pack count with no hits
         "400 deep", "300 deep", "200 deep", "500 deep",
         "100 packs deep",
         # "Worse odds than a slot machine" / typo variant (then vs than)
         "worse odds then a slot machine", "worse odds than a slot machine",
         # "Nothing but bad/worthless cards" — near-zero value hits
         "nothing but bad cards", "nothing but worthless cards",
         # "Nothing in return" / "get nothing in return"
         "nothing in return", "get nothing in return",
         # "Never won any money"
         "never won any money",
         # "Probability is way off" — math-framed complaint
         "probability is way off", "probabilities are way off",
         # "Haven't gotten a good hit"
         "haven't gotten a good hit", "haven't gotten any good hits",
         "havent gotten a good hit",
         # "No hits over a dollar" — sub-dollar value ceiling
         "no hits over a 1.00", "no hits over a dollar",
         "no hits over $1", "no hits over 1 dollar",
         # "Needs better odds" / "need better odds" — bare imperative complaint
         "needs better odds", "need better odds", "needs bette odds",
         "needs much better odds", "needs way better odds",
         # Pack-count written out — "two hundred packs", "four hundred packs"
         "two hundred packs", "four hundred packs", "five hundred packs",
         "three hundred packs",
         # "4k packs" / "5k packs" — large count slang
         "4k packs", "4000 packs", "5k packs", "5000 packs",
         "after 4k packs", "after 5k packs", "4k pack",
         # "Odds aren't correct" / "odds are not correct"
         "odds aren't correct", "odds are not correct",
         "odds arent correct", "odds aren't even correct",
         # "Bad pull rate" (singular, no 's') — specific form
         "bad pull rate", "terrible pull rate", "awful pull rate",
         "very bad pull rate", "really bad pull rate",
         # "You'll find better luck at a casino" — casino comparison
         "you'll find better luck at a casino", "find better luck at a casino",
         "better luck at a casino",
         # "Long runs of missing 50/50" — sustained losing streak on 50/50 spins
         "long runs of missing 50/50", "long run of missing 50/50",
         "missing 50/50 spins", "keep missing 50/50",
         # "1300 packs" / "1k pulls" — very large pack count with no hits
         "1300 packs", "over 1300 packs", "opened 1300",
         "1k pulls", "1000 pulls", "1k rips",
         "over 1000 packs", "over 1k packs",
         # "Not one card was equal to the value of the pack" — value ceiling miss
         "not one card was equal to the value",
         "not one card equal to", "not one card worth",
     ]},

    # ── Not Worth the Money ───────────────────────────────────────────────────
    {"id": "value-bad", "name": "Not Worth the Money",
     "keywords": [
         "not worth it", "not worth the money", "not worth the price", "not worth what",
         "not worth spending", "not worth your money", "not worth my money",
         "waste of money", "wasted my money", "wasted money", "total waste of money",
         "complete waste of money", "money down the drain", "throwing money away",
         "throwing my money away", "down the drain",
         "rip off", "rip-off", "ripoff", "ripped off", "getting ripped off",
         "highway robbery", "robbery",
         "overpriced", "way too expensive", "way overpriced", "grossly overpriced",
         "prices are ridiculous", "prices are insane", "prices are outrageous",
         "burn through money", "burning through money", "burn through my money",
         "drains your wallet", "drains your bank",
         "not getting my money back", "want my money back", "requesting a refund",
         "demand a refund",
         # Economics of always losing
         "lose money every time", "always lose money", "you will lose money",
         "will definitely lose", "guaranteed to lose", "always end up losing",
         "always losing money", "you lose every time", "lose every single time",
         "lose way more", "lose more than", "spend more than you get",
         "get less than", "worth less than", "cards aren't worth",
         "cards are not worth", "card was worth", "got a card worth",
         "card worth less", "value is way off",
         # "Never broke even" / can't profit from packs
         "never broke even", "can't break even", "couldn't break even",
         "haven't broken even", "never once broke even", "barely broke even",
         "never made my money back", "never got my money back",
         # Consistent losing value
         "always lose value", "always at a loss", "always come out behind",
         "always get less", "always getting less", "consistently lose",
         "cards worth less than", "always get a card worth less",
         "card is worth less than the", "worth less than the cost",
         "worth less than what i paid", "less than i paid",
         "less than what i spent", "less than the price",
         # "Never profit" / "can't profit"
         "never profit", "can't profit", "never once profited", "never profited",
         "don't profit", "didn't profit",
         # "Got rinsed" / "completely rinsed"
         "got rinsed", "completely rinsed", "getting rinsed", "got absolutely rinsed",
         # "Don't waste your money" — imperative complaint; negation check looks before "don't"
         "don't waste your money", "do not waste your money",
         "never get your money's worth", "never get your moneys worth",
         "won't get your money's worth", "won't get your moneys worth",
         # Junk / garbage cards every time
         "every pack is garbage", "every pack has been garbage", "every rip is garbage",
         "every rip is a garbage", "always get garbage", "always get junk cards",
         "spent money for junk", "spent for junk", "paid for junk",
         "spent hundreds for nothing", "spent money for nothing",
         # "Garbage card" / never worth the price
         "garbage card", "garbage cards", "got garbage cards", "pulled garbage cards",
         "never worth the price", "not ever worth the price",
         # Value < cost — "never equal or greater"
         "not equal or greater", "never equal or greater", "not once equal",
         "always less than what", "always worth less than what",
         # "Can't break even" typo / colloquial variants
         "can't break even", "cant break even", "could never break even",
         # "Ripping me off" — active present-tense variant of "ripped off"
         "ripping me off", "ripping you off", "they're ripping you off",
         "this app is ripping", "app is ripping me off",
         # "You lose money more often"
         "lose money more often", "you lose money more", "lose more money than",
         # Implicit loss statements ("I lost so much")
         "lost so much money", "lost so much already", "lost so much on this",
         "i've lost so much", "i lost so much",
         "losing so much money", "lost a lot of money on this",
         # "Like losing money" warning phrases
         "like losing money", "enjoy losing money", "love losing money",
         # Value mismatch — cards worth much less than packs
         "not even close to what you actually", "not even close to what you pull",
         "not what was advertised", "not as advertised",
         # "Not one time equal or greater" — Arena Club protection fee context
         "not one time equal or greater", "never once equal or greater",
         "not equal in value", "never equal in value",
         # "Not worth the time"
         "not worth the time", "never worth the time",
         # "Money pit"
         "money pit", "total money pit", "it's a money pit",
         # "Way below value"
         "way below value", "way below the value", "far below value",
         "cards way below value", "cards way below what i paid",
         # "Spend more than you ever make"
         "spend more than you ever make", "spend more than you'll ever make",
         "spend more than you could ever win", "spend way more than you get back",
         # "Good at first but now you lose"
         "good at first but now", "great at first but now",
         # "Under value of what I spent" — value mismatch
         "under value of what i spent", "under value of what i spend",
         "all were under value", "all where under value",
         # Fee structure complaints
         "fee structure is unfair", "unfairly stacked against",
         "steep secondary fees", "charged steep fees", "excessive fees",
         # "I'm constantly losing money" / ongoing
         "keep losing money", "keep on losing money",
         # "Paying 10x the value" — icybox style
         "paying 10x the value", "10x the value",
         "paying way more than", "paying way more than the value",
         # "Not even close in value"
         "not even close in value",
         # Spending hundreds without good cards
         "spent hundreds without", "spent hundreds and got",
         "put in hundreds and",
         # "You'll lose your money" — future-tense warning
         "you'll lose your money", "youll lose your money",
         "you will lose your money here", "going to lose your money",
         "gonna lose your money",
         # "Never pulled anything more than half of what i paid"
         "never pulled anything more than half of what i paid",
         "never get more than half of what i paid",
         "always less than half of what i paid",
         "half the value of whatever box", "half the value of the box",
         "half of what i paid", "half of what you paid",
         # "Lost money every time" — past-tense variant
         "lost money every time", "lost money each time",
         "always lost money", "i always lost money",
         # "Quickest way to lose" — warning-label phrasing
         "quickest way to lose", "fastest way to lose",
         "easy way to lose money", "easiest way to lose money",
         # "All I do is lose money" — ongoing financial loss frame
         "all i do is lose money", "all i ever do is lose money",
         "all you do is lose money", "all you ever do is lose money",
         # "Eat your money" — money-drain metaphor
         "eat your money", "eats your money", "eating your money",
         "eat through your money", "eats through your money",
         "slowly eat your money", "slowly eats your money",
         "eat through your wallet", "eats through your wallet",
         # "Not worth the investment"
         "not worth the investment", "never worth the investment",
         # "Absolutely cooked" — slang for big loss
         "absolutely cooked", "got cooked on this",
         "got cooked", "getting cooked",
         # "Poof good bye" — money vanished
         "poof good bye", "poof goodbye",
         # "Loose more than you win" — typo variant of "lose"
         "loose more than what you win", "loose more money than",
         "loose more than you win",
         # "Waist your money" — typo variant of "waste"
         "waist your money", "waist you money", "waist of money",
         # "Waste of $" — using dollar sign instead of "money"
         "waste of $",
         # "Lost a lot of money" — bare form (without "on this")
         "lost a lot of money",
         # "For digital cards" — real money for digital-only items
         "for digital cards", "real money for digital",
         # "Need real world money" — high spending requirement
         "need real world money", "real world money constantly",
         # "Every single pack gave me" [bad result] — pack pattern
         "every single pack gave me",
         # "Hardly pull rares" — low rare/hit rate
         "hardly pull rares", "rarely pull rares",
         "hardly ever pull rares",
         # "Waste of funds" — formal word for "money"
         "waste of funds", "waste of my funds",
         # "Charge you 10% to sell back"
         "charge you 10% to sell", "10% to sell it back",
         "charge 10% to buy back", "10% buyback fee",
         # "Better off buying cards" at retail
         "better off buying cards", "better off just buying",
         # "Lost a lot of money" (more variants)
         "lost so much money on", "i've lost a lot",
         # "-$50" loss notation (common shorthand)
         "-$50", "-$100", "-$200", "-$300",
         # "Ate so much money" — different tense from "eats your money"
         "ate so much money", "ate my money", "ate all my money",
         "it just ate my money",
         # "One card is not a pack" — value expectation mismatch
         "one card is not a pack", "one card is not a full pack",
         # "Barely got anything back" / ongoing value loss
         "barely got anything back", "barely getting anything back",
         "barely winning my money back", "barely win my money back",
         # "Money hungry game"
         "money hungry game", "money hungry app", "money hungry",
         # "Buyback is cheaper than the watch" — resell discount complaint
         "buy back is cheaper", "buyback is cheaper",
         "90 percent buyback", "only 90% buyback", "90% buyback",
         # "More like rips off"
         "more like rips off",
         # "Hardly anything worth"
         "hardly anything worth", "hardly worth anything",
         # "Lowered the floor" — updated cards are worth less
         "lowered the floor cards", "lowered the floor",
         "lowered the card floor",
         # "None of them have been worth the cost"
         "none of them have been worth",
         # "Usual winners every time" — rigged for regulars
         # moved to rigged section
         # "Lose your money" (bare imperative warning)
         "lose your money", "lose all your money",
         "lose a bunch of money", "lose a ton of money",
         # "Lose it all within" — rapid-drain pattern
         "lose it all within", "lost it all within",
         # "Took a fee" — unexpected fee charge
         "took a fee", "takes a fee", "taking a fee",
         "charged a fee", "charges a fee",
         # "Spend $100 for a $40 card" — specific mismatch
         "spend $100 for a", "spent $100 for a",
         "spend $50 for a", "spent $50 for a",
         # "Better off at a casino" — gambling comparison
         "better off at a casino", "better off going to a casino",
         # "Barely winning" from bad pulls
         "barely winning", "barely break even on",
         # "Just buy real packs"
         "better off buying real packs", "just buy real packs",
         "better off buying the physical cards",
         # "If you like making donations" — sarcasm for value loss
         "if you like making donations", "like making a donation to them",
         "just making a donation", "might as well donate your money",
         "good app if you like donating",
         # "Spent over $100 and only got" — non-dollar-sign amount variants
         "spent over 100", "spent over $100 and only got",
         "over 100 and got nothing", "over 100 dollars and",
         "spent $300 and got nothing", "spend 300 dollars",
         "spent 300 dollars", "spent 300 and got",
         "2 grand and got nothing", "spent 2 grand", "2 thousand dollars",
         # "Stop while you're ahead" — sarcastic loss warning
         "stop while you're ahead", "stop while you are ahead",
         # "Don't get anything close in value"
         "don't get anything close in value", "nowhere near the value",
         "nothing close to the value of the pack",
         # "Play to lose" — game designed against the player
         "play to lose", "designed to lose", "made to lose",
         "it's just pay to lose", "pay to lose",
         # "$10 minimum deposit" — entry barrier complaint
         "$10 minimum deposit", "$5 minimum deposit",
         "minimum deposit requirement", "minimum deposit for a",
         # "Turn $200 into $3" — dramatic loss illustration
         "turn $200 into", "turned $200 into",
         "turn $100 into", "turned $100 into",
         "turn $50 into", "turned $50 into",
         # "Funnels money out" — systematic slow drain
         "funnels money", "funnels your money", "funnels money out",
         "funnel money out of your", "funnels money slowly",
         # "10 cent cards" / "nothing but 10 cent cards" — extreme low-value hits
         "10 cent cards", "nothing but 10 cent cards",
         "all 10 cent cards", "pulling 10 cent cards",
         "0.10 cent cards", ".10 cards",
         # "Not market value" / "not at market value" — pricing mismatch
         "not market value", "not at market value",
         "below market value", "way below market value",
         "not at market price", "below market price",
         "way below market",
         # "Never gets your money's worth" — third-person singular form
         "never gets your moneys worth", "never gets your money's worth",
         "never gets their money's worth",
         # "Barely the cost of the pack" — hitting cards barely worth the pack price
         "barely the cost of the pack", "barely worth the cost of the pack",
         "barely covers the cost of the pack",
         "barely worth what the pack costs",
         # "Raised the price" — pack price increase complaint
         "raised the price", "raised the prices",
         "raised the pack price", "increased the pack price",
         "raised it by $5", "raised it $5",
         # "Negative payout" — always losing money framing
         "negative payout", "always have a negative payout",
         "always a negative payout", "consistently a negative payout",
         # "Added a sales tax" — unexpected fee added to purchase
         "added a sales tax", "added sales tax", "now they charge sales tax",
         "charging sales tax", "added a tax",
         # "Worth even 80% of pack price" — expressing ROI frustration
         "worth even 80% of pack price", "80% of pack price",
         "80% of what you paid", "barely 80% of", "less than 80% of",
         "worth the pack price", "worth the price of the pack",
         "not worth the cost of the pack",
     ]},

    # ── Scam / Misleading / Fraud ─────────────────────────────────────────────
    {"id": "trust", "name": "Scam / Misleading / Fraud",
     "keywords": [
         "it's a scam", "this is a scam", "total scam", "complete scam", "straight up scam",
         "absolute scam", "100% scam", "obvious scam",
         "false advertising", "false advertisement", "misleading advertising",
         "bait and switch", "bait-and-switch",
         "deceptive", "deception", "fraudulent", "fraud",
         "they're lying", "they are lying", "they lied", "you lied",
         "not as advertised", "doesn't match description", "misrepresented",
         "false claims", "misleads you", "false promises", "they lure you",
         "they scam you", "scammed me", "i was scammed",
         "fake reviews", "paid reviews", "bought reviews",
         "don't trust", "can't be trusted", "untrustworthy",
         "sketchy practices", "shady practices", "shady business",
         "money grab", "cash grab",
         # Missing scam variants
         "this app is a scam", "app is a scam", "the app is a scam",
         "basically a scam", "pretty much a scam", "kind of a scam",
         "it's basically a scam", "this is basically a scam",
         "should be illegal", "this should be illegal", "has to be illegal",
         "getting scammed", "get scammed", "you get scammed",
         "they take your money", "taking your money", "just takes your money",
         "in it for the money",
         # "The biggest scam ever" — superlative forms
         "biggest scam", "biggest scam ever", "the biggest scam",
         "worst scam", "obvious fraud",
         # Unauthorized charges
         "took money from my card", "charged my card without", "unauthorized charge",
         "charged outside the app", "took money outside",
         # "Feels like a scam" — subjective impression
         "feels like a scam", "feel like a scam", "feeling like a scam",
         "feels like a ripoff", "feel like a rip off", "feels like a rip-off",
         # "This is predatory"
         "this is predatory", "app is predatory", "predatory business",
         # Unauthorized drain of card
         "drained of the full value", "card was drained", "my card was drained",
         "prepaid card was drained", "drained my prepaid",
         # "Unconsented charges"
         "unconsented charges", "unconsentual charges",
         # Free packs promised but not delivered
         "never got my free pack", "never received my free pack",
         "free pack never shows up", "free weekly pack never",
         "supposed to get a free pack", "promised a free pack",
         "free promotion pack", "sign up pack never",
         "notification says free pack but", "app says free pack but",
         # "Automatically sell the card" — vault expiry surprise
         "automatically sell the card", "automatically sells your cards",
         "auto-sells your card", "sell your card without permission",
         # "They sent me the wrong card" / wrong item
         "sent me the wrong card", "sent me a card that wasn't",
         "not what i ordered", "received the wrong",
         # Lied about free / misleading claims
         "ad said try for free", "ad said it was free", "says it's free but",
         "nothing is for free", "nothing is free on this app",
         # "Forces you to spend money"
         "forces you to spend", "forced to spend money", "forces me to spend",
         # "Ads are misleading"
         "ads are misleading", "ads were misleading", "the ads are misleading",
         "misleading ads", "misleading advertisement", "misleading commercials",
         # "Bait n switch" (no apostrophe variant)
         "bait n switch",
         # "Steal money from your account"
         "steal money from your account", "steal money straight from",
         "stealing money from my account", "stole money from my account",
         "stole money without", "stolen money from",
         # "Didn't get my referral" / promo not honored
         "didn't get my referral pack", "didnt get my referral",
         "never got my referral", "referral pack never received",
         "didn't get my referral", "never got a referral",
         "sign up pack never", "signup bonus never",
         "deposit match never", "never got my deposit match",
         "promo not working", "promotion not applied",
         # Bare "scam" / "scammer" — short reviews that just say the word
         "scam", "scammer", "scam app", "scammer app",
         "what a scam", "what is this scam", "what even is this scam",
         # "Steal your money" — bare accusation (without "from account")
         "steals my money", "stealing your money",
         # "Digital gambling" — explicit gambling framing
         "digital gambling",
         # "Take your money" — bare form (we have "they take your money" but not bare)
         "take your money", "takes your money", "just gonna take your money",
         "will take your money", "to take your money",
         # "Done nothing but steal money from me"
         "done nothing but steal money", "does nothing but take your money",
         "does nothing but steal", "nothing but take your money",
         # "Constant bots" / fake-user manipulation
         "constant bots", "bots that are made to look like users",
         "artificial buyers", "artificial bids",
         # "Nowhere near advertised" — false marketing
         "nowhere near advertised", "no where near advertised",
         "doesn't hit anywhere near advertised", "hit nowhere near",
         "not even close to what they advertise", "not what they advertised",
         # "Trap your funds" / lock money in app
         "trap it", "trap your money", "trap your funds",
         "traps your money", "money trapped in the app",
         "funds stuck in the app", "stuck in the app",
         # "Card number got stolen" — security/data compromise
         "card number got stolen", "card number was stolen",
         "number got stolen", "my card got stolen",
         # "Buy cheap cards and then sell them" — inventory manipulation
         "buy multiples of cheap cards", "buy cheap cards for their vault",
         "they don't actually have the cards", "they dont actually have the cards",
         # "Banned me for no reason"
         "banned for no reason", "banned me for no reason",
         "banned with no reason", "banned without reason",
         # "Spend X and THEN ask for your ID" — delay-ID bait
         "spend money and then ask for your id", "spend money then ask for id",
         "makes you spend money and then", "put money in and then ask",
         # "Thieves" / "thief" — blunt accusation
         "thieves", "thief", "theif", "theifs",
         "these guys are thieves", "they are thieves",
         # "Rug pull" / "pump and dump" — crypto-style scam language
         "rug pull", "pump and dump", "feels like a rug pull",
         # "Unregulated gambling"
         "unregulated gambling",
         # "Lie about value" / fake card values
         "lie about value", "lies about value", "lied about value",
         "fake values", "inflated values", "lying about the value",
         "who price checks", "price check these",
         # "Just takes money" — bare verb form (shorter than existing variants)
         "just takes money", "only takes money", "only takes your money",
         # "Refused to refund me" — dispute/charge complaint
         "refused to refund me", "refused to refund", "refuse to refund me",
         "wouldn't refund me", "wouldnt refund me", "won't give me a refund",
         # "Free pack ain't free" — misleading marketing
         "free pack ain't free", "free pack isnt free", "free pack is not free",
         # "Lies to you" / "it lies to you" — shorter accusation forms
         "lies to you", "lying to you", "it lies to you", "they lie to you",
         "lied to me", "this app lies",
         # "Just wanted my money" / "just wants your money"
         "just wanted my money", "just wants my money",
         "only wanted my money", "just want your money",
         "just wants your money",
         # "They pulled some shady move"
         "shady move", "pulled a shady move",
         # "Packs where free" — common typo of "were"
         "packs where free", "said packs were free",
         "thought the packs were free", "said the packs were free",
         # "Percentages are misleading"
         "percentages are misleading", "misleading percentages",
         "odds are misleading", "misleading odds",
         # "Ad said first rip is free" — deceptive ad claim
         "ad said first rip is free", "said first rip is free",
         "first rip is free", "first rip free",
         "commercial says free", "ad says free",
         # "Only get 1 free pack" — minimal free offering is misleading
         "only get 1 free pack", "only get one free pack",
         "only 1 free pack", "only one free pack",
         # "Force you to buy" — coercive design
         "force you to buy", "forces you to buy",
         "forcing you to buy",
         # "It's all a lie" — blunt dismissal
         "it's all a lie", "its all a lie", "all a lie",
         "it's one big lie", "its one big lie",
         # "Just took my money" — past-tense bare charge accusation
         "just took my money", "it just took my money",
         "app just took my money",
         # "Took money from my venmo" — specific payment method
         "took it out of my venmo", "took money from my venmo",
         "took money from my paypal and never applied",
         "took money from my paypal",
         # "Cards shipped are fake"
         "cards shipped are fake", "cards are fake", "shipped fake cards",
         "fake cards", "cards are counterfeit",
         # "Nowhere to enter code" — missing UI element
         "nowhere to enter code", "no where to enter code",
         "nowhere to enter the code",
         # "Misleading company"
         "misleading company", "company is misleading",
         # "Didn't get a free pack after downloading"
         "didn't get a free pack after downloading",
         "did not get a free pack after downloading",
         "downloaded it and no free pack",
         # "Forfeiture of balances" — TOS banning risk
         "forfeiture of balances", "forfeiture of your balance",
         "forfeit your balance", "forfeit my balance",
         # "Not in stock" / "sold out" — bait card unavailable after win
         "not in stock", "isn't in stock", "was not in stock",
         "out of stock", "sold out of that", "sold out",
         # "Don't be fooled by the ads" — ad deception warning
         "don't be fooled by the ads", "fooled by the ads",
         "dont be fooled by the ads",
         # "Tryna take your money" — informal accusation
         "tryna take it", "tryna take your money", "tryna take my money",
         # "Harassing me with their ads" — ad fatigue / deceptive-ad complaint
         "harassing me with their ads", "harassing me with ads",
         "harassed by their ads",
         # "Promo code didn't work"
         "promo code didn't work", "promo code doesnt work",
         "promo code does not work", "promo code not working",
         "promo code wouldn't work",
         # "Did not receive a free card as advertised"
         "did not receive a free card as advertised",
         "didn't receive a free card as advertised",
         # "Wouldn't honor the free promo pack"
         "wouldn't even honor the free", "wouldn't honor the promo",
         "won't honor the promo", "didn't honor the promo",
         # "No option to enter a code" — missing UI for referral
         "no option to enter a code", "no option to enter code",
         "nowhere to enter the referral code",
         # "Approved a sale while I was asleep"
         "approved a sale while i was asleep",
         "sold my card while i was asleep",
         "approved a sale without my",
         # "Sold my card without my permission"
         "sold my card without my permission",
         "sold without my permission", "sold it without my",
         # "Double charged me"
         "double charged me", "double charged",
         "charged me twice", "charged twice",
         # "Only gave me half of my money back"
         "only gave me half of my money back",
         "gave me half my money back",
         # "Didn't give me my starter pack"
         "didn't give me my starter pack", "didnt give me my starter pack",
         "didn't give me a starter pack",
         # "Ads don't show you have to pay"
         "ads don't show that you have to pay",
         "ads dont show that you have to pay",
         "ads don't show you have to pay",
         # "Just another way to take your money"
         "just another way to get your money",
         "just another way for app developers",
         "just another money grab",
         # "Banned with no refund"
         "banned with no refund", "banned me with no refund",
         "banned my account with no refund",
         # "Swindle you out of your money" — strong deception verb
         "swindle you out of your money", "swindled me out of",
         "swindle you out of", "swindle out of money",
         # "Didn't receive my free card" — short form (no "as advertised")
         "didn't receive my free card", "did not receive my free card",
         "i didn't receive my free card", "never received my free card",
         # "Just kept taking my money" — past continuous drain
         "just kept taking my money", "kept taking my money",
         "kept taking my cash", "kept draining my account",
         # "No free packs without invite" — invite-gated model is misleading
         "no free packs without invite", "need an invite for free packs",
         "only free with an invite", "free pack only if you invite",
         # "Won't let me enter my code" / "won't let me put my code"
         "won't let me enter my code", "wont let me enter my code",
         "won't let me put my code", "wont let me put my code",
         "won't let me use my code", "wont let me use my code",
         # "Downloaded the app and did not receive" (free pack promised)
         "downloaded the app and did not receive",
         "downloaded and did not receive",
         "downloaded it and did not receive",
         # "It's just gambling" / "legal gambling" — explicit gambling characterization
         "it's just gambling", "its just gambling", "it's literally gambling",
         "this is just gambling", "basically gambling",
         "legal gambling", "legalized gambling",
         "it's legal gambling", "its legal gambling",
         # "Stop giving you free stuff" → bait-and-switch after initial freebies
         "stopped giving free", "stops giving you free",
         # "Sent my refund to a card I don't own" — wrong payment reversal
         "sent my refund to a card i don't", "refunded to the wrong card",
         "sent refund to wrong card", "refund went to wrong card",
         # "Gambling disguised as not gambling"
         "gambling disguised as", "disguised as gambling",
         "disguised as not gambling",
         # "Just another gambling app"
         "just another gambling app", "yet another gambling app",
         "just another casino app",
         # "Won't let me use referral codes" — referral code UI blocked
         "won't let me use referral codes", "wont let me use referral codes",
         "won't let me use a referral code", "wont let me use a referral code",
         # "Advertisement doesn't say you have to pay" — ad deception
         "advertisement doesn't say you have to pay",
         "advertisement doesn't say you have to pay into",
         "ad doesn't say you have to pay",
         # "You have to pay just to open packs" — pay-to-play frustration
         "have to pay just to open packs", "you have to pay to open packs",
         "have to pay to open packs", "pay just to open packs",
         # "Deleted my card I got for a referral" — vault/deletion without consent
         "deleted my card i got for a referral", "deleted the card i got for referral",
         "deleted my referral card", "they deleted my card",
         # "Replaced with a different card" — bait and switch after win
         "replaced with a different card", "replaced my card with a cheaper",
         "replaced the card with", "card was replaced with",
         "switched my card to a different",
         # "Free pack from a friend's code" — referral pack not working
         "free pack from a friend's code", "free pack from a friend",
         "free pack from referral code", "free pack from my friend",
         # "Not like the video ad claims" — ad vs reality mismatch
         "not like the video ad", "not like their video ad",
         "video ad doesn't show", "their video ad lies",
         # "Real money for digital cards" — pay-to-play value complaint
         "real money for digital cards", "real dollars for digital cards",
         "real money for virtual cards",
         # "The commercial lies" — broadcast ad deception
         "the commercial lies", "commercial lies about",
         "their commercials lie", "commercials lie",
         # "Filing a dispute with my card" — chargeback action
         "filing a dispute with my card", "filed a dispute with my card",
         "had to file a dispute", "filed a chargeback",
         "filing a chargeback",
         # "Fake app" — blunt authenticity accusation
         "fake app", "it's a fake app", "this is a fake app",
         "totally fake app",
         # "Have to invite people for free packs" — invite-gating complaint
         "have to invite people for free packs", "need to invite people for free packs",
         "only free if you invite", "have to invite for free packs",
         # "Drag you in just to take" — bait-and-switch trust complaint
         "drag you in just to take", "drag you in to take your",
         "dragged me in just to take", "drag you in and take",
         # "No money to be made" — zero profit guarantee
         "no money to be made", "there is no money to be made",
         "no money to be made here",
         # "Referrals don't get rewarded" — referral system broken
         "referrals don't get rewarded", "referrals dont get rewarded",
         "referrals are not rewarded", "referral rewards don't work",
         # "Robbed me of my money" — strong theft accusation
         "robbed me of my money", "this app robbed me",
         "they robbed me", "robbed me", "robbing me of my money",
         # "Blacklisted" — account banned/blacklisted permanently
         "blacklisted forever", "blacklisted", "you're blacklisted",
         "account gets blacklisted", "permanently blacklisted",
         # "Annoying ads" — aggressive ad campaign complaint
         "annoying ads", "super annoying ads", "ads are annoying",
         "the ads are annoying", "ads are so annoying",
         "ads are too long", "ads are too frequent",
         "too many ads", "way too many ads",
         # "Took my money" — bare past-tense accusation (without "just")
         "took my money", "it took my money",
         # "Resealed" / "resealed pack" — tampered product received
         "resealed", "resealed pack", "pack was resealed",
         "resealed packs",
         # "Loophole to allow kids to gamble" — underage gambling accusation
         "loophole to allow kids to gamble", "kids to gamble",
         "letting kids gamble", "allows kids to gamble",
         # "Ponzi scheme" — scam structure accusation
         "ponzi scheme", "ponzee scheme", "ponzi scam",
         "pyramid scheme",
         # "Class action lawsuit" — legal action threat
         "class action", "class-action", "class action lawsuit",
         "file a class action", "class action suit",
         # "Will be suing" / "suing for my money" — individual legal threat
         "will be suing", "suing for my money", "going to sue",
         "gonna sue", "filing a lawsuit", "will sue",
     ]},

    # ── App Bugs / Crashes ────────────────────────────────────────────────────
    {"id": "bugs", "name": "App Bugs / Crashes",
     "keywords": [
         "app crashes", "app crashed", "keeps crashing", "constantly crashing",
         "always crashes", "crashes all the time", "crashes every time",
         "app freezes", "keeps freezing", "app froze", "frozen app",
         "won't load", "not loading", "fails to load", "stuck loading",
         "infinite loading", "loading screen forever",
         "won't open", "can't open the app", "app won't open",
         "glitches", "glitching", "glitch out", "glitched out", "full of glitches",
         "full of bugs", "so many bugs", "buggy app", "tons of bugs",
         "error message", "getting an error", "throws an error", "keeps erroring",
         "app is broken", "broken app", "completely broken",
         "doesn't work", "stopped working", "not working properly", "nothing works",
         "black screen", "white screen", "blank screen",
         "can't stream", "stream cuts out", "video cuts out", "buffering",
         "can't watch", "won't play", "video won't play",
         "lost my data", "lost my history", "data missing", "orders disappeared",
         # Auction / bidding bugs
         "auctions aren't working", "auctions not working", "auction is broken",
         "auction broke", "auctions are broken", "auction glitch", "auction glitching",
         "bidding system glitch", "bidding glitch", "bid glitch",
         "auctions broken", "auction stopped working",
         # Verification / keyboard bugs
         "verification code won't", "can't enter verification", "keyboard stops working",
         "keyboard doesn't work",
         # "Screen turned/went black" — common phrasing different from "black screen"
         "screen turned black", "screen went black", "screen goes black",
         "screen turned white", "screen went white",
         "app went black", "app turned black", "goes black on me",
         # "Can't click" / totally non-functional
         "can't click on anything", "cant click on anything",
         "can't tap anything", "nothing is clickable",
         # GPS / permission loops
         "gps pop up", "won't let me run", "force closes",
         # Deposit not crediting
         "deposited and it", "money never showed", "deposit didn't go through",
         "deposit not showing", "didn't put the money",
         # General performance
         "excessive animation", "too many animations", "app is unusable",
         # Deposit / payment not working
         "not letting me deposit", "won't let me deposit", "wont let me deposit",
         "can't deposit money", "cant deposit money", "cant deposit",
         "can't add money to my account", "cant add money", "money won't deposit",
         "deposit keeps failing", "deposit is failing", "deposit failed",
         # Google Pay / payment methods not working
         "google pay not working", "gpay is not working", "gpay not working",
         "google pay doesn't work", "can't use google pay", "cant use google pay",
         "payment method not working", "payment not going through",
         # "Game froze" — different from "app froze"
         "game froze", "game freezes", "game frozen",
         # "Screen blackout" — different phrasing from "black screen"
         "screen blackout", "black out after a pull", "blackout after a pull",
         "blacks out after", "app blacks out",
         # "Randomly stops working"
         "randomly stops working", "randomly stopped working",
         "stops working randomly", "suddenly stopped working",
         # "Won't let me buy" — payment/purchase blocked
         "won't let me buy anything", "wont let me buy", "can't buy anything",
         "won't even let me buy packs", "wont even let me buy",
         "can't even buy packs", "cant even buy packs",
         # Wrong/invalid code errors
         "saying wrong code", "keeps saying wrong code", "says wrong code",
         # "Repeatedly crashes"
         "repeatedly crashes", "repeatedly crash", "causing the app to crash",
         # Balance/money not updating after deposit
         "money not showing up", "balance not updating", "funds not showing",
         "deposit isn't showing", "deposit not showing up", "money never showed up",
         # "Won't let me buy packs"
         "wont update my money",
         # App picks for you / auto-selects
         "automatically picks a card", "automatically selects",
         # Bank card / payment method not working
         "bank card not working", "bank card won't work", "bank card doesn't work",
         "can't use my bank card", "cant use my bank card",
         "debit card not working", "debit card not accepted",
         "card not accepted", "card was declined",
         "refuse to take my money", "refuses to take my money",
         "won't accept my payment", "wont accept my payment",
         # Stuck on loading / won't load past
         "doesn't load past", "won't load past", "wont load past",
         "stuck on loading screen", "stuck on load screen",
         "sits on loading", "loading screen won't go away",
         # Location restrictions
         "not available in my state", "can't use in my state",
         "doesn't work in my state", "blocked by location",
         "not available in my location", "my location isn't supported",
         # "Won't let me add money" — slightly different from "can't add money"
         "won't let me add money", "wont let me add money",
         "won't let me put money", "wont let me put money",
         # "Nothing loads" — total loading failure
         "nothing loads", "nothing will load", "nothing is loading",
         "none of it loads", "app won't load anything",
         # "Won't accept payment" (bare, without "my")
         "won't accept payment", "wont accept payment",
         "doesn't accept payment", "not accepting payment",
         # "Keeps asking me to enter X but no field to enter it" — form UI bug
         "keeps asking me to enter", "asking me to enter my last name",
         "asking to enter last name but", "no place to enter",
         # "Gliching" — misspelling of glitching
         "gliching", "glichs", "gliches",
         # "Quit in the middle of opening" — mid-pack crash
         "quit in the middle of", "quit mid-pull", "quit while opening",
         "crashed while opening a pack", "crashed mid-pull",
         # "It took my money and now can't make purchases"
         "took my money and now i can't", "took my money and now cant",
         "took my money but now", "charged me but",
         # "ID photo blacks out" — specific verification screen crash
         "trying to take a photo of my id it", "takes a photo of my id it blacks out",
         "photo of my id it just blacks out",
         # "Closes right when your card is being pulled" — mid-pull crash
         "closes right when", "closes right when your card",
         "close right when i'm opening", "closed mid pull",
         # "Counted but never gave me the pack" — charge without delivery
         "counted it but it never gave", "counted but never gave me",
         "charged but never gave me the pack", "charged me but never gave",
         # "Crashed but funds still removed" / charged after crash
         "crashed but funds still removed", "crashed but still charged",
         "crashed but still took my money", "crashed and took my money",
         "game crashed but still", "app crashed but still",
         "took my money before i could open", "froze and took my money",
         # "Takes my money and freezes" — double failure
         "keeps taking my money and it freezes", "takes my money and freezes",
         # "Allowed me to add money but won't give" — credit never applied
         "allowed me to add money but wont give", "allowed me to add money but won't give",
         # "So buggy" — bare adjective form
         "so buggy", "really buggy", "very buggy", "super buggy",
         # "Redirects me and says tap but no button" — payment flow UX bug
         "redirects me and says to tap the button below and there is no button",
         "tap the button below and there is no button",
         # "Breach or glitch in their system" — security/account compromise
         "breach or glitch in their system", "glitch in their system",
         "someone logged into my account", "someone has logged into my account",
         # "Asks for last name but never gives place to put it" — form bug
         "ask for my last name and never gives me a place",
         "never gives me a place to put it",
         # "Doesn't let me use my points" / rewards system bug
         "won't let me use my points", "won't let me use points",
         "doesn't let me use my points", "doesn't let me use points",
         "can't use my points", "cant use my points",
         "points don't work", "points aren't working",
         # "Refuses to let me sell/ship" — specific action blocked
         "refuses to let me sell", "refuses to let me ship",
         "won't let me sell my card", "wont let me sell my card",
         "won't let me ship my card", "wont let me ship my card",
         # "Doesn't load after downloading"
         "doesn't load after downloading", "won't load after downloading",
         "doesn't load after i downloaded", "just sits there",
         # "Can't delete my account"
         "won't let me delete my account", "wont let me delete my account",
         "can't delete my account", "cant delete my account",
         "unable to delete my account",
         # "Screen blacked out" — different tense from "blacks out"
         "screen blacked out", "screen just blacked out",
         # "Quite in the middle of" — typo of "quit"
         "quite in the middle of", "quite mid pull",
         # "Wouldn't let me put my last name" — form field bug
         "wouldn't let me put my last name", "wouldnt let me put my last name",
         "put my last name in anywhere", "put my last name anywhere",
         "no place to put my last name",
         # "It freezes" — bare form (safe due to star gate)
         "it freezes", "this app just freezes",
         # "Crashes on me everyday" / "crashes on me every time"
         "crashes on me every", "crashes on me all the time",
         "crashes every time i open",
         # "Crashes right before I pick my pack"
         "crashes on me right before", "crashes right before i pick",
         "crashed right before i opened",
         # "Screen goes blank" — different from "blank screen"
         "screen goes blank", "screen goes completely blank",
         # "Half flips from one color to another" — animation/UI bug
         "half flip", "half flips from", "half flips to another",
         # "Freeze right after you click"
         "freeze right after you click", "freezes right after i click",
         # "Lacks/locks out when you scroll" — scroll bug (typo included)
         "lacks out when you scroll", "locks out when you scroll",
         "locks up when i scroll", "locks up when you scroll",
         # "Make me restart every time I buy a pack"
         "make me restart every time i buy", "restart every time i buy",
         "restart every time i open",
         # "Steal $1" / small-amount charge bug
         "steal $1", "steal $2", "steal a dollar",
         "stealing a dollar", "stealing my dollar",
         # "Not letting me add money" — deposit blocked
         "not letting me add money", "not letting me put money",
         # "Can no longer deposit money"
         "can no longer deposit money", "can no longer add money",
         # "Haven't gotten my weekly pack"
         "haven't gotten my weekly pack", "havent gotten my weekly pack",
         "didn't get my weekly pack", "never got my weekly pack",
         "weekly pack never showed", "weekly pack restarted",
         # "Phone died and I didn't get the card"
         "phone died and", "my phone died",
         # "Claimed my rewards but doesn't give me"
         "claimed my rewards but", "claimed rewards but",
         "says i claimed my rewards",
         # "Doesn't give me my rewards"
         "doesn't give me my rewards", "doesnt give me my rewards",
         "not giving me my rewards",
         # "Stopped giving weekly packs"
         "stopped giving weekly packs", "stopped giving me weekly packs",
         # "Android user can't use"
         "android user to", "android users can't",
         "directs me to download on apple",
         # "This happened three times now" — repeat bug
         "this happened three times", "happened three times now",
         # "Keeps stopping" — app force-closes
         "keeps stopping", "app keeps stopping", "keeps on stopping",
         # "Frezes" — typo of "freezes"
         "frezes", "frezing", "frezzing",
         # "App not working" — bare form
         "app not working", "app just not working",
         # "Won't let me make a deposit"
         "won't let me make a deposit", "wont let me make a deposit",
         "can't make a deposit", "cant make a deposit",
         # "Screen goes dark" — darker phrasing than "goes black"
         "screen goes dark", "screen just goes dark",
         "app screen goes dark",
         # "App keeps dropping" — slang for crashes
         "app keeps dropping", "keeps dropping when i spin",
         "keeps dropping when",
         # "Won't let me see what I pull"
         "won't let me see what i pull", "wont let me see what i pull",
         # "Money never went into my account"
         "never went into my account", "money never went into my account",
         "never credited to my account",
         # "Wasn't credited to my account"
         "wasn't credited to my account", "wasnt credited to my account",
         "purchase wasn't credited", "not credited to my account",
         # "Will not let me load funds"
         "will not let me load funds", "wont let me load funds",
         "won't let me load funds",
         # "Doesn't accept Cash App"
         "doesn't accept cash app", "doesn't take cash app",
         "won't take cash app", "won't accept cash app",
         "not accepting cash app",
         # "Doesn't accept any payment"
         "doesn't accept any payment", "won't accept any payment",
         "not accepting any payments",
         # "Asking for my last name" (without "to enter" — shorter form)
         "asking for my last name", "keeps asking for my last name",
         "asking me for my last name",
         # "Trying to make a deposit for" [minutes/hours]
         "trying to make a deposit for", "been trying to make a deposit",
         # "Taken money from venmo and never applied"
         "took it out of my venmo account and never applied",
         "took money from venmo but never applied",
         # "Won't let me open packs" — combined deposit+purchase block
         "won't let me open packs", "wont let me open packs",
         "can't open any packs", "cant open any packs",
         # "Keeps dropping when I spin"
         "keeps dropping when i", "app keeps going down",
         # "Very glitchy" / "laggy interface"
         "very glitchy", "the app is very glitchy", "incredibly glitchy",
         "laggy interface", "the interface is laggy",
         # "Constantly have to close and reopen"
         "constantly have to close and reopen", "have to close and reopen",
         "always have to close and reopen", "force close and reopen",
         # "PayPal didn't go through"
         "paypal didn't go through", "paypal wont go through",
         "paypal won't go through", "paypal not going through",
         "paypal payment didn't go through", "paypal payment won't go through",
         # "Frooze" — typo of "froze"
         "frooze", "it frooze", "app frooze",
         # "Code for free pack but it's not showing up"
         "code for free pack but", "code for the free pack but",
         "entered the code but no free pack", "code didn't give me a free pack",
         # "Heats up my phone"
         "heats up my phone", "heats up phone", "makes my phone hot",
         "makes phone overheat", "phone overheats",
         # "Banking information didn't upload"
         "banking information didn't upload", "banking info didn't upload",
         "banking information wouldn't upload", "banking info wouldn't upload",
         # "Crashes often" / "crashes a lot" — lower-frequency crash variants
         "crashes often", "crashes a lot", "crash a lot",
         "crashes so often", "crashes too often",
         # "Wont load" — no apostrophe variant (common typo)
         "wont load", "wont even load", "wont open",
         "just wont load", "just wont open",
         # "Keeps shutting down" / "keeps shutting off"
         "keeps shutting down", "keeps shutting off",
         "shuts down on its own", "shuts off randomly",
         "keeps turning off",
         # "Freeze up" — compound verb form
         "freeze up", "freezes up", "froze up",
         "freezes right up", "it'll freeze up",
         # "Weekly pack did not work" — different from "never showed"
         "weekly pack did not work", "weekly pack doesn't work",
         "weekly pack wont work", "weekly pack won't work",
         "free weekly pack does not work",
         # "Can't redeem my weekly reward"
         "can't redeem my weekly", "cant redeem my weekly",
         "can't redeem my rewards", "cant redeem my rewards",
         "won't let me redeem", "wont let me redeem",
         "not letting me redeem",
         # "Won't let me earn rewards"
         "won't let me earn rewards", "wont let me earn rewards",
         "can't earn my rewards", "not letting me earn",
         # "Bad glitch" — noun-first form
         "bad glitch", "there's a bad glitch", "there is a bad glitch",
         "had a bad glitch", "massive glitch",
         # "Frozen and stolen my purchase"
         "frozen and stolen my purchase", "frozen and stolen my",
         "froze and stole my", "froze and stolen",
         # "Did not go through" — deposit/payment generic form
         "it did not go through", "it didn't go through",
         "money did not go through", "payment did not go through",
         # "Nothing to claim" — weekly pack UI shows nothing
         "nothing to claim", "nothing there to claim",
         "no reward to claim", "says free pack but nothing",
         # "App glitch" — noun form (different from "glitches" verb)
         "app glitch", "the app glitch", "a glitch in the app",
         "there's a glitch", "there is a glitch", "major glitch",
         # "Continuously crashed" — progressive continuous crash form
         "continuously crashed", "continuously crashing",
         "continuous crashing", "crashes continuously",
         # "Keeps bugging" — informal progressive form
         "keeps bugging", "keeps buggin", "always bugging out",
         "keeps bugging out",
         # "Charged for packs I don't receive" — charge-without-delivery
         "charged for packs that i don't receive", "charged for packs i never receive",
         "being charged for packs i don't receive",
         "charged for packs and never receive them",
         # "Lags when getting a pack" — lag during pack pull
         "lags when", "lags during", "lag when opening",
         "lags when opening", "lags when spinning",
         # "Can't even deposit" — stronger form than "can't deposit"
         "can't even deposit", "cant even deposit",
         "can not even deposit", "doesn't even let me deposit",
         # "Issues trying to deposit" — general deposit error form
         "issues trying to deposit", "so many issues trying to deposit",
         "having issues trying to deposit", "problem trying to deposit",
         # "Won't let me add to my balance" / "balance resets"
         "won't let me add to my balance", "wont let me add to my balance",
         "balance keeps resetting", "balance resets on its own",
         "resets my balance", "balance goes back to",
         # "Won't let me claim my weekly pack"
         "claim the weekly pack", "won't let me claim my weekly",
         "wont let me claim my weekly", "can't claim my weekly",
         "cant claim my weekly pack",
         # "Did not let me choose my pack" — forced random selection
         "did not let me choose my pack", "won't let me choose my pack",
         "wont let me choose my pack", "didn't let me choose my card",
         "didn't let me pick my pack",
         # "Doesn't run smoothly" — general smoothness complaint
         "doesn't run smoothly", "doesnt run smoothly",
         "doesn't run properly", "won't run smoothly",
         # "Constantly having problems claiming my free pack"
         "problems claiming my free pack", "having problems claiming",
         "trouble claiming my free pack",
         # "Cash App card declined" — specific payment decline
         "cash app card declined", "cash app card was declined",
         "decline my cash app card", "declined my cash app card",
         "cash app card not working", "cashapp card declined",
         # "Skip a few spins" — animation bug skipping cards
         "skip a few spins", "skips spins", "skipped a few spins",
         "skipping spins",
         # "Just crashes" — bare informal form
         "just crashes", "it just crashes", "just crashes on me",
         # "Cards don't load" / "cards wont load" — inventory loading bug
         "cards don't load", "cards wont load", "cards won't load",
         "cards aren't loading", "cards are not loading",
         # "Keeps blacking out" — blackout during pull
         "keeps blacking out", "blacking out on me", "game keeps blacking out",
         # "Flips back to the card before" — animation rollback bug
         "flip back to the card before", "flips back to the card before",
         "goes back to the card before",
         # "Can't deposit from / with Google Pay" — payment-method deposit block
         "can't deposit from google pay", "can't deposit with google pay",
         "cant deposit from google pay", "cant deposit with google pay",
         # "Click to open a pack but nothing happens"
         "click to open a pack but nothing happens",
         "tap to open a pack but nothing happens",
         "press open pack but nothing happens",
         # "Won't let me add any more funds" / "add funds" blocked
         "won't let me add any more funds", "wont let me add any more funds",
         "won't let me add funds", "wont let me add funds",
         "can't add funds", "cant add funds",
         # "Google Wallet not working" — payment method
         "google wallet not working", "google wallet doesn't work",
         "can't use google wallet", "cant use google wallet",
         # "Roll back to the card before" — animation rollback bug (different verb from "flip back")
         "roll back to the card before", "rolls back to the card before",
         "rolled back to the card before",
         # "Weekly reward packs state expired" — pack UI bug showing wrong state
         "packs state expired", "says expired when you should be able to claim",
         "weekly reward packs state", "shows as expired",
         "says expired but i should be able",
         # "Keeps exiting out" — force-closes before user can pick
         "keeps exiting out", "keeps exiting out of the app",
         "exits out before i can pick", "exits out right before",
         "exits out of the app before",
         # "Won't even let my buy" — typo of "me" as "my"
         "won't even let my buy", "wont even let my buy",
         # "Charged me without providing" — charge without delivering goods
         "charged me without providing", "charged me without giving me",
         "charged without providing",
         # "Scan a card won't work" — card scanning feature broken
         "scan a card won't work", "scan a card wont work",
         "won't let me scan my cards", "wont let me scan my cards",
         "won't scan my card", "wont scan my card",
         "card scanner doesn't work", "card scan won't work",
         # "A.i. chatbox" — period-separated abbreviation for AI chatbot
         "a.i. chatbox", "a.i chatbox", "ai chatbox",
         "chatbox that doesnt help", "chatbox doesn't help",
         "chatbox is useless", "chat box is useless",
         # "Google wallet" bare mention — payment method blocked
         "my google wallet", "use my google wallet", "in my google wallet",
         "through google wallet",
         # "Didn't credit my balance" — purchase not reflected
         "didn't credit my balance", "doesnt credit my balance",
         "doesn't credit my balance", "never credited my balance",
         "balance wasn't credited", "balance wasn't updated",
         # "Glitched and took my money" / "glitched lost" — app error consuming funds
         "glitched and took all my money", "glitched and took my money",
         "glitch took my money", "glitch took my coins",
         "glitched lost money", "glitched lost my money",
         "glitch and lost my money", "glitched and lost my money",
         # "Still showing $0" — deposit didn't credit to balance
         "still showing $0", "still showing 0.00", "showing $0.00",
         "still shows $0", "shows zero balance", "showing zero",
         "took my money and still shows", "took money and shows $0",
         # "Camera not working" / "camera is wonky" — camera bug
         "camera is wonky", "camera not performing",
         "camera not working on this app", "the camera won't work",
         "camera doesn't work", "camera issues",
         # "Screen went blank and balance was less" — screen-blank bug with money loss
         "screen went blank", "screen just went blank", "screen blanked out",
         "went blank and my balance", "went blank and then my balance",
         # "Couldn't verify my address" — address verification failure
         "couldn't verify my address", "can't verify my address",
         "wouldn't verify my address", "couldn't verify address",
         "address verification failed", "failed to verify my address",
         # "Freezes after I buy a pack" — freeze immediately after purchase
         "freezes after i buy", "freezes after buying", "freezes right after buying",
         "freezes after purchasing", "froze after i bought",
         "froze right after i bought", "freezes every time i buy",
         # "Said they added the money but never did" — credit promised but not applied
         "said they added the money but never", "added the money but never did",
         "said money was added but", "says money was added but wasn't",
         # "Goes black" — screen blackout bug (with typo variant)
         "goes black", "gos black", "screen goes black",
         "app goes black", "it goes black",
         # "Won't let me use venmo" — Venmo payment blocked
         "wont let me use venmo", "won't let me use venmo",
         "cant use venmo", "can't use venmo", "venmo doesn't work",
         "venmo wont work", "venmo won't work",
         # "Deposited but didn't get credited" — deposit not reflected in balance
         "deposited but didn't get credited", "deposit didn't get credited",
         "deposited money but didn't get credited", "deposited but no credit",
         "deposited but it never showed up", "deposited but balance didn't update",
         "deposit not showing", "deposit not showing up", "deposit not reflected",
         "took the money from my account but no credit",
         "took it out but didn't credit", "charged but didn't credit",
         "took money but didn't add to balance", "took money but balance didn't change",
         "took from my bank but not in app", "charged my bank but not credited",
         "charged my card but", "charged 6 times", "charged multiple times",
         "double charged", "triple charged", "charged twice",
     ]},

    # ── Login / Account Issues ────────────────────────────────────────────────
    {"id": "login", "name": "Login / Account Issues",
     "keywords": [
         "can't log in", "can't login", "cannot log in", "cannot login",
         "won't let me log in", "won't let me login",
         "locked out", "locked out of my account", "account locked", "account suspended",
         "account banned", "got banned",
         "lost my account", "can't access my account", "can't access my profile",
         "account disappeared", "account was deleted", "account got deleted",
         "password reset", "forgot password", "password won't reset",
         "keeps logging me out", "logs me out", "keeps kicking me out",
         "kicked me out", "logs out automatically",
         "lost my cards", "lost my items", "items are gone", "cards disappeared",
         "collection disappeared", "inventory disappeared", "missing from my account",
         # "Signed me out on its own" — involuntary logout
         "signed me out on its own", "signed me out automatically",
         "logs me out on its own", "kicked me out on its own",
         # "Won't let me back in" — post-logout lockout
         "won't let me back in", "wont let me back in", "can't get back in",
         "cant get back in", "can no longer log in",
         # "Code doesn't match" / "code is incorrect" — verification failures
         "code doesn't match", "code is incorrect", "code was incorrect",
         "says the code is incorrect", "code doesn't work",
         "saying invalid code", "says it's an invalid code",
         # "Wont let me log back to my account" — post-logout re-login failure
         "wont let me log back", "won't let me log back",
         "can't log back in", "cant log back in",
         "unable to log back in", "unable to log back into",
         # "Login portion sucks" / "wouldn't sign me in"
         "login portion sucks", "login part doesn't work",
         "wouldn't sign me in", "wouldnt sign me in",
         "won't sign me in", "wont sign me in",
         # "Can't make a new account" (blocked by phone number/email already used)
         "won't let me make a new account", "wont let me make a new account",
         "can't make a new account", "cant make a new account",
         "can't create a new account", "cant create a new account",
         "won't let me create an account", "wont let me create an account",
         # "Won't let me make a new one" — "one" instead of "account"
         "won't let me make a new one", "wont let me make a new one",
         # "Banned for no reason"
         "banned me for no reason", "banned for no reason",
         "account was banned for no reason", "banned my account with no reason",
         # "Please unban me" — account banned
         "please unban me", "unban my account", "unban me",
         # "Never sent me my code" — OTP/verification never arrives
         "never sent me my code", "never send me my code",
         "wouldn't send me a code", "thing would never send me my code",
         # "It not let me login" — informal grammar
         "it not let me login", "it not let me log in",
         # "Phone number cannot be reused"
         "phone number could not be reused", "phone number can't be reused",
         "can't reuse my phone number", "same number can't be used again",
         # "Tells me it's the wrong code" — OTP verification failure
         "tells me it's the wrong code", "tells me its the wrong code",
         "told me it was the wrong code", "says it's the wrong code",
         # "Keeps saying try again" — login retry loop
         "keeps saying try again", "it keeps saying try again",
         "keeps telling me to try again",
         # "Won't let you add your account back" — deleted account lockout
         "won't let you add your account back", "wont let you add your account back",
         "won't let me add my account back", "won't add my account back",
         "can't add my account back", "cant add my account back",
         # "Was never able to make a new one" — after deleting, can't re-register
         "was never able to make a new one", "was never able to make a new account",
         "never able to make a new account", "never able to create a new account",
         "couldn't make a new account", "couldnt make a new account",
         "wasn't able to make a new account", "wasnt able to make a new account",
         # "Keeps telling me i have the wrong code"
         "keeps telling me i have the wrong code",
         "keeps telling me the code is wrong",
         "telling me i have the wrong code",
         # "Tells me its invalid" — invalid code message
         "tells me its invalid", "tells me it's invalid",
         "it tells me it's invalid", "it tells me its invalid",
         "says it's invalid", "says its invalid",
         # "Need my account back" — account recovery request
         "need my account back", "want my account back",
         "i need my exact account back", "need to get my account back",
         "get my account back", "want my old account back",
         # "Wouldn't send me a verification code" — SMS not received
         "wouldn't send me a verification code",
         "wouldn't send a verification code",
         "wouldn't send an sms code",
         "didn't send me a verification code",
         "not text the code to my number",
         "proceeded to not text the code",
         "won't text me a code",
         # "Every code is wrong" — all codes rejected
         "every code is wrong", "every code it gives is wrong",
         "all codes are wrong", "codes are always wrong",
         "keep saying code is wrong",
         # "Phone number code never working" — SMS OTP failures
         "phone number code never", "code to my phone never",
         "sms code never", "text code never",
         # "Unable to change phone number linked to account"
         "unable to change the phone number", "can't change my phone number",
         "cant change my phone number", "can't change phone number on my account",
         "unable to change phone number",
         # "Tells me they are invalid" — plural invalid codes
         "tells me they are invalid", "tells me its invalid",
         "tells me wrong code", "tells me it's the wrong code",
         "tells me the wrong code",
         # "Says incorrect code" — bare phrasing (no "is")
         "says incorrect code", "said incorrect code",
         "always says incorrect code",
         # "It would say incorrect" — past conditional form
         "it would say incorrect", "would say the code is incorrect",
         "kept saying incorrect",
         # "Says its wrong" — no-apostrophe informal form
         "says its wrong", "it says its wrong",
         "just says its wrong",
         # "Claims I have the wrong code" — accusation-framed OTP failure
         "claims i have the wrong code", "claim i have the wrong code",
         "claims the code is wrong",
         # "Longin" — extremely common mobile typo of "login"
         "wont let me longin", "won't let me longin",
         "wouldn't let me longin", "cant longin",
         "it wouldn't let me longin",
         # "Code I received is incorrect" — OTP received but rejected by system
         "code i received is incorrect", "code received is incorrect",
         "says the code i received is incorrect",
         "code they sent is incorrect", "code they sent is wrong",
     ]},

    # ── Shipping Problems ─────────────────────────────────────────────────────
    {"id": "shipping-bad", "name": "Shipping Problems",
     "keywords": [
         "shipping too expensive", "shipping is too expensive", "shipping costs too much",
         "shipping cost is high", "expensive shipping", "high shipping", "overpriced shipping",
         "shipping fee is ridiculous", "charge too much for shipping",
         "shipping is outrageous", "shipping price is insane",
         "can't combine shipping", "won't combine shipping", "no combined shipping",
         "slow shipping", "shipping takes forever", "takes weeks to ship",
         "hasn't shipped yet", "hasn't been shipped", "still hasn't arrived",
         "package lost", "lost in the mail", "lost my package",
         "never arrived", "never received", "never got my order", "still waiting",
         "damaged in shipping", "arrived damaged", "package damaged", "card damaged",
         "poorly packaged", "bad packaging", "not packaged well",
         "tracking never updated", "tracking hasn't moved", "tracking stopped",
         "tracking not working",
         # "Shipping is ridiculous" — standalone adjective form
         "shipping is ridiculous", "shipping costs are ridiculous",
         "shipping costs are crazy", "shipping cost is crazy",
         "shipping is outrageous", "shipping is absurd",
         "ridiculous shipping", "absurd shipping",
         # "Shipping took forever" — time-based complaint
         "shipping took forever", "shipping took way too long",
         "took forever to ship", "took weeks to get here",
         "took 9 days", "took 2 weeks", "been 2 weeks",
         # "Shipping time is ridiculous"
         "shipping time is ridiculous", "shipping time is terrible",
         "shipping time is awful", "shipping times are ridiculous",
         # "You win, you pay for shipping" — reward is offset by fee
         "you win, you pay for shipping", "win but pay for shipping",
         "win and then pay for shipping",
         # "Pay to open a pack and then pay shipping"
         "pay to open a pack and then you pay",
         "pay to rip and then pay for shipping",
         # "Card has not shipped" — item awaiting fulfillment
         "card has not shipped", "card still hasn't shipped",
         "card hasn't shipped", "cards have not shipped",
         "watch has not shipped", "watch hasn't shipped",
         # "Never posted" — never entered shipping pipeline
         "never posted", "it was never posted", "was never posted",
         # "Couldn't ship the card I wanted"
         "couldn't ship the card", "couldn't ship so lost",
         "couldn't ship my card",
         # "Not received my card in the mail"
         "not received my card in the mail", "have not received my card",
         "haven't received my card", "havent received my card",
         "not received my order in the mail",
         # "Costs more to ship than the pack is worth"
         "costs more to ship than", "shipping costs more than the",
         "more in shipping than the card", "shipping costs more than the card",
         # "Won't ship your cards" — refusal to ship
         "won't ship your cards", "wont ship your cards",
         "refuses to ship", "refused to ship",
         "they won't ship", "they wont ship",
         # "Separate shipping per card"
         "separate shipping per card", "separate shipping for each card",
         "shipping for each individual card", "ship each card separately",
         # "Charge shipping for each card"
         "charge shipping for each card", "charging for shipping on each",
         "paying for shipping on each", "shipping on each individual card",
         "charge per card to ship", "$6 per card to ship", "per card shipping",
         # "Forced to pay shipping"
         "forced to pay shipping", "force you to pay shipping",
         # "Item never delivered" — already have "never arrived" but not this
         "item never delivered", "watch never delivered",
         "package was never delivered", "order was never delivered",
         # "Beaten up" / damaged slabs
         "beat up slabs", "slabs were beat up", "slabs arrived beat up",
         # "Can't ship more than 1 at a time" / no group shipping
         "cant ship more than 1 at a time", "can't ship more than 1 at a time",
         "cant ship more than one at a time", "can't ship more than one at a time",
         "no group shipping", "no bundle shipping", "no combined shipping option",
         "ship each card separately for", "ship one at a time",
         "only ship one card at a time", "will only ship one card at a time",
         # "Scratches on slabs" / damaged item received
         "scratches on the slab", "slab has scratches", "slabs have scratches",
         "hairline crack on the slab", "slab has a crack", "cracked slab",
         # "Never got the cards shipped to me" — extra time variant
         "never got the cards shipped to me", "cards were never shipped",
         "hasn't been shipped after", "after 3 months", "been 3 months",
         # "8 dollars to ship" / "$10 to ship" — specific high shipping fee amounts
         "8 dollars to ship", "$8 to ship", "8 bucks to ship",
         "10 dollars to ship", "$10 to ship", "10 bucks to ship",
         "13 dollars for delivery", "$13 for delivery", "13 dollars for shipping",
         "$13 to ship", "13 dollars to ship",
         # "No ability to ship cards together" — consolidation not available
         "no ability to ship cards together", "no ability to ship together",
         "can't ship cards together", "cant ship cards together",
         "no way to ship cards together",
         # "Arrived broken" / "came broken"
         "arrived broken", "came broken", "arrived cracked",
         "came cracked", "shipped broken", "got it broken",
         # "Too hard to ship" / confusing shipping process
         "too hard to ship", "so hard to ship", "complicated to ship",
         "shipping process is difficult", "shipping process is confusing",
         # "Ship each card individually" — no-consolidation phrasing
         "ship each card individually", "ship them individually",
         "ship individually", "ship every card individually",
         "shipping individually", "shipped individually",
         # "Says delivered but I never got it" — misdelivery complaint
         "says delivered but i never got it", "says delivered but never got it",
         "says it was delivered but i never got", "says delivered but never received",
         "said the card was delivered but i never got",
         # "Don't see how to ship multiple" — UX: no group shipping option
         "don't see how to ship multiple", "dont see how to ship multiple",
         "no way to ship multiple cards", "no option to ship multiple",
         # "Bulk shipping" not available
         "bulk shipping", "no bulk shipping", "bulk shipping is not an option",
         "bulk shipping not available",
         # "$5 to ship every card" / "$5 per card" — per-card fee variants
         "$5 to ship every", "$5 to ship each", "$5 per card to ship",
         "$5 per card for shipping", "$5 for each card to ship",
         # "5 dollars per card to ship" — written-out dollar amount variant
         "5 dollars per card to ship", "5 dollars per card for shipping",
         "4 dollars per card", "6 dollars per card", "7 dollars per card",
         "dollars per card to ship", "dollars a card to ship",
         # "9 days to arrive" — specific delivery time complaint
         "9 days to arrive", "took 9 days to arrive", "9 days to get here",
         "8 days to arrive", "10 days to arrive", "11 days to arrive",
         "took over a week to arrive", "over a week to arrive",
         # "Shipping is a joke" — dismissive shipping complaint
         "shipping is a joke", "shipping is a ripoff", "shipping is a rip off",
         "shipping is robbery",
         # "Shipping exceeded card worth" — shipping cost exceeded card value
         "shipping exceeded", "shipping exceeds", "shipping exceeded card",
         "shipping basically exceeded", "shipping more than the card",
         "shipping cost more than the card", "shipping costs more than",
         "ship fee exceeded", "ship cost exceeded",
         # "Cost too much to get your cards out" — general retrieval cost
         "cost too much to get your cards out", "cost too much to get my cards",
         "costs too much to get your cards out",
         # "Still hasn't been sent" / "card hasn't been sent"
         "still hasn't been sent", "still hasnt been sent",
         "card hasn't been sent", "card hasnt been sent",
         "cards haven't been sent", "cards havent been sent",
         "hasn't been sent yet", "hasnt been sent yet",
         # "$12 to ship" — specific high shipping fee
         "$12 to ship", "pay $12 to ship",
         "12 dollars to ship", "12 bucks to ship",
         "$12 for shipping", "12 dollars for shipping",
         # "$5.50 to ship" — specific per-card fee
         "$5.50 to ship", "5.50 to ship", "5.50 for shipping",
         "$5.50 for shipping", "five fifty to ship",
         # "9 dollars to ship" / "$9 to deliver" — per-card delivery fee
         "9 dollars to ship", "$9 to ship", "9 dollars to deliver",
         "$9 to deliver", "9 dollars for delivery", "$9 for delivery",
         "charge like 9 dollars", "like 9 dollars to deliver",
         "like $9 to deliver", "like 9 bucks to ship",
         # "Lost in transit" — shipping lost during delivery
         "lost in transit", "lost in the transit",
         "missing in transit",
         # "6 dollars for shipping" / "$6.99 shipping" — specific per-card shipping fees
         "6 dollars for shipping", "$6 for shipping",
         "6 dollars to ship", "$6 to ship",
         "charging 6 dollars for shipping", "6 bucks for shipping",
         "charging 6 dollars", "6 dollars to deliver",
         "$6.99 for shipping", "6.99 for shipping", "$6.99 per card",
         "6.99 to ship", "$6.99 to ship", "charged $6.99",
         # "Slabs mailed" / "slaps mailed" — slabs misspelling + shipping context
         "slabs mailed", "slaps mailed",
         "cant get your slabs mailed", "can't get your slabs mailed",
         "get your slabs mailed",
     ]},

    # ── Customer Service (Bad) ────────────────────────────────────────────────
    {"id": "cs-bad", "name": "Customer Service Issues",
     "keywords": [
         "no response", "no reply", "never responded", "never replied", "zero response",
         "support ignored", "support never responded", "support never replied",
         "waited weeks", "waiting weeks", "waiting months", "waited days for",
         "no one helped", "no one responded", "nobody helped",
         "support is terrible", "support is awful", "support is horrible",
         "customer service is awful", "customer service is terrible", "customer service sucks",
         "worst customer service", "horrible customer service",
         "couldn't get help", "can't get help", "couldn't reach anyone", "can't reach anyone",
         "unresponsive", "completely unresponsive",
         "ghosted me", "they ghost you", "they ghosted me",
         "don't respond", "they don't respond", "won't respond",
         "terrible support", "awful support", "useless support",
         "automated response", "only bots", "just a bot", "talk to a real person",
         "reached out multiple times", "emailed multiple times", "tried multiple times",
         # "Can't get a hold of" — phone/general contact failure
         "can't get a hold of anyone", "cant get a hold of anyone",
         "can't get hold of support", "can't reach support",
         "can't contact customer support", "cant contact customer support",
         # "Customer support takes days" — SLA complaint
         "customer support takes days", "support takes days to respond",
         "days to respond", "takes days to get back",
         "customer service doesn't respond", "customer service dont respond",
         "hasn't reached out", "they haven't responded", "still no response",
         # "No customer support at all"
         "no customer support at all", "no customer support",
         # "AI chatbot is useless" — common frustration with bot-only CS
         "ai chat bot is useless", "chatbot is useless", "bot is useless",
         "only an ai", "just an ai", "ai responds", "chat bot doesn't help",
         # "Customer support is basically nonexistent"
         "basically none existent", "basically nonexistent",
         "customer support is nonexistent", "support is nonexistent",
         "support is practically nonexistent", "barely any support",
         # "Doesn't respond to emails"
         "doesn't respond to emails", "dont respond to emails",
         "never responds to emails", "not responding to emails",
         # "Takes a week to respond"
         "responds within a week", "respond within a week", "take a week to respond",
         "week to get a response", "waited a week for a response",
         # "Won't answer"
         "won't answer customer support", "wont answer customer support",
         "no answer from support", "never answers support",
         # "The line has gone cold" / "nobody responds anymore"
         "line has gone cold", "gone cold on me",
         "nobody responds anymore", "no one responds anymore",
         "stopped responding", "they stopped responding",
         # "Getting verified is impossible"
         "getting verified is impossible",
         "reaching customer support is impossible", "impossible to reach support",
         "impossible to get help",
         # "Support is non existent" (with space between "non" and "existent")
         "support is non existent", "service is non existent",
         "non existent customer support", "non existent support",
         # "Support be abysmal" / "support is abysmal"
         "support be abysmal", "support is abysmal", "support was abysmal",
         "customer service is abysmal",
         # "Can't reach customer support" (exact phrase)
         "can't reach customer support", "cant reach customer support",
         "couldn't reach customer support",
         # "Just has AI support" / "only AI support"
         "just has ai support", "only has ai support",
         "all ai support", "just ai chatbot",
         # "Unprofessional" — customer-facing staff complaint
         "unprofessional", "very unprofessional", "totally unprofessional",
         # "CS sucks" — abbreviation
         "cs sucks", "their cs sucks", "cs is terrible",
         # "Support is a joke" — dismissive complaint
         "support is a joke", "customer support is a joke",
         "their support is a joke",
         # "They don't even reply"
         "they don't even reply", "dont even reply",
         "they don't reply at all", "won't even reply",
         # "Spent all day trying to get a hold"
         "spent all day trying to get a hold",
         "spent all day trying to reach",
         "tried all day to reach support",
         # "Support is ignoring me" — ghosted specifically
         "support is ignoring me", "customer support is ignoring me",
         "they're ignoring me", "they are ignoring me",
         "ignoring my messages", "ignoring my emails",
         # "No option to make a complaint"
         "no option to make a complaint", "no way to make a complaint",
         "no option to contact", "no way to contact",
         "no apparent way to contact customer service",
         "no apparent way to contact",
         # "It's all automated" — bot-only support
         "it's all automated", "its all automated",
         "it's completely automated", "all automated responses",
         # "No way to contact customer service"
         "no way to contact customer service",
         "can't contact anyone", "cant contact anyone",
         # "Customer support team doesn't answer" — phone/email unresponsive
         "support team doesn't answer", "support doesn't answer",
         "customer support doesn't answer", "customer service doesn't answer",
         "team doesn't answer",
         # "Poor support" — brief dismissive form
         "poor support", "poor customer support", "poor customer service",
         "really poor support", "very poor support",
         # "Sent my refund to a card I don't own" — wrong refund destination
         "sent my refund to a card i don't own",
         "refunded to a card i don't own",
         "sent the refund to the wrong card",
         "refund went to wrong card", "refunded to wrong card",
         # "Contacted them 4 times" / "sent X messages"
         "sent 4 messages", "sent 3 messages", "sent 5 messages",
         "messaged them 4 times", "messaged them 3 times",
         "sent multiple emails and no response",
         # "Says delivered but I never received it" → misdelivery + CS failure
         "says delivered but i never", "says it was delivered but i never",
         "says delivered but never received",
         # "Support is all AI" — automated-only CS complaint
         "support is all ai", "it is all ai", "it's all ai",
         "support is completely ai", "all ai customer support",
         "support group doesn't offer any reason", "support group doesn't",
         "ai chatbot only", "only ai chatbots",
         # "AI chat bot that doesn't help" — bot-only support that can't resolve issues
         "ai chat bot that doesn't help", "chatbot that doesn't help",
         "ai bot that doesn't help", "bot doesn't help", "chatbot can't help",
         "support is an ai bot", "support is a chatbot", "just a chatbot",
         "their support is just an ai", "their customer support is an ai",
         "only a bot responds", "only bots respond",
         "the support bot", "the chatbot doesn't", "the ai bot doesn't",
         # "I couldn't get in touch with a real person"
         "couldn't get in touch with a real person", "cant talk to a real person",
         "can't talk to a real person", "can't get a real person",
         "cant get a real person", "need to talk to a real person",
         "need a real person", "want to talk to a real person",
         "no real person to talk to", "no human support",
     ]},

    # ── Cashout / Withdrawal Problems ─────────────────────────────────────────
    {"id": "cashout-bad", "name": "Cashout / Withdrawal Problems",
     "keywords": [
         "can't withdraw", "cannot withdraw", "won't let me withdraw",
         "can't cash out", "cannot cash out", "won't let me cash out",
         "can't access my money", "can't get my money out", "can't get my funds",
         "withdrawal pending", "withdrawal stuck", "withdrawal failing", "withdrawal failed",
         "balance disappeared", "balance is gone", "balance is missing", "balance reset",
         "money disappeared", "money is missing", "money vanished",
         "funds disappeared", "funds missing", "funds are gone",
         "won't pay out", "won't release", "holding my money", "holding my funds",
         "they're holding my money", "keeping my money",
         "cashout denied", "cashout failed", "cashout is broken", "cashout doesn't work",
         "cashout never arrived", "payout never arrived", "never received payment",
         "balance is 0", "balance shows zero", "account balance gone",
         # Missing withdrawal patterns
         "impossible to get your money back", "impossible to get money back",
         "can't get my money back", "cannot get my money back",
         "take forever to pay out", "takes forever to pay out",
         "slow to pay out", "forever to pay out",
         "can't confirm withdrawal", "can not confirm withdrawal", "confirm button",
         "won't let me transfer", "can't transfer funds", "unable to withdraw",
         "withdrawal not working", "withdrawals don't work", "withdrawals broken",
         "withdrawal is broken", "can't access funds",
         "not let me take out", "won't let me take out my money",
         "my money went", "where did my money go", "where is my money",
         "why would it let me spend", "let me spend but not",
         # "Doesn't let you cash out" — third-person / different form
         "doesn't let you cash out", "doesn't let me cash out",
         "won't let me take my money out", "can't take my money out",
         "easy to put money in but", "easy to take money in but",
         # "Hard to withdraw" / access issues
         "hard to withdraw", "hard to get my money", "hard to get money out",
         "rejected my kyc", "kyc rejected", "verification rejected",
         "won't verify me", "cant verify", "can't verify me",
         # "Slow to pay out" / delays
         "takes too long to pay", "takes forever to pay",
         "pay out takes", "payout takes forever",
         "days to pay out", "weeks to pay out",
         # No-apostrophe variants (common typos)
         "cant withdraw", "wont let me withdraw", "cant cash out", "wont cash out",
         "wont let me cashout", "cant get my money out",
         # Verification / ID required to withdraw
         "account still isn't verified", "account not yet verified",
         "haven't been able to verify my id", "havent been able to verify",
         "they want a copy of your passport", "want a copy of my passport",
         "want a copy of your id", "asking for my ssn", "asked for your ssn",
         "asking for ssn", "asking for my social security",
         # Not letting me withdraw variants
         "not letting me withdraw", "not letting me cash out",
         # "Won't accept my ID to cash out" — verification refusal
         "won't accept my id", "wont accept my id", "not accept my id",
         "id not accepted", "id verification failed", "id was rejected",
         "require photo id to cash out", "require id for cashing out",
         "require identification to cash out", "need id to cash out",
         "required a photo id", "required photo id",
         "refused to send money without", "won't send money without",
         # "Wouldn't let me withdrawal" — common misspelling of withdraw
         "wouldn't let me withdrawl", "wouldnt let me withdrawl",
         "wont let me withdrawl", "won't let me withdrawl",
         "cant withdrawl", "can't withdrawl",
         # "Insufficient funds" error when cashing out
         "insufficient funds", "says insufficient funds",
         # "Funds stuck / trapped" waiting for verification
         "trap it by making you pass", "money trapped by",
         "id is taking so long to verify", "id taking so long to verify",
         # "3 days to get your funds" — slow payout complaint
         "3 days to get your funds", "takes 3 days to get your funds",
         "days to get your funds", "days to receive your funds",
         "3 days to get your money", "days to get your money out",
         # "Can't KYC" / KYC blocking withdrawal
         "cant kyc", "can't kyc", "you cant kyc", "kyc doesn't work",
         "kyc not working", "kyc process not working",
         "kyc never goes through", "kyc keeps failing",
         # "Money came out of my account but didn't receive funds"
         "money came out of my account but",
         "came out of my account but have not received",
         "came out of my account but i have not",
         # "Verification process not working"
         "verification process not working", "verification process doesn't work",
         "verification process is broken", "verification process failed",
         # "Getting verified is impossible"
         "getting verified is impossible", "verification is impossible",
         "verification close to impossible", "makes verification impossible",
         "can't get verified", "cant get verified",
         "unable to get verified", "impossible to verify",
         # "Wont verify my id to cash out"
         "wont verify my id to cash out", "won't verify my id to cash out",
         "wont verify my id", "won't verify my id",
         # "Taking too long to verify my identity"
         "taking too long to verify my identity", "takes too long to verify my identity",
         "taking forever to verify my identity",
         # "Not able to cash out without doing 20 steps" / excessive steps
         "not able to cash out without", "unable to cash out without",
         "20 steps to cash out", "so many steps to cash out",
         # "Easy to put money in, impossible to take it out" — exact phrase from sample
         "easy to put money in. impossible to take it out",
         "easy to put money in but impossible to take it out",
         "impossible to take it out", "impossible to take my money out",
         # "Wait a million years for verification"
         "wait a million years", "million years for verification",
         # "Wont give me a card or my money after deposit"
         "wont give me the card or my money", "won't give me the card or my money",
         # "Easy to put money in but hard to take out" — asymmetric UX
         "ok with me depositing but withdrawing", "ok with deposits but not withdrawals",
         "deposits fine but withdrawals", "easy to deposit but hard to withdraw",
         # "This game is rig" — misspelling of "rigged"
         "this game is rig", "the game is rig",
         # "Funds/assets stuck because i cannot complete verification"
         "funds stuck because", "assets stuck because",
         "money stuck because", "stuck because i cannot verify",
         # "ID verification takes forever" / approval delays
         "id verification takes", "id verification is taking",
         "id is taking too long", "id taking too long to verify",
         # "Pending verification for [weeks]" — different word order
         "pending verification for", "pending verification",
         "says verification pending", "still says verification pending",
         # "Don't know where my money went"
         "don't know where my money went", "dont know where my money went",
         "i don't know where my money went",
         # "Hassle to do withdrawal"
         "hassle to do withdrawal", "hassle to withdraw",
         "such a hassle to cash out", "such a hassle to withdraw",
         # "Won't take pic of my ID" — camera/upload verification bug
         "wont take pic of my id", "won't take pic of my id",
         "wont take a pic of my id", "won't take a pic of my id",
         "take pic of my id", "take a pic of my id",
         # "Wouldn't scan my ID"
         "wouldn't scan my id", "wouldnt scan my id",
         "wont scan my id", "won't scan my id",
         "won't take a picture of my id", "wont take a picture of my id",
         # "Did not cashout to paypal" — specific platform failure
         "did not cashout to paypal", "didn't cashout to paypal",
         "won't cashout to paypal", "not cash out to paypal",
         "won't transfer to paypal", "wont transfer to paypal",
         # "Won't add the money" — credit never applied
         "won't add the money", "wont add the money",
         "won't add my money", "wont add my money",
         # "Withdrawal with debit card" — blocked method
         "withdrawal with a debit card", "withdraw with a debit card",
         "withdraw using debit card", "withdrawal using my debit",
         # "KYC policy is unfair"
         "kyc policy is unfair", "kyc is unfair",
         "kyc requirements are unfair",
         # "Won't let me have my money back"
         "won't let me have my money back", "wont let me have my money back",
         "won't give me my money back", "wont give me my money back",
         # "Software can't read my ID"
         "software can't read my id", "software can't read ids",
         "software won't read my id", "can't read my id",
         # "Pending verification" — bare form to catch short reviews
         "pending for verification", "stuck in verification",
         # "Won't let me have money back" — short form
         "won't let me take my money", "wont let me take my money",
         # "Can't get my id to verify" / "id won't verify" — verification failure
         "can't get my id to verify", "cant get my id to verify",
         "id won't verify", "id wont verify",
         "id failed to verify", "id is not verifying",
         "id says success but wont work",
         # "Have to provide ID to get card shipped" — ID gate on shipping
         "have to provide id to get your card shipped",
         "have to provide id to ship",
         "require id to ship my card",
         "supposed to provide id to get card shipped",
         "trash how you're supposed to provide id",
         # "Withdrawal is a different story" — asymmetric UX complaint
         "withdrawal is a different story", "withdrawing is a different story",
         "different story when you try to withdraw", "different story when withdrawing",
         "different story when trying to cash out",
         # "ID scan won't work" / "face scan won't work" — biometric verification failure
         "id scan wont work", "id scan won't work",
         "face scan wont work", "face scan won't work",
         "face scan doesn't work", "id scan doesn't work",
         "id scan failed", "face scan failed",
         # "Identity verification will block you" — actively blocking
         "identity verification will block you", "identity verification blocks you",
         "identity verification blocking", "blocked by identity verification",
         # "Still haven't received my money" — long-delayed payout
         "still haven't received my money", "still havent received my money",
         "haven't received my money yet", "havent received my money yet",
         # "Asking SSN for withdrawal" — SSN request at withdrawal
         "asking ssn for withdrawal", "asked for ssn for withdrawal",
         "asking my ssn to withdraw", "asking ssn to cash out",
         # "Too much personal information" required
         "too much personal information", "way too much personal information",
         "requiring too much personal info", "asking for too much personal info",
         # "Can't even get verification done" — verification completely blocked
         "can't even get verification done", "cant even get verification done",
         "can't even complete verification", "cant even complete verification",
         "can't complete the verification", "cant complete the verification",
         # "Verification never finished" — process incomplete after waiting
         "verification never finished", "verification didn't complete",
         "verification never completed", "verification didn't finish",
         "shipping verification never finished", "verification never ended",
         # "Verified identity but still won't let me" — KYC done but still blocked
         "verified my identity but won't", "verified identity but won't",
         "verified my identity but still won't let me", "verified but still won't let me",
         "verified my id but still can't", "verified identity but still can't",
         # "Says on hold" — withdrawal put on hold with no explanation
         "says on hold", "just says on hold", "withdrawal says on hold",
         "says my withdrawal is on hold", "says it's on hold",
         # "Cost to cash out is ridiculous" — high fee to withdraw
         "cost to cash out is ridiculous", "costs too much to cash out",
         "fee to cash out is ridiculous", "cashout fee is ridiculous",
         "it costs too much to withdraw",
         # "Cant even withdrawal" — typo of "withdraw" (noun used as verb)
         "cant even withdrawal", "can't even withdrawal",
         "wont even let me withdrawal", "won't even let me withdrawal",
         # "Verification is a pain" — frustration with verification requirement
         "verification is a pain", "verification is such a pain",
         "verification is a huge pain", "verification process is a pain",
         # "Still verifying my identification" — long wait for ID verification
         "still verifying my identification", "still verifying my id",
         "still haven't verified my id", "still verifying identity",
         "still being verified",
         # "Takes for ever" — space-separated "forever" typo
         "takes for ever", "taking for ever",
         "take for ever to verify", "took for ever",
         # "Need to verify identity faster" — slow verification complaint
         "need to veify identity", "need to verify identity faster",
         "verify identity faster", "verify faster",
         # "Stuck at the code verification" — verification step that won't advance
         "stuck at the code verification", "stuck at verification",
         "stuck on verification", "stuck at the verification",
         "stuck on the verification step", "stuck on the identity verification",
         # "Weeks to verify your identity" — very long KYC wait
         "weeks to verify your identity", "weeks to verify my identity",
         "took weeks to verify", "takes weeks to verify",
         "week to verify my identity", "been waiting weeks to be verified",
         # "Did not receive my withdrawal" — payout simply never arrived
         "did not receive my withdrawal", "never received my withdrawal",
         "have not received my withdrawal", "havent received my withdrawal",
         "didn't receive my withdrawal",
         # "App keeps your money" — platform accused of holding funds
         "app keeps your money", "app keeps my money",
         "keeps your money", "keeping my money",
         "they keep my money", "company keeps your money",
         # "Unresolved withdrawal" — payout issue not fixed
         "unresolved withdrawal", "unresolved $",
         "unresolved cashout", "unresolved payout",
         "still unresolved", "issue is unresolved",
         # "Photo of my ID" to ship — identity gate at shipping step
         "photo of my id", "photo of my drivers license",
         "required a photo of my id", "asks for photo of id",
         "id photo to ship",
         # "Been verifying for hours" — cashout verification stuck for hours
         "verifying for 8 hours", "verifying for hours",
         "been verifying for", "verifying me for my payout",
     ]},

    # ── Addiction / Gambling Harm ─────────────────────────────────────────────
    {"id": "addiction", "name": "Addiction / Gambling Harm",
     "keywords": [
         # Life impact — person or someone they know
         "ruined my life", "ruined his life", "ruined her life",
         "ruined my relationship", "ruined our relationship", "ruined my marriage",
         "ruined my finances", "ruined me financially", "financially ruined",
         "destroyed my finances", "destroyed my life", "destroyed my savings",
         "affected my family", "hurt my family", "affecting my family",
         "cost me everything", "cost him everything", "cost her everything",
         # Financial ruin
         "lost everything", "lost it all", "lost all my money",
         "lost all his money", "lost all her money",
         "spent rent money", "used rent money", "spent my rent",
         "spent my savings", "drained my savings", "emptied my savings",
         "spent my last", "spent everything i had", "spent everything i have",
         "maxed out my credit card", "maxed out my card", "maxed my credit",
         "in debt because of", "went into debt", "going into debt", "debt because of this",
         "financial ruin", "in financial ruin", "financial disaster",
         "can't stop spending", "can't stop buying", "can't stop myself",
         # Explicit gambling / addiction language
         "gambling addiction", "gambling problem", "problem gambling",
         "this is gambling", "it's gambling", "this is literally gambling",
         "predatory app", "preys on", "exploits addiction", "designed to addict",
         "manipulates you into spending", "tricks you into spending",
         # High-loss amounts
         "spent thousands", "spent thousands of dollars",
         "lost thousands", "blown thousands", "blew thousands",
         "addicted and lost", "addicted and spent", "addicted and broke",
         # "I am addicted" — explicit self-identification
         "i am addicted", "i'm addicted", "im addicted",
         "i am so addicted", "i'm so addicted",
         "i am addicted and", "i'm addicted to this app",
         # "Digital gambling in its purest form"
         "digital gambling in its purest form", "digital gambling at its finest",
         "pure form of digital gambling", "this is digital gambling",
         # "Just a gambling app" / "basically gambling with trading cards"
         "just a gambling app", "it's just a gambling app", "basically a gambling app",
         "basically gambling with trading cards", "essentially a gambling app",
         "it's gambling with cards", "gambling with cards",
         # "Unregulated gambling at its finest"
         "unregulated gambling at its finest",
         # "Predatory" — bare word (safe; complaint-only + no 4★/5★ exposure)
         "predatory",
         # "Predatory marketing"
         "predatory marketing", "predatory tactics", "predatory design",
         # "A gambling game" — explicit gambling framing
         "a gambling game", "it's a gambling game", "this is a gambling game",
         # "It's a casino" — explicit casino comparison
         "it's a casino", "its a casino", "this is a casino",
         "basically a casino", "it's basically a casino",
         # "Get these gambling apps off the app store"
         "get these gambling apps off", "gambling apps off the app store",
     ]},

    # ── Cross-Brand Comparison (negative) ─────────────────────────────────────
    {"id": "brand-compare-bad", "name": "Prefers Another Brand",
     "keywords": [
         # Preferring another brand
         "arena club is better", "arena club is way better", "switch to arena club",
         "switching to arena club", "go to arena club",
         "courtyard is better", "courtyard is way better", "switch to courtyard",
         "switching to courtyard", "go to courtyard",
         "rips by triumph is better", "rbt is better", "switch to rbt",
         "switch to rips", "switching to rips by triumph",
         "icybox is better", "icy box is better", "switch to icybox",
         "switching to icybox", "go to icybox",
         # Leaving this app for one of the others
         "deleted this for", "leaving for arena club", "leaving for courtyard",
         "leaving for rbt", "leaving for icybox",
         "left for arena club", "left for courtyard", "left for rbt", "left for icybox",
     ]},

    # ── App Navigation / UX Issues ────────────────────────────────────────────
    {"id": "ux-bad", "name": "App Navigation / UX Issues",
     "keywords": [
         "not user friendly", "not very user friendly", "not very intuitive",
         "hard to navigate", "hard to use", "hard to find", "hard to understand",
         "difficult to navigate", "difficult to use", "difficult to find",
         "confusing app", "confusing layout", "confusing interface",
         "confusing navigation", "confusing design",
         "hard to navigate the app", "tough time navigating",
         "having a tough time navigating", "hard time navigating",
         "can't find", "cant find", "hard to find my",
         "hard to find the", "couldn't find", "couldn't figure out",
         "not intuitive", "really not intuitive",
         "poorly designed", "poor design", "bad design", "bad layout",
         "bad interface", "clunky interface", "clunky app",
         "bad user experience", "bad ux", "poor user experience",
         "ui is terrible", "ui is bad", "ui is awful", "ui is confusing",
         "interface is confusing", "interface is bad", "interface is terrible",
         "layout is confusing", "layout is bad", "navigation is bad",
         "navigation is confusing", "navigation is terrible",
         "no back button", "can't go back", "cant go back",
         "accidentally clicked", "accidentally tapped", "accidentally selected",
         "accidentally bought", "accidentally purchased", "no confirmation before",
         "no undo option", "no way to undo", "can't undo", "no cancel option",
         "can't cancel", "no option to cancel",
         "really confusing", "so confusing", "very confusing",
         "hard to figure out", "takes a while to figure out",
         "not easy to use", "not easy to navigate", "not straightforward",
     ]},

    # ── No Combined / Bulk Shipping ───────────────────────────────────────────
    {"id": "no-bulk-ship", "name": "No Combined Shipping",
     "keywords": [
         "can't ship together", "cant ship together", "can't combine shipping",
         "cant combine shipping", "combine shipping", "combined shipping",
         "ship multiple cards at once", "ship multiple cards together",
         "bulk shipping", "bulk ship", "no bulk shipping", "no bulk ship",
         "ship all at once", "ship all of them at once", "ship them all together",
         "ship them together", "send them together", "send all at once",
         "each card individually", "each card ships individually",
         "ships each card individually", "ships individually",
         "pay per card shipping", "charge per card to ship", "charge for each card",
         "have to ship one at a time", "ships one at a time", "ship one at a time",
         "no mass shipping", "mass ship", "mass shipping", "batch ship", "batch shipping",
         "have to pay shipping per card", "pay shipping for each card",
         "can't send multiple", "cant send multiple",
         "shipping each card separately", "ships cards separately",
         "individual shipping", "sends individually",
         "only ships one card at a time", "ship them all at once",
         "can only ship one at a time", "can't ship all at once",
         # Dollar-amount per card shipping cost complaints
         "$5 per card", "$5.50 per card", "$6 per card", "$6.00 per card",
         "5 dollars per card", "5.50 per card", "6 dollars per card",
         "per card no matter how many", "per card regardless",
         "shipping per card", "shipping for each slab", "shipping for every card",
         # "Can't add cards into same shipping"
         "can't add cards into the same shipping", "cant add cards into the same shipping",
         "add cards into the same shipping", "add them to the same shipment",
         "add to the same shipment", "add to one shipment",
         # "Purchase separately for each slab"
         "purchase separately for each", "ordered separately for each",
         "pay separately for each card", "pay for each one separately",
         # Separate shipping per slab / item
         "separate shipping for each", "separate shipment for each",
         "shipping for each one", "ship each one separately",
         # "Shipping cost is too high" per individual card context
         "33 to ship", "$33 to ship", "cost too much to ship each card",
         "cost more to ship than the card is worth",
         "shipping costs more than the card",
     ]},

    # ── App Slow / Laggy ─────────────────────────────────────────────────────
    {"id": "app-slow", "name": "App Slow / Laggy",
     "keywords": [
         "very laggy", "super laggy", "app is so laggy", "app is laggy",
         "so laggy", "too laggy", "extremely laggy",
         "app is slow", "slow app", "the app is slow", "really slow app",
         "app runs slow", "app loads slow", "app very slow",
         "loading slow", "loads really slow", "takes forever to load",
         "slow to load", "so slow to load",
         "app lags", "app lags a lot", "it lags", "always lags",
         "constant lag", "lots of lag", "terrible lag", "so much lag",
         "lagging all the time", "keeps lagging",
         "buffering a lot", "always buffering", "constant buffering",
         "slow performance", "poor performance", "bad performance",
         "app is sluggish", "sluggish app", "runs sluggish",
         "takes too long to respond", "not responding quickly",
         "slow to respond", "responds slowly",
         "app takes forever", "forever to open", "forever to load",
         "opening is slow", "startup is slow", "slow startup",
         "animations are slow", "slow animations",
     ]},

    # ── Limited Card Selection ────────────────────────────────────────────────
    {"id": "selection-bad", "name": "Limited Card Selection",
     "keywords": [
         "only pokemon", "only pokémon", "only pokemon cards", "only pokémon cards",
         "no sports cards", "no baseball cards", "no football cards",
         "no basketball cards", "no hockey cards", "no soccer cards",
         "no baseball", "no football", "no basketball", "no hockey",
         "no magic", "no magic the gathering", "no mtg", "no yugioh", "no yu-gi-oh",
         "need more card types", "more card types", "more types of cards",
         "more variety of cards", "more card variety", "better card selection",
         "limited selection", "limited card selection", "small selection",
         "not enough selection", "lack of selection", "lacking selection",
         "same card multiple times", "same cards multiple times",
         "duplicate cards", "getting duplicate cards", "too many duplicates",
         "repeat cards", "repeating cards", "getting the same cards",
         "more card options", "more pack options", "more packs available",
         "only one type of card", "only one type",
         "wish there were more cards", "wish there was more variety",
         "want more card types", "need different cards",
         "no rare cards to choose from", "limited rare options",
         # "Can only rip pokemon" — brand-specific selection complaint
         "can only rip pokemon", "can only rip one type",
         "prefer mtg", "prefer magic the gathering", "prefer yugioh",
         "prefer baseball cards", "prefer sports cards",
         "wish they had mtg", "wish they had magic", "wish they had sports cards",
         "no graded cards", "only raw cards", "only ungraded",
         "no graded options", "wish they had graded",
         "only baby cards", "only cheap cards", "only commons in the store",
         "not enough variety", "need more variety", "lacks variety",
         "only one sport", "need more sports",
     ]},

    # ── Misleading Advertising ────────────────────────────────────────────────
    {"id": "misleading-ads", "name": "Misleading Advertising",
     "keywords": [
         "ads don't show you have to pay", "ads don't tell you have to pay",
         "ads didn't show you have to pay", "ads are misleading",
         "nothing like shown in ads", "nothing like the ads", "not like in the ads",
         "misleading ads", "misleading advertisement", "misleading advertising",
         "ads are deceiving", "deceptive ads", "deceptive advertising",
         "not like the ads", "ads don't show", "ads showed something different",
         "the ad is misleading", "the advertisement is misleading",
         "false advertising", "false advertisement",
         "misleading commercials", "misleading marketing",
         "ad was misleading", "ads were misleading",
         "not what was advertised", "not what the ad showed",
         "ad made it look", "commercial made it seem",
         "ad shows wins", "ad shows big wins", "ad makes you think",
         "looked way better in the ad", "looks better in ads",
         "not like what you see in the ads",
         # "The ad said free shipping" — specific false promise in advertisement
         "the ad said free shipping", "the ads said free shipping",
         "ad said free shipping", "ads said free shipping",
         "advertised free shipping", "claimed free shipping", "promised free shipping",
         "the commercial said free shipping", "the ad promised free shipping",
         "definitely said free shipping", "said free shipping but",
         # "Hired [celebrity] for commercial" — endorser-based complaint
         "hired that loser", "hired a celebrity i don't support",
         "the commercial they made", "the commercial shows",
         # "Suicide ideation in the ad" — harmful ad content
         "suicide ideation in the ad", "harmful content in the ad",
         "inappropriate ad", "inappropriate advertisement", "the ad is inappropriate",
         # "Reviews are bought" / fake social proof
         "reviews are bought", "reviews are fake", "reviews are paid",
         "bought reviews", "fake reviews", "paid reviews",
         "bots left reviews", "review bombing",
     ]},

    # ── Referral / Invite Didn't Work ────────────────────────────────────────
    {"id": "referral-fail", "name": "Referral / Invite Didn't Work",
     "keywords": [
         "invite link didn't work", "invite link does not work", "invite link not working",
         "referral link not working", "referral link didn't work", "referral link doesn't work",
         "invite code didn't work", "invite code doesn't work", "invite code not working",
         "referral code didn't work", "referral code doesn't work", "referral code not working",
         "referral didn't work", "referral doesn't work", "referral not working",
         "invite didn't work", "invite doesn't work", "invite not working",
         "referral bonus not received", "referral credit not received",
         "didn't get my referral bonus", "never got my referral credit",
         "friend didn't get credit", "friend didn't receive credit",
         "promo code not working", "promo code didn't work",
         "sign up bonus not received", "sign up bonus didn't work",
         "my invite link", "my referral link", "my referral code",
         "referred a friend", "referral reward", "referral program doesn't work",
     ]},

    # ── Forced to Sell / Can't Hold Cards ────────────────────────────────────
    {"id": "forced-sell", "name": "Forced to Sell / Can't Hold Cards",
     "keywords": [
         "forced to sell", "forces you to sell", "makes you sell",
         "pushed to sell", "pressured to sell",
         "have to sell back", "have to sell it back", "have to sell your cards",
         "can't hold your cards", "cant hold your cards",
         "can't keep your cards", "cant keep your cards",
         "doesn't let you hold", "doesn't let you keep",
         "doesn't allow you to hold", "won't let you hold",
         "makes you sell your cards back", "you must sell back",
         "no option to hold", "no way to hold",
         "have to immediately sell", "immediately have to sell",
         "can't store your cards", "no card storage",
         "forces the sale", "forced sale",
         "makes you liquidate", "have to liquidate",
         "can't accumulate cards", "won't let you accumulate",
     ]},

    # ── Weekly Reward / Free Pack Didn't Arrive ───────────────────────────────
    {"id": "weekly-reward-broken", "name": "Weekly Reward Didn't Arrive",
     "keywords": [
         "weekly reward", "weekly free pack", "weekly pack didn't", "weekly pack did not",
         "weekly reward is fake", "weekly reward never came", "weekly reward didn't come",
         "weekly reward not showing", "weekly reward not there", "weekly reward disappeared",
         "didn't get my weekly", "didn't receive my weekly", "never got my weekly",
         "weekly pack is fake", "weekly pack never", "weekly free pack never",
         "second week in a row", "third week in a row", "week in a row that i lost",
         "lost my weekly pack", "missing my weekly pack", "missing my weekly reward",
         "weekly pack gone", "weekly reward gone", "where is my weekly",
         "weekly signature pack", "signature pack didn't", "signature pack not",
         "free weekly pack", "my weekly free", "weekly benefit",
         "first week only", "only get it the first week", "only worked once",
         "only got it once", "weekly didn't reset", "weekly reset didn't work",
         "free daily reward", "daily reward not showing", "daily reward disappeared",
         "free pack expired", "free pack gone", "lost my free pack",
         "didn't get my free pack", "never received my free pack",
         "free pack reward not credited", "reward not credited",
         "claimed reward but didn't get", "clicked on reward but didn't get",
     ]},

    # ── Spam Notifications / Emails ───────────────────────────────────────────
    {"id": "spam-emails", "name": "Spam Notifications / Emails",
     "keywords": [
         "spam emails", "spam email", "spamming me with emails", "spamming my email",
         "emails every hour", "emails every other hour", "email every day",
         "constant emails", "too many emails", "way too many emails",
         "excessive emails", "emails are excessive", "emails are annoying",
         "push notifications are annoying", "too many notifications",
         "constant notifications", "nonstop notifications", "too many push notifications",
         "notification spam", "spam notifications", "spamming me with notifications",
         "won't stop sending", "keeps sending me emails", "keeps emailing me",
         "emails are spammed", "bombarded with emails", "bombarded with notifications",
         "unsubscribe from emails", "can't unsubscribe", "turn off notifications",
         "annoying notifications", "overwhelming notifications",
         "email marketing", "unwanted emails", "marketing emails",
         "inbox flooding", "flooding my inbox", "flooded with emails",
         "getting spammed", "being spammed", "they spam you",
     ]},

    # ── Limited Payment Methods ───────────────────────────────────────────────
    {"id": "limited-payment", "name": "Limited Payment Methods",
     "keywords": [
         "only takes debit", "only accepts debit", "doesn't accept credit",
         "can't use credit card", "wont accept credit card", "won't take credit card",
         "no credit card", "no credit cards accepted",
         "doesn't accept paypal", "no paypal option", "can't use paypal",
         "only takes select", "only accepts select", "limited payment options",
         "payment options are limited", "can't use google pay", "no google pay",
         "no apple pay", "doesn't accept apple pay", "can't use apple pay",
         "no cash app", "doesn't take cash app", "can't use cashapp",
         "no venmo", "doesn't accept venmo", "doesn't take venmo",
         "payment method not supported", "my payment method doesn't work",
         "won't let me add my card", "can't add my debit card",
         "declined my card", "card was declined", "won't accept my card",
         "won't give me an option to set up", "can't set up payment",
         "no payment method works", "payment isn't working",
         "limited deposit options", "very few deposit options",
         "only takes certain cards", "only certain payment methods",
     ]},

    # ── Crypto Required (Courtyard) ───────────────────────────────────────────
    {"id": "crypto-required", "name": "Requires Crypto to Participate",
     "keywords": [
         "have to buy crypto", "need to buy crypto", "requires crypto",
         "buying crypto", "crypto as a currency", "crypto to make offers",
         "crypto to purchase", "only accepts crypto",
         "have to use cryptocurrency", "requires cryptocurrency",
         "not everyone has crypto", "don't want to use crypto",
         "don't understand crypto", "unfamiliar with crypto",
         "crypto barrier", "crypto hurdle", "complicated crypto",
         "usdc", "solana wallet", "need a wallet", "crypto wallet required",
         "have to set up a wallet", "wallet setup",
         "converting to crypto", "convert my money to crypto",
         "off-putting that you need crypto", "turn off by the crypto",
     ]},

    # ── Physical Card Arrived Damaged ─────────────────────────────────────────
    {"id": "card-damaged", "name": "Card Arrived Damaged / Wrong Item",
     "keywords": [
         "card came scratched", "card was scratched", "card has a scratch",
         "card arrived scratched", "arrived with a scratch", "scratch on my card",
         "came with a scratch", "card came with a scratch", "came with scratches",
         "scratched card", "scratched in the middle", "scratched on the back",
         "damaged card", "card was damaged", "card arrived damaged",
         "bent card", "card was bent", "card arrived bent", "card is bent",
         "card came bent", "creased card", "card has a crease",
         "card arrived with damage", "card is not mint", "not in mint condition",
         "wrong card", "wrong item", "got the wrong card", "sent the wrong card",
         "not the card i pulled", "not the card i won", "different card",
         "card was in poor condition", "card was in bad condition",
         "poor top loader", "weak top loader", "cheap top loader",
         "top loader was weak", "top loader was cheap",
         "no protective sleeve", "no sleeve", "sleeve was damaged",
         "card had marks", "card had fingerprints", "card wasn't protected",
         "card not as described", "not what was shown", "not as advertised card",
     ]},

    # ── Sign-Up Promo / Credits Not Received ─────────────────────────────────
    {"id": "promo-not-received", "name": "Sign-Up Promo / Credits Not Received",
     "keywords": [
         "free credits not received", "free credits didn't arrive",
         "sign up bonus not received", "sign up bonus didn't credit",
         "sign-up bonus not credited", "welcome bonus not received",
         "promo not applied", "promo code didn't work", "promo didn't apply",
         "said i would get free", "promised me free", "advertised free credits",
         "said i'd get", "told me i'd get", "never got my free",
         "said it was going to give me", "going to give me free", "supposed to give me free",
         "was going to give me", "said they would give me free",
         "didn't get the free credits", "didn't receive the credits",
         "didn't get my sign up", "didn't get my welcome",
         "free pack never came", "referral bonus not credited",
         "didn't apply the promo", "promo wasn't applied",
         "no free credits", "where are my free credits",
         "said 50 dollars in free credits", "said $50 in free credits",
         "said 20 dollars free", "said $20 free", "advertised free money",
         "never gave me the", "never credited my account",
         "promo not working", "code not working", "code doesn't work",
         "reward not applied", "bonus not applied", "bonus not credited",
         "first deposit bonus", "deposit bonus not received",
     ]},

    # ── Auction Problems (Arena Club) ─────────────────────────────────────────
    {"id": "auction-issues", "name": "Auction / Marketplace Problems",
     "keywords": [
         "auction reserve", "reserve price not shown", "hidden reserve",
         "won the auction but", "won auction but didn't get", "win the auction but",
         "auction winner but", "bid and won but",
         "auction doesn't notify", "no auction notification",
         "outbid without notice", "was outbid", "got outbid",
         "auction ended without", "auction closed without",
         "sold for wrong price", "sold for less than listed", "sold for half the price",
         "sold at wrong price", "listing price wrong",
         "sold below my asking", "sold for minimum price",
         "price error on auction", "wrong auction price",
         "buyout price changed", "buy it now price wrong",
         "marketplace listing issue", "listing disappeared",
         "listed a card and it sold for less", "notified it sold for less",
         "card sold without my approval", "sold without permission",
         "auction malfunction", "auction bug",
         "can't make offers", "offer not going through", "trade declined",
         "trade scam", "scammers in trades", "scammer in trades", "avoid trading with",
     ]},

    # ── Account Banned / Suspended ────────────────────────────────────────────
    {"id": "account-banned", "name": "Account Banned with Funds Locked",
     "keywords": [
         "account banned", "account was banned", "got banned", "permanently banned",
         "account suspended", "account was suspended", "got suspended",
         "account closed", "account was closed", "locked my account",
         "account locked", "account terminated", "permanently suspended",
         "banned my account", "suspended my account", "closed my account",
         "banned without reason", "banned with no reason", "banned without warning",
         "banned for no reason", "suspended for no reason",
         "banned with money inside", "banned with funds", "banned with balance",
         "locked out with money", "money trapped", "funds trapped", "balance trapped",
         "money inside and banned", "lost my funds when banned",
         "can't access my account", "lost access to my account",
         "account deletion", "deleted my account and", "deleted account",
         "permanently deleted my account", "they deleted my account",
         "banned and lost my money", "suspended and lost my money",
     ]},

    # ── Only One Card Per Pack ────────────────────────────────────────────────
    {"id": "only-one-card", "name": "Only Get One Card Per Pack",
     "keywords": [
         "only one card", "only get one card", "just one card", "only 1 card",
         "one card per pack", "you only get 1 card", "you only get one card",
         "expecting multiple cards", "expected multiple cards",
         "thought i'd get more cards", "thought there would be more",
         "thought it was multiple", "assumed i'd get multiple",
         "disappointed only seeing the 1 card", "disappointed only getting one",
         "only comes with one card", "comes with just one card",
         "single card per pack", "one card rip", "single rip",
         "1 pack is really just 1 card", "pack is really just 1",
         "wish you got more cards", "wish there were more cards per pack",
         "should come with more cards", "more cards per pack",
         "wanted more cards per pack",
     ]},

    # ── Android / Platform Disparity ─────────────────────────────────────────
    {"id": "platform-disparity", "name": "Android vs iOS Feature Gap",
     "keywords": [
         "android app is worse", "android version is worse",
         "ios is better than android", "android doesn't have",
         "not available on android", "android doesn't support",
         "only on ios", "ios only", "ios exclusive",
         "android app is missing", "android is behind",
         "please release on android", "please release to android",
         "not on google play", "not available on google play",
         "android users are left out", "android users get less",
         "obvious favoritism toward ios", "favoritism when comparing android",
         "android app inferior", "android is inferior",
         "need android version", "bring it to android",
         "android release", "when is android version",
         "android beta", "android rollout",
         "full app on ios but not android", "ios has features android doesn't",
         "android missing features", "features missing on android",
     ]},

    # ── Security / Privacy Concerns ───────────────────────────────────────────
    {"id": "security-concern", "name": "Security / Privacy / SSN Concerns",
     "keywords": [
         "social security number", "need my ssn", "requires ssn", "asking for ssn",
         "requires social security", "asking for social security",
         "ssn to withdraw", "social security to cash out",
         "data breach", "information was compromised", "my info was stolen",
         "leaked my information", "leaked my data",
         "unauthorized charge", "unauthorized transaction", "unauthorized purchase",
         "they charged my card without", "charged without my permission",
         "account after i installed", "new account created after installing",
         "netflix account created", "fraud after installing",
         "identity theft", "suspicious activity after",
         "security risk", "security concern", "privacy concern",
         "selling my data", "selling my information",
         "government id required", "passport required for",
         "too much personal info", "asking for too much info",
         "they have my personal information",
         "linked to fraud", "linked to identity theft",
         "after i downloaded", "after installing i noticed",
         "phishing", "phishing attempt",
     ]},

    # ── Geo-Restricted / Country Not Supported ────────────────────────────────
    {"id": "geo-restricted", "name": "Country / Region Not Supported",
     "keywords": [
         "not available in my country", "not available in my region",
         "not available in australia", "not available in canada",
         "not available in the uk", "not available in europe",
         "can't use in my country", "can't use outside the us",
         "only available in the us", "us only", "united states only",
         "not supported in my country", "country not supported",
         "region not supported", "blocked in my country", "blocked in my region",
         "doesn't work outside the us", "not accessible from",
         "don't allow australia", "don't allow canada", "don't allow uk",
         "not available to me because", "restricted to us",
         "won't let me use it because i'm in", "can't access because of my location",
         "geoblocked", "geo blocked", "geo-blocked",
         "not in my country", "my country isn't supported",
         "nigeria", "include nigeria", "include my country",
         "india passport", "india verification",
         "wish it was available worldwide", "wish it was available in my country",
     ]},

    # ── Unexpected Fees / Hidden Charges ─────────────────────────────────────
    {"id": "unexpected-fees", "name": "Unexpected Fees / Hidden Charges",
     "keywords": [
         "unexpected fee", "unexpected fees", "hidden fee", "hidden fees",
         "hidden charges", "unexpected charges", "surprise fee",
         "sales tax was unexpected", "didn't know about the tax",
         "charged sales tax", "added tax", "extra tax",
         "service fee", "service charge", "processing fee",
         "extra fees", "additional fees", "fees not disclosed",
         "fees not mentioned", "fees weren't mentioned",
         "10% fee", "10 percent fee", "ten percent fee",
         "charged more than expected", "cost more than advertised",
         "hidden cost", "hidden costs", "extra cost", "extra costs",
         "additional charges", "added charges", "undisclosed charges",
         "didn't mention the fee", "fees not shown", "fees not displayed",
         "fees not upfront", "not transparent about fees",
         "withdrew less than expected", "got less than expected due to fees",
         "deducted fees", "they deducted", "deducted from my withdrawal",
         "buyback fee", "10% buyback fee", "sells back at 90%",
     ]},

    # ── Pack Reveal Bait-and-Switch ───────────────────────────────────────────
    {"id": "pack-reveal-fake", "name": "Pack Reveal Bait-and-Switch",
     "keywords": [
         "shows a good card then flips", "good card then switches",
         "exciting card then turns", "shows expensive card then",
         "almost showed a rare", "about to flip to a rare but",
         "animation bait", "reveal bait", "fake reveal",
         "tease a good card", "teasing good cards",
         "about to turn it turns over", "just when it's about to turn",
         "card spins to the bad one", "spins away from the good card",
         "psychologically manipulative", "manipulative animation",
         "rigged animation", "animation is rigged",
         "no fireworks but still", "when no fireworks pop off",
         "the spinning is fake", "spin animation is fake",
         "direction of the spin", "spins in the wrong direction",
         "opposite direction than expected", "flips the wrong way",
         "shows you what could have been", "shows the card before",
         "reveals the bad card after showing the good",
         "calculated to show you the expensive one first",
         "designed to make you think you almost got",
         "algorithm shows you what you missed",
     ]},

    # ── Courtyard Daily Spin Rewards Not Accurate ─────────────────────────────
    {"id": "courtyard-rewards-rigged", "name": "Daily Spin / Rewards Not Accurate",
     "keywords": [
         "daily spin is rigged", "spin rewards are rigged", "spin is rigged",
         "spun and got less", "rolled but got less than shown",
         "spin said one thing but gave less",
         "333 points but gave 23", "rolled 333 but got 23",
         "rewards don't match", "reward doesn't match what it showed",
         "daily rewards are fake", "daily spin is fake",
         "spin gave wrong amount", "spin rewarded wrong",
         "points don't match", "points were wrong",
         "the spin lied", "spin cheated me",
         "daily reward is wrong", "daily points are wrong",
         "roll was wrong", "dice roll wrong",
         "daily bonus wrong", "free spin reward wrong",
         "rolling mechanic is broken", "rolling mechanic is rigged",
         "courtyard daily", "daily courtyard",
     ]},

    # ── Watch Quality Issues (IcyBox) ─────────────────────────────────────────
    {"id": "watch-quality-bad", "name": "Watch Quality Disappointing",
     "keywords": [
         "watch quality is bad", "watch quality is poor", "watch quality is terrible",
         "watch is cheap", "cheap watch", "low quality watch",
         "watch feels cheap", "watch looks cheap",
         "no name watch", "no-name watch", "unknown brand watch",
         "unknown brand", "random no name brand", "no name brands",
         "random brands", "garbage watches", "a lot of garbage watches",
         "unknown brands", "watches are junk", "junk watches",
         "watch was broken", "watch arrived broken", "watch doesn't work",
         "watch stopped working", "watch is defective",
         "not authentic", "not a real watch", "fake watch",
         "counterfeit watch", "replica watch",
         "watch is disappointing", "disappointed in the watch",
         "not worth it for the watch", "watch not worth",
         "mostly cheap watches", "mostly low value watches",
         "watch catalog is bad", "poor watch selection",
         "only cheap watches now", "cheaper watches lately",
         "watch selection has gotten worse",
     ]},

    # ── ID Verification Difficult ─────────────────────────────────────────────
    {"id": "id-verification-hard", "name": "ID Verification Too Difficult",
     "keywords": [
         "id verification is hard", "id verification is difficult", "id won't verify",
         "id failed to verify", "id verification failed", "id keeps failing",
         "can't verify my identity", "identity verification failed",
         "can't complete verification", "verification keeps failing",
         "camera is blurry", "blurry camera for id", "camera won't work for id",
         "id scan fails", "scan fails", "app can't read my id",
         "app doesn't recognize my id", "software rejects my id",
         "valid id not accepted", "id is valid but", "real id not working",
         "government id required", "need government id", "wants government id",
         "only accepts government id", "only passport", "needs passport",
         "social security required", "requires a social",
         "takes forever to verify", "verification takes forever",
         "been waiting for verification", "verification pending",
         "id verification is slow", "manual review taking forever",
         "kyc is hard", "kyc failing", "kyc won't work",
         "can't pass kyc", "can't complete kyc",
         "verification requirements are too strict", "overly strict verification",
         "id requirements are too high",
     ]},

    # ── Very Limited Card Types / Sports ─────────────────────────────────────
    {"id": "pack-type-limited", "name": "Too Few Card Types / Sports",
     "keywords": [
         "only pokemon", "only basketball", "only one piece",
         "only 3 types", "only three types", "only a few card types",
         "no baseball cards", "no football cards", "no nfl cards",
         "no mlb cards", "no soccer cards", "no hockey cards",
         "no yugioh", "no yu-gi-oh", "no magic the gathering",
         "no sports cards", "needs more sports",
         "wish they had nfl", "wish they had mlb", "wish they had nba",
         "wish they had football", "wish they had baseball",
         "wish they had more card types", "need more card types",
         "limited card types", "only certain card types",
         "nba starts at 25", "nba only",
         "want more variety in card types", "want different card types",
         "more card games", "need other card games",
         "only a handful of packs", "not enough pack types",
     ]},

    # ── No Way to Earn Free Packs / Play-to-Earn ─────────────────────────────
    {"id": "earn-free-packs", "name": "No Way to Earn Free Packs",
     "keywords": [
         "no way to earn free packs", "can't earn free packs",
         "wish i could earn packs", "no play to earn", "no way to earn",
         "no free way to play", "need to spend to play",
         "have to pay to do anything", "have to spend money to play",
         "no free option", "no free play", "no free mode",
         "wish there was a free mode", "wish you could play for free",
         "no mini games", "no games to earn", "no way to earn credits",
         "wish there was more ways to earn", "more options for earning",
         "earning free packs", "earn packs for free", "earn packs without spending",
         "ads for packs", "watch ads for packs", "watch an ad to earn",
         "play-to-win", "play to win", "play to earn",
         "no ads to earn", "no tasks to earn", "no daily tasks",
         "surveys for credits", "offers for credits",
         "no free daily pack", "no daily free pack",
     ]},

    # ── Counterfeit / Fake Card Received ─────────────────────────────────────
    {"id": "counterfeit-card", "name": "Received Counterfeit / Fake Card",
     "keywords": [
         "counterfeit card", "fake card", "received a fake card", "sent a fake card",
         "card was fake", "card is fake", "the card was counterfeit",
         "not authentic card", "card is not authentic", "card failed grading",
         "failed authentication", "failed the authenticity test",
         "sent to be graded and found out it was fake",
         "graded and found fake", "tested and fake", "tested fake",
         "card didn't pass", "card was rejected", "card was not graded",
         "not a real psa", "fake psa", "fake graded card",
         "fake slab", "not a real slab", "counterfeit slab",
         "replica card", "reprint card", "proxy card",
         "didn't grade well because", "wouldn't grade because fake",
     ]},

    # ── Pack Value Much Less Than Advertised ──────────────────────────────────
    {"id": "value-pack-mismatch", "name": "Pack Value Much Less Than Advertised",
     "keywords": [
         "opened a $20 pack and got", "opened 20 dollar pack and got",
         "opened $25 pack and got a dollar", "opened 25 dollar pack and",
         "shows average $200 but", "says average value but",
         "pack description says minimum", "minimum value not met",
         "average value is misleading", "stated value is wrong",
         "says $50 average but got", "says the cards are worth more than",
         "showed value of", "shows minimum value but",
         "way less than advertised value", "value much less than shown",
         "less than the minimum stated", "below the stated minimum",
         "not worth 20 dollars", "not worth the pack price",
         "card worth way less than the pack", "card is worth cents but pack cost",
         "dollar card from a 10 dollar pack", "cent card from a dollar pack",
         "68 cent card", "65 cent card", "less than a dollar card",
         "card worth nothing from expensive pack",
         "pack shows guaranteed minimum", "guaranteed minimum not honored",
         "what's in the pack show average", "pack description is misleading",
         "advertised value is wrong", "pack value not accurate",
         "minimum value lie", "misleading pack value",
     ]},

    # ── App Lost Purchase / Pack Opened Without Card ──────────────────────────
    {"id": "app-lost-purchase", "name": "App Lost Purchase / Pack Opened Without Card",
     "keywords": [
         "app took my money but", "charged me but didn't get", "charged me but no card",
         "opened without me", "opened by itself", "opened randomly",
         "randomly opened without", "opened without letting me",
         "pack opened but no card", "pack opened but nothing",
         "purchased but nothing appeared", "bought pack but didn't get anything",
         "money gone but no pack", "funds deducted but no pack",
         "credit deducted but nothing", "credits gone but nothing",
         "paid for pack but nothing", "charged for pack but nothing",
         "app glitched and opened", "glitch opened my pack",
         "error during opening", "error when opening pack",
         "loading error lost my", "crash lost my",
         "showroom and there's nothing", "showroom nothing there",
         "pressed accept but nothing", "accepted but didn't receive",
         "purchase went through but", "transaction went through but no",
         "lost my purchase", "purchase was lost",
         "pack just disappeared", "my pack disappeared",
         "10 cent card from bug", "worthless card from glitch",
     ]},

    # ── Grading Turnaround Too Long (Arena Club) ──────────────────────────────
    {"id": "grading-slow", "name": "Grading Takes Too Long",
     "keywords": [
         "grading is slow", "grading takes forever", "grading takes too long",
         "grading turnaround", "grading time is too long",
         "been waiting for grading", "still waiting for grading",
         "grading has been pending", "grading is pending",
         "months waiting for grading", "weeks waiting for grading",
         "grading is backed up", "grading queue is long",
         "grading is taking forever", "grading process is slow",
         "arena club grading", "grading at arena club",
         "submitted for grading and", "sent in for grading and",
         "grading delay", "delayed grading",
         "when will my grading", "where is my grading",
         "my card is still at grading", "stuck in grading",
         "turnaround time is too slow", "submission turnaround",
     ]},

    # ── Balance / Credits Disappeared ────────────────────────────────────────
    {"id": "balance-disappears", "name": "Balance / Credits Disappeared",
     "keywords": [
         "balance disappeared", "balance went to zero", "balance reset to zero",
         "balance is gone", "balance was gone", "balance just disappeared",
         "credits disappeared", "credits went to zero", "credits reset",
         "credits are gone", "credits just disappeared",
         "my money disappeared", "money disappeared from balance",
         "wallet is empty", "wallet went to zero", "wallet balance gone",
         "balance after logging out", "logged back in and balance was zero",
         "logged out and lost my balance", "logged back in and lost",
         "balance not there anymore", "missing from my balance",
         "my funds disappeared", "funds just disappeared",
         "winnings disappeared", "winnings not credited",
         "pulled a card but balance didn't change",
         "balance doesn't add up", "balance is wrong",
         "lost my balance randomly", "randomly lost my balance",
         "money taken from account", "balance depleted without",
         "credits gone after update", "balance gone after update",
     ]},

    # ── Withdrawal Processing Fee ─────────────────────────────────────────────
    {"id": "withdrawal-fee", "name": "Withdrawal / Cashout Fee",
     "keywords": [
         "withdrawal fee", "fee to withdraw", "charged to withdraw",
         "fee for cashing out", "fee to cash out", "fee when cashing out",
         "takes a fee", "deducted a fee", "fee is deducted",
         "processing fee to withdraw", "transaction fee to withdraw",
         "$1 fee", "one dollar fee", "dollar fee for withdrawal",
         "fee for paypal", "paypal fee", "paypal deducts",
         "less than expected because of fees", "got less because of fee",
         "deducted from my payout", "deducted from my withdrawal",
         "hidden withdrawal fee", "withdrawal fee not shown",
         "charge me to get my own money", "charged for my own money",
         "fee to get my own money out",
         "1.00 fee", "-$1", "minus a dollar", "-1.00",
         "they take a cut", "they take their cut",
     ]},

    # ── Overall App Complaint (catch-all — only if no other theme matched) ────
    {"id": "overall-bad", "name": "Unclassified Complaint",
     "keywords": [
         "this app sucks", "this app is terrible", "terrible app", "this app is trash",
         "trash app", "garbage app", "this app is garbage", "worst app",
         "worst app ever", "worst app i've ever", "worst app i've used",
         "absolute garbage", "complete garbage", "pure garbage", "total garbage",
         "absolute trash", "complete trash", "pure trash",
         "avoid this app", "stay away from this app", "stay away",
         "don't download", "do not download", "do not use this app",
         "don't waste your time", "waste of time", "total waste of time",
         "delete this app", "already deleted", "deleting this app",
         "1 star because", "giving 1 star", "giving one star", "deserves 0 stars",
         "zero stars", "0 stars",
         # Negative recommendation (contrast with positive "highly recommend")
         "would not recommend", "would never recommend", "cannot recommend",
         "can't recommend", "do not recommend", "don't recommend",
         "highly recommend not", "highly recommend you avoid", "highly recommend avoiding",
         "strongly recommend not", "strongly recommend against", "strongly recommend avoiding",
         "recommend you avoid", "recommend staying away", "recommend you stay away",
         "save yourself", "save your money", "save your time",
         "think before buying", "think twice before", "think long and hard before",
         "caution when playing", "use caution", "buyer beware", "be warned",
         "do yourself a favor and avoid", "do yourself a favor and don't",
         # More imperative "don't" complaint phrases (full phrase; negation check looks BEFORE "don't")
         "don't even try", "don't even bother downloading", "don't even bother",
         "do not spend your money", "do not waste your money on this",
         "don't use it", "don't use this app",
         # "Worst of the worst" / superlatives not captured
         "worst of the worst", "the worst app", "far inferior",
         # "Horrible app" — plain adjective form
         "horrible app", "terrible app", "awful app",
         # "This one is trash" — demonstrative form
         "this one is trash", "this thing is trash", "this is total trash",
         # "Other apps are better" / any other app — generic brand comparison
         "other apps are better", "any other app is better",
         "use literally any other", "literally any other app",
         "any other platform is better", "other platforms are better",
         # "Do yourself a favor and use another"
         "do yourself a favor and use", "do yourself a favor and try another",
         # "Not good at all"
         "not good at all", "not good whatsoever",
         # "Straight trash"
         "straight trash", "is straight trash", "app is straight trash",
         # "Nothing but trash"
         "nothing but trash",
         # "This app is horrible" — word-order variant of "horrible app"
         "this app is horrible", "this app is awful", "this app is trash",
         # "Bogus app" — skeptical/dismissive tone
         "bogus app", "this app is bogus",
         # "Terrible experience all around" — comprehensive pan
         "terrible experience all around", "awful experience all around",
         "horrible experience all around", "bad experience all around",
         # "Dont waste your time" — no-apostrophe variant (common in reviews)
         "dont waste your time", "dont waste money",
         "dont even bother", "dont bother downloading",
         "dont download", "dont use this app",
         # "Hot garbage" — informal intensifier
         "hot garbage", "this is hot garbage", "this app is hot garbage",
         # "Worse than slots" / casino comparison
         "worse than the actual casino", "worse than an actual casino",
         "worse than slots", "worse than slot machines",
         # "This is bs" / slang dismissal
         "this is bs", "it's all bs", "its all bs",
         "this app is bs", "this is total bs",
         # "Garbage from day one"
         "garbage from day one", "trash from day one",
         "trash from the start", "garbage from the start",
         # "Can't even be described as gambling" / no chance at winning
         "can't even be described as gambling", "cant even be described as gambling",
         "not even a chance at winning", "no chance of winning",
         "you have no chance", "you have no chance of winning",
         # "Severely regret" — buyer's remorse
         "severely regret", "deeply regret downloading",
         "regret downloading this", "regret getting this app",
         # "Never again" — strong rejection
         "never again", "never using this again", "never ever again",
         # "0/10 stars" — novel format for low rating
         "0/10 stars", "0 out of 10", "0/10",
         # "Immediate uninstall" / "just uninstalled"
         "immediate uninstall", "immediately uninstalled",
         "instantly uninstalled", "just uninstalled",
         # "Don't get this app"
         "don't get this app", "dont get this app",
         # "If I could rate lower" / "would give 0 stars"
         "if i could rate lower i would", "if i could give 0 stars",
         "would give 0 stars if i could", "would give negative stars",
         "would give 0 stars", "if i could give a negative",
         # "Absolutely trash" / superlative trash forms
         "absolutely trash", "absolutely terrible",
         "absolutely horrible", "absolutely awful",
         "absolutely garbage",
     ]},
]

PRAISE_THEMES = [
    # ── Great Pulls / Big Wins ────────────────────────────────────────────────
    {"id": "great-pulls", "name": "Great Pulls / Big Wins",
     "keywords": [
         "hit a grail", "pulled a grail", "hit the grail", "pulled the grail",
         "hit my grail", "pulled my grail",
         "amazing pull", "incredible pull", "insane pull", "unreal pull",
         "great pull", "fantastic pull", "awesome pull",
         "huge hit", "massive hit", "big hit", "monster hit",
         "big win", "huge win", "massive win", "absolute win",
         "best pull", "best card", "best pack", "dream card", "dream pull",
         "pulled something incredible", "pulled something amazing", "pulled something great",
         "best card i've ever", "best card ever pulled", "never pulled something this good",
         "hit big", "hit something big",
         "love my hit", "love my pull",
         "can't believe i pulled", "i actually hit",
     ]},

    # ── Fun / Exciting Experience ─────────────────────────────────────────────
    {"id": "fun", "name": "Fun / Exciting Experience",
     "keywords": [
         "love ripping", "love opening packs", "love opening boxes",
         "love this app", "loving this app", "love using this app",
         "love it", "love this", "love the app", "love the game",
         "love it so much", "i love it", "we love it",
         "so exciting", "super exciting", "really exciting", "so much excitement",
         "thrill of opening", "thrill of ripping", "the rush of", "love the rush",
         "best feeling when", "best feeling is when", "love the feeling",
         "so much fun", "so addicting in a good way", "addicting in the best",
         "dangerously addicting", "dangerously fun",
         "absolutely love it", "absolutely love this", "obsessed with this app",
         "obsessed with this", "can't stop using", "can't stop opening",
         "favorite app", "my favorite app", "my new favorite app",
         "best experience", "incredible experience", "amazing experience",
         "love the experience", "love the concept",
         "this is so cool", "this is amazing", "this is incredible",
         "it's amazing", "it is amazing", "it's awesome", "it is awesome",
         "this is awesome", "this is fire", "this is great", "this is perfect",
         "so good", "so great", "super fun", "really fun", "very fun",
         "really cool", "super cool", "very cool", "pretty cool",
         "great app", "amazing app", "awesome app", "best app",
         "really good app", "really great app",
         "fun to do", "fun to use", "fun to play",
         "addicting", "addictive", "super addicting", "very addicting",
         "it's fun", "its fun", "is fun", "such a fun",
         "great game", "amazing game", "awesome game",
         # "Having fun" — present-progressive enthusiasm
         "having fun", "having a blast", "having a great time",
         "having so much fun", "been having fun",
         # "Dope" / "Dope app" — Gen Z/slang enthusiasm
         "dope app", "dope game", "this is dope", "it's dope", "its dope",
         "so dope", "really dope",
         # "Love collecting" — collector enthusiasm
         "love collecting", "love to collect", "love the collecting",
         "love collecting cards", "love collecting pokemon",
         "great for collectors", "perfect for collectors",
         # "Fun so far" — early positive impression
         "fun so far", "fun so far!", "really fun so far",
         # "Making money and having fun" — dual positive
         "making money and having fun", "making some money and having fun",
         "win money and have fun",
         # General short enthusiasm
         "love it already", "love it so far", "love this already",
         "fire app", "this is fire", "it's lit", "its lit", "this slaps",
         "w app", "w game", "it's a w", "big w", "total w",
     ]},

    # ── Fair Odds / Good Value ────────────────────────────────────────────────
    {"id": "fair-value", "name": "Fair Odds / Good Value",
     "keywords": [
         "fair odds", "great odds", "good odds", "love the odds",
         "odds are great", "odds are good", "odds are fair", "odds are solid",
         "odds seem fair", "odds feel fair",
         "worth the money", "worth every penny", "worth the price",
         "great value", "good value", "amazing value", "incredible value",
         "great deal", "amazing deal", "incredible deal",
         "bang for your buck", "well priced", "reasonably priced", "affordable",
         "fair price", "fair pricing", "great pricing", "love the pricing",
         "good return", "great return",
         # "$1 packs" / low-price packs
         "$1 packs", "1 dollar packs", "dollar packs", "cheap packs",
         "low price packs", "low cost packs", "reasonable packs",
         "packs are cheap", "packs are affordable", "packs are reasonably priced",
         "done with money not gems", "done with money and not gems",
         "real money not gems", "uses real money", "no gems", "not gems",
         # Broke even / got money back
         "got my money back", "got about what i put in", "got back what i put in",
         "got back what i spent", "break even", "broke even",
         "made more than i spent", "made money on it",
     ]},

    # ── Fast Shipping ─────────────────────────────────────────────────────────
    {"id": "fast-ship", "name": "Fast Shipping",
     "keywords": [
         "shipped fast", "ships fast", "ships so fast", "arrived fast", "arrived quickly",
         "fast shipping", "quick shipping", "speedy shipping",
         "quick delivery", "fast delivery", "speedy delivery",
         "arrived in days", "arrived in 2 days", "arrived in 3 days",
         "next day delivery", "next day ship", "same day ship", "shipped same day",
         "got here so fast", "came so fast", "came super fast",
         "super fast shipping", "lightning fast", "quick turnaround",
         "love how fast", "impressed with shipping", "shipping was fast",
         "shipping was quick", "shipping is fast", "shipping is quick",
         "shipping was amazingly fast", "shipping was incredibly fast",
         "amazingly fast shipping", "incredibly fast shipping",
         "came quickly", "arrived quickly", "got it quickly", "came so quickly",
         "within 7 days", "within a week", "in just a few days",
     ]},

    # ── Great Packaging / Card Condition ──────────────────────────────────────
    {"id": "packaging", "name": "Great Packaging",
     "keywords": [
         "well packaged", "packaged well", "packaged perfectly", "packaged great",
         "packed very well", "packed well", "packed great",
         "great packaging", "amazing packaging", "love the packaging",
         "arrived in perfect condition", "arrived in great condition",
         "arrived in excellent condition", "arrived perfectly",
         "perfect condition", "mint condition", "no damage", "zero damage",
         "perfectly packaged", "carefully packaged",
         "cards were in great shape", "cards in perfect shape",
         "cards arrived perfectly", "cards were perfect",
         "nice presentation", "love the presentation", "great presentation",
         "looks beautiful", "looks amazing",
         "package was really secured", "package was secure", "nicely secured",
         "nice little box", "came in a nice box", "came in great packaging",
         "really good protective packaging", "good protective packaging",
         "protective packaging", "came in great condition",
     ]},

    # ── Buyback / Cashout Works Well ──────────────────────────────────────────
    {"id": "buyback", "name": "Buyback / Cashout Works",
     "keywords": [
         "love the buyback", "love buyback", "buyback is great", "buyback is amazing",
         "buyback is awesome", "great buyback", "amazing buyback", "awesome buyback",
         "buyback works great", "buyback works perfectly", "buyback is easy",
         "easy to cash out", "cashed out easily", "cashout was easy",
         "cashing out is easy", "cashing out is simple", "love cashing out",
         "love being able to sell back", "sell back easily", "easy to sell back",
         "love that i can sell", "love that you can sell", "love being able to sell",
         "buyback program is great", "buyback option is great",
         "love the cashout", "cashout feature is great", "cashout is smooth",
         "love selling back", "love the sell back",
         # "Withdraw works" / getting money out easily
         "withdraw works", "withdrawals work", "withdrawal works",
         "easy to withdraw", "simple to withdraw", "withdraw is easy",
         "no hassle withdrawing", "no hassle getting my money",
         "easy to get my money out", "easy to get my money",
         "love being able to withdraw", "love that i can withdraw",
         "cashed out instantly", "instant cashout", "instant withdrawal",
         "got my money out", "pulled my money out easily",
         "payout was fast", "payout is fast", "fast payout",
         "no issues withdrawing", "no problems withdrawing",
         # Easy deposit
         "easy to deposit", "easy to add money", "easy to add funds",
         "no issues depositing", "no problem adding money", "funding was easy",
         "deposit was easy", "depositing is easy", "simple to deposit",
         # "Cashout was quick and easy" — combined quick+easy cashout
         "cashout was quick", "cashout is quick", "cashout was fast",
         "cashout is fast", "quick cashout", "fast cashout",
         "cashout and easy", "quick and easy cashout",
         "was quick and easy to withdraw", "quick and easy to cash out",
         "withdrawal was fast", "withdrawal was quick", "withdrawal is fast",
         "withdrawal was smooth", "withdrawal is smooth",
         "cashed out quickly", "withdrew quickly", "got paid quickly",
         # "Adding funds was easy"
         "adding funds was easy", "adding money was easy", "adding cash was easy",
         "funds were added quickly", "funds added instantly",
         "money was added quickly", "money added instantly",
         # "Payout" variations
         "payouts work great", "payouts work perfectly", "payouts are great",
         "payouts are easy", "payouts are smooth", "love the payouts",
         "great payouts", "amazing payouts", "awesome payouts",
         "they pay out", "they pay you out", "they actually pay out",
         "pays out immediately", "pays out instantly", "pays out quickly",
     ]},

    # ── Easy to Use / Great App Design ───────────────────────────────────────
    {"id": "ux", "name": "Easy to Use / Great Design",
     "keywords": [
         "easy to use", "so easy to use", "really easy to use",
         "easy to navigate", "easy to find", "easy to understand",
         "user friendly", "user-friendly", "very user friendly",
         "very intuitive", "really intuitive", "super intuitive",
         "great interface", "love the interface", "love the ui",
         "great ui", "great ux", "great design", "beautiful design",
         "love the design", "smooth app", "smooth experience", "runs smoothly",
         "simple to use", "very simple", "well designed", "beautifully designed",
         "clean interface", "clean design", "clean app", "love the layout",
         "great layout", "perfect layout",
     ]},

    # ── Great Customer Service ────────────────────────────────────────────────
    {"id": "cs-good", "name": "Great Customer Service",
     "keywords": [
         "great customer service", "amazing customer service", "awesome customer service",
         "best customer service", "outstanding customer service", "excellent customer service",
         "incredible customer service", "fantastic customer service",
         "went above and beyond", "goes above and beyond",
         "super helpful", "very helpful", "extremely helpful", "so helpful",
         "very responsive", "super responsive", "incredibly responsive",
         "responded quickly", "fast response", "quick response",
         "handwritten note", "personal note", "thank you note",
         "they really care", "they actually care", "showed they care",
         "support team is great", "support was amazing", "support is amazing",
         "support team is amazing", "love the support",
     ]},

    # ── Cross-Brand Comparison (positive) ─────────────────────────────────────
    {"id": "brand-compare-good", "name": "Prefers This Brand vs Others",
     "keywords": [
         # Switched to this app from another brand
         "switched from arena club", "left arena club", "came from arena club",
         "switched from courtyard", "left courtyard", "came from courtyard",
         "switched from rbt", "left rbt", "came from rbt", "left rips by triumph",
         "switched from icybox", "left icybox", "came from icybox",
         # This one is better
         "better than arena club", "way better than arena club",
         "better than courtyard", "way better than courtyard",
         "better than rbt", "way better than rbt", "better than rips",
         "better than icybox", "way better than icybox",
     ]},

    # ── Would Recommend to Others ─────────────────────────────────────────────
    {"id": "recommend", "name": "Would Recommend to Others",
     "keywords": [
         "recommend to friends", "recommend to everyone", "recommend to family",
         "recommend to all my friends", "recommend to all friends",
         "recommend to all my family", "recommend this to everyone",
         "recommend this to my friends", "recommend this to anyone",
         "tell everyone", "told my friends about", "told all my friends",
         "i would recommend", "i would highly recommend", "would highly recommend",
         "highly recommend this", "highly recommend it", "highly recommend the app",
         "recommend this app", "recommend the app", "recommend downloading",
         "10 out of 10 recommend", "10/10 recommend", "recommend 10/10",
         "would recommend to anyone", "recommend to anyone who",
         "tell your friends", "tell a friend", "sharing with friends",
         "sharing with my family", "share with everyone",
         "must try", "must download", "everyone should try", "everyone should download",
         "get your friends on", "get my friends on", "get all my friends on",
         "got my friends to download", "got my friends to use",
         "showed my friends", "showed my family", "showed my dad", "showed my mom",
         "my friends and family", "friends and family would love",
         "you won't regret it", "won't regret downloading",
         "recommend to all", "recommend for everyone", "recommend for anyone",
         # "Would definitely recommend" — strong affirmation
         "would definitely recommend", "definitely recommend this",
         "definitely recommend downloading", "definitely recommend it",
         "definitely recommend the app",
         # "I would 100% recommend"
         "100% recommend", "100 percent recommend", "i'd recommend", "id recommend",
         # "Would not hesitate to recommend"
         "would not hesitate to recommend", "wouldn't hesitate to recommend",
         # "You should check it out"
         "you should check it out", "should definitely check it out",
         "check it out", "check this out if you",
         # Sharing referral / invite code
         "use my code", "use my referral", "use my invite", "use my promo code",
         "my code is", "my referral code is", "invite code is",
         "here's my code", "here is my code",
     ]},

    # ── Great Selection / Special Series ─────────────────────────────────────
    {"id": "selection-good", "name": "Great Card Selection / Special Series",
     "keywords": [
         "special series", "love the special series", "love the series",
         "great special series", "amazing special series", "awesome special series",
         "love the special packs", "great special packs",
         "love the variety", "great variety", "amazing variety", "awesome variety",
         "variety of packs", "variety of cards", "so many packs to choose from",
         "so many options", "love the options", "great selection",
         "amazing selection", "awesome selection", "love the selection",
         "so many different packs", "so many different cards",
         "love how many packs", "love how many cards",
         "so many different series", "lots of different series",
         "lots of different packs", "great pack selection", "pack selection is great",
         "pack variety", "love the pack variety",
         "always new packs", "always adding new packs", "always new cards",
         "always fresh content", "new content all the time",
         "love the different series", "love the card selection",
         "card selection is great", "card selection is amazing",
         "great lineup", "great pack lineup", "amazing lineup",
     ]},

    # ── Feels Legitimate / Transparent ───────────────────────────────────────
    {"id": "legit-good", "name": "Feels Legitimate / Transparent",
     "keywords": [
         "feels legit", "feels legitimate", "seems legit", "seems legitimate",
         "more legit than i expected", "more legitimate than i expected",
         "feels way more legit", "way more legit than i expected",
         "can see exactly what you might get", "can see what you get",
         "shows you the odds", "odds are transparent", "transparent odds",
         "actually legit", "is actually legit", "it's actually legit",
         "really is legit", "it really is legit",
         "trustworthy app", "trustworthy platform", "i trust this app",
         "actually trustworthy", "seems trustworthy",
         "not a scam", "definitely not a scam", "this isn't a scam",
         "it's legit", "its legit", "the app is legit", "app is legit",
         "legit company", "legit business", "legitimate company", "legitimate business",
         "real cards", "actual cards", "the cards are real", "real graded cards",
         "authentic cards", "cards are authentic", "genuine cards",
         "they actually send", "they do actually send", "they actually ship",
         "got my cards", "received my cards", "actually received",
         "cards actually showed up", "cards actually arrived",
         # "I thought it was a scam but" — converted skeptic
         "thought it was going to be a scam but", "thought it was a scam but",
         "thought this was a scam but", "expected it to be a scam but",
         "was skeptical but", "was skeptical at first but",
         "had my doubts but", "was hesitant but", "had doubts at first but",
         "skeptical at first", "had my doubts at first",
         "wasn't sure if it was legit", "wasn't sure it was legit",
         "wasn't sure about it but", "was unsure but",
         # "Actually pays out" — confirmation it's real
         "actually pays out", "actually pays", "does actually pay out",
         "it actually pays out", "this actually pays",
         "actually sends the cards", "actually delivers",
         # "Quick payouts" — speed of getting paid
         "quick payouts", "fast payouts", "quick payout", "fast payout",
         "speedy payouts", "instant payouts", "instant payout",
         "payouts are quick", "payouts are fast",
         # "I had my doubts" — broader skepticism resolved
         "had my doubts", "had doubts about it", "was doubtful but",
         "had my reservations but", "wasn't sure about this app",
     ]},

    # ── Odds Shown / Transparency ─────────────────────────────────────────────
    {"id": "odds-transparent", "name": "Love That Odds Are Shown",
     "keywords": [
         "love that they show the odds", "love that odds are shown",
         "nice to see the odds", "great that they show odds",
         "love being able to see the odds", "can see the odds before",
         "shows you the odds before", "odds displayed before",
         "able to check the odds", "check the odds before",
         "enjoy how you show the odds", "love how you show the odds",
         "made me feel more comfortable", "more comfortable knowing the odds",
         "odds are displayed", "odds are shown upfront",
         "odds shown upfront", "odds are listed", "odds are posted",
         "shows odds before you open", "can check the odds",
         "odds are transparent", "transparent about odds",
         "odds are clear", "odds are straightforward",
         "feel comfortable because of the odds", "felt more comfortable seeing odds",
         "made me trust it more", "made me feel safe knowing the odds",
         "appreciated seeing the odds", "glad they show the odds",
         "love the transparency on odds", "transparency of the odds",
         "odds listed on each pack", "odds on each box",
         "read through the box information", "box information before choosing",
         "box details before", "helpful box details",
         "at least i knew the odds", "knew what i was getting into",
         "liked having the odds", "prefer that odds are shown",
         "odds let me make an informed decision", "informed decision because of odds",
         "like that you can see the possible outcomes",
         "see the possible outcomes before",
     ]},

    # ── Reveal Animation / Suspense ───────────────────────────────────────────
    {"id": "reveal-animation", "name": "Reveal Animation / Suspense is Great",
     "keywords": [
         "reveal animation", "love the reveal", "love the animation",
         "reveal is great", "reveal is satisfying", "reveal is exciting",
         "reveal is fun", "reveal never gets old", "still exciting every time",
         "the suspense", "love the suspense", "the anticipation",
         "love the anticipation", "the moment before the reveal",
         "tiny pause before", "pause before the reveal",
         "that moment when", "the moment of reveal",
         "animation is smooth", "animations are smooth",
         "opening animation", "pack opening animation",
         "box opening animation", "opening is satisfying",
         "satisfying animation", "love the opening animation",
         "the animation gets me every time", "gets me every time",
         "adrenaline rush", "gives you an adrenaline rush",
         "rush when opening", "rush of opening",
         "the reveal is satisfying", "reveal animation is satisfying",
         "love watching the card flip", "watching the reveal",
         "love the reveal moment", "the opening experience",
         "best part is the reveal", "best part is the opening",
         "the suspense when opening", "suspense is great",
         "love the mystery", "love not knowing",
         "love the element of surprise",
     ]},

    # ── Gifting / Opening with Family ─────────────────────────────────────────
    {"id": "gifting", "name": "Great for Gifting / Family Fun",
     "keywords": [
         "gifted a box", "gifted it to", "gave it as a gift",
         "great gift", "perfect gift", "awesome gift", "gift idea",
         "gift for christmas", "gift for birthday", "birthday gift",
         "christmas gift", "holiday gift",
         "opened one with my son", "opened with my daughter", "opened with my kid",
         "opened with my dad", "opened with my mom", "opened with my family",
         "opened together with", "ripped one together",
         "sent a box to", "sent my dad a box", "sent my friend a box",
         "gift it to someone", "gift them a box",
         "great for kids", "kids loved it", "my kid loved it",
         "my son loved it", "my daughter loved it",
         "parents and kids", "parent and child", "with my children",
         "would gift these", "would give this as a gift",
         "perfect for the holidays", "holiday fun",
         "fun for the whole family", "family activity",
         "great way to bond", "bonding activity",
         "my sister put me on", "my brother told me about",
         "turned me on to this app", "put me on to this",
     ]},

    # ── Building a Collection / Collection Tracker ────────────────────────────
    {"id": "collection-building", "name": "Great for Building a Collection",
     "keywords": [
         "building my collection", "build my collection", "building a collection",
         "grow my collection", "growing my collection",
         "collection tracker", "tracks my collection", "keep track of my collection",
         "collection history", "pull history", "log of everything i've pulled",
         "love the collection feature", "love the tracking",
         "see everything i've pulled", "view my collection",
         "love that it keeps track", "keeps a running log",
         "adding to my collection", "add to my collection",
         "collector app", "great for collectors", "perfect for collectors",
         "for collectors like me", "as a collector",
         "got back into collecting", "gotten back into collecting",
         "got back into pokemon", "gotten back into cards",
         "building something over time", "feels like building something",
         "more than just one off", "more than just one-off",
         "getting cards i always wanted", "cards i've always wanted",
         "dream cards", "chase cards", "cards for my collection",
         "adding rare cards to my collection",
         "longtime collector", "serious collector",
         "getting graded cards for my collection",
         "card collecting", "watch collecting",
     ]},

    # ── Social / Sharing with Friends ────────────────────────────────────────
    {"id": "social-sharing", "name": "Fun to Share / Social Experience",
     "keywords": [
         "sharing with my friends", "shared my pull with",
         "my friends and i opened", "opened one with my friend",
         "my friend and i", "opened together",
         "send each other our pulls", "send our pulls to each other",
         "comparing our pulls", "compare pulls with friends",
         "told my friend", "told all my friends", "got my friends hooked",
         "got my friends on it", "put my friends on",
         "my friend introduced me", "friend showed me", "friend told me about",
         "watching each other open", "watching my friend open",
         "uploading to youtube", "upload my rips", "upload rips to youtube",
         "my youtube channel", "my audience", "content creator",
         "streaming my rips", "streaming my opens",
         "screenshotted my pull", "screen recording my pull",
         "sent the screen recording", "sharing screen recordings",
         "group activity", "group opening", "guys and i",
         "watch people open on youtube", "watching opens",
         "like watching unboxings", "like unboxing videos",
         "better than unboxing videos", "like those unboxing channels",
         "better than watching someone else",
         "now i'm the one opening",
     ]},

    # ── Marketplace / Trading Features (Arena Club) ───────────────────────────
    {"id": "marketplace-good", "name": "Marketplace / Trading Features",
     "keywords": [
         "love the marketplace", "great marketplace", "amazing marketplace",
         "love the trading feature", "trading is great", "great trading",
         "love being able to trade", "trading system is great",
         "buy and sell cards", "love that i can buy and sell",
         "marketplace is well done", "marketplace is great",
         "trades through the app safely", "trade through the app safely",
         "safely through the app", "collection is well done",
         "safe to trade on", "safe trades through",
         "marketplace is easy to use", "easy to buy on marketplace",
         "great selection on marketplace", "lot of cards on marketplace",
         "love the auction", "auction is great", "great auction",
         "buy at fixed rate", "fixed price option",
         "10% fee beats", "fee is fair", "lower fee than",
         "better fees than", "fees are reasonable",
         "love the safe trading", "safe trades", "trades safely through the app",
         "collect and trade", "buy sell and trade",
         "sell my cards on", "list my cards on",
         "love arena club marketplace", "arena club marketplace is great",
         "grade and sell", "graded marketplace",
         "collection management", "manage my collection",
     ]},

    # ── Limited Drops / Special Events ───────────────────────────────────────
    {"id": "limited-drops", "name": "Limited Drops / Special Events",
     "keywords": [
         "limited drop", "limited release", "limited edition drop",
         "love the limited drops", "the limited drops are great",
         "sold out quickly", "drops sell out fast", "drops sell out",
         "set a reminder for the drop", "reminded me of the drop",
         "got one before it sold out", "grabbed one before it sold out",
         "felt like an event", "like an event not just a purchase",
         "seasonal box", "seasonal drop", "seasonal release",
         "special event box", "limited quantities",
         "new drop", "upcoming drop", "next drop",
         "love new releases", "love when they drop new",
         "loved the 151 drop", "151 limited drop", "151 weekend",
         "limited box", "limited series", "exclusive drop",
         "exclusive box", "exclusive pack", "exclusive release",
         "slakoth box", "new box for the slabs",
         "always new content", "new content coming",
         "love the new boxes", "love the new packs",
         "special series drop", "anniversary drop",
     ]},

    # ── Watch Quality / Condition (IcyBox positive) ───────────────────────────
    {"id": "watch-quality-good", "name": "Watches Are Great Quality",
     "keywords": [
         "watch quality is great", "watch quality is amazing",
         "great quality watch", "amazing quality watch", "high quality watch",
         "watch was in great condition", "watch arrived in perfect condition",
         "love the watch i got", "love my watch", "love the watch i pulled",
         "watch is beautiful", "beautiful watch", "gorgeous watch",
         "watch i always wanted", "watch i've been wanting",
         "watches are authentic", "authentic watches",
         "came with authentication", "came with auth info", "authentication included",
         "real watch", "legit watch", "the watch is real",
         "better quality than expected", "quality exceeded expectations",
         "watch surprised me", "better than expected watch",
         "name brand watch", "recognized brand", "known brand",
         "seiko", "casio", "hamilton", "citizen", "tissot",
         "rolex", "panerai", "breitling", "tag heuer",
         "watch is a keeper", "keeping the watch", "decided to keep the watch",
         "wore it right away", "started wearing it",
         "in my regular rotation", "wearing it regularly",
         "watch was authentic", "watch was legit",
     ]},

    # ── Made Profit / Positive ROI ───────────────────────────────────────────
    {"id": "profit-made", "name": "Made Profit / Positive Return",
     "keywords": [
         "turned a profit", "made a profit", "made money on it",
         "made more than i put in", "made more than i spent",
         "up on my investment", "positive return",
         "deposited 10 made 60", "deposited 5 made", "put in 10 and made",
         "put in 20 and made", "put in 50 and made",
         "made $100 off", "made $200 off", "made $60 off",
         "made back double", "made back triple", "doubled my money",
         "tripled my money", "turned 5 into", "turned 10 into",
         "turned $5 into", "turned $10 into", "turned $20 into",
         "went from 5 to", "went from 10 to", "went from 20 to",
         "started with 5 ended with", "started with 10 ended",
         "invested 45 and got", "invested 50 and got",
         "1000 dollars in value", "made well over",
         "made 80 bucks", "made 70 bucks", "made 60 bucks",
         "come out ahead", "came out ahead", "came out on top",
         "actually made money", "actually turned a profit",
         "legitimately made money", "real money off this",
         "made my money back and then some", "made back more than i spent",
         "net positive", "in the green",
         "bills are paid", "paid my bills",
         "i'm up", "im up", "currently up",
     ]},

    # ── Referral / Invite Code Worked ────────────────────────────────────────
    {"id": "referral-good", "name": "Referral Code Worked / Free Packs",
     "keywords": [
         "referral code worked", "referral code works", "invite code worked",
         "promo code worked", "code gave me free", "code gave me credit",
         "code gave me packs", "got free packs from referral",
         "referral packs were free", "free packs from referral",
         "used a code and got", "used my code and got",
         "friend's code worked", "used my friend's code",
         "referral rewards are great", "love the referral program",
         "referral system is great", "great referral system",
         "got free credits from referral", "referral bonus worked",
         "got free credits using code",
         "code from instagram worked", "got extra credit with code",
         "discount code worked", "promo code applied",
         "get free packs for referring", "earn packs for referring",
         "earn free packs by referring", "free packs for every referral",
         "got packs from getting people to join", "multiple free packs from referrals",
         "multiple free packs from getting", "free packs from getting people",
         "packs from getting multiple people", "got multiple free packs from",
         "referral system works well", "referral is great",
         "pulled a card from referral pack", "referral pack came with a good card",
     ]},

    # ── Overall App Love (catch-all — only if no other theme matched) ─────────
    {"id": "overall-good", "name": "General App Praise",
     "keywords": [
         "best app ever", "best app i've ever used", "best app i've ever downloaded",
         "love this app so much", "this app is incredible", "this app is amazing",
         "this app is fantastic", "this app is awesome", "this app is great",
         "highly recommended",
         "10 out of 10", "10/10", "perfect app", "flawless app",
         "couldn't be happier", "couldn't be more happy", "couldn't ask for more",
         "exceeded my expectations", "blew my expectations", "blew me away",
         "exactly what i wanted", "exactly what i was looking for",
         "life changing app", "game changer", "game changing",
         "you won't be disappointed", "not disappointed",
     ]},
]

ALL_THEMES = COMPLAINT_THEMES + PRAISE_THEMES

# IDs of catch-all themes — only assigned when NO other theme of same type was found
_CATCHALL_COMPLAINT = "overall-bad"
_CATCHALL_PRAISE    = "overall-good"

# At 4★ only these functional complaint themes are allowed (not subjective ones)
_COMPLAINT_FUNCTIONAL = frozenset({
    "bugs", "login", "shipping-bad", "cs-bad", "cashout-bad",
    "no-bulk-ship", "app-slow", "selection-bad", "misleading-ads",
    "referral-fail", "forced-sell", "ux-bad",
    # New functional complaints (valid on 4★ reviews)
    "weekly-reward-broken", "spam-emails", "limited-payment",
    "card-damaged", "promo-not-received", "auction-issues",
    "account-banned", "id-verification-hard", "unexpected-fees",
    "geo-restricted", "security-concern", "balance-disappears",
    "withdrawal-fee", "app-lost-purchase", "counterfeit-card",
    "grading-slow", "platform-disparity",
})

# Words that negate the complaint/praise meaning of a keyword that follows them.
# "no" and "never" are intentionally EXCLUDED: in review context these almost
# always appear as part of the complaint itself ("no hits", "never win"),
# not as negation of a nearby keyword. Including them caused false negatives.
_NEGATION_WORDS = frozenset([
    "not", "don't", "dont", "doesn't", "doesnt", "didn't", "didnt",
    "without", "can't", "cant", "won't", "wont",
    "isn't", "isnt", "wasn't", "wasnt", "wouldn't", "wouldnt",
    "couldn't", "couldnt", "shouldn't", "shouldnt",
    "haven't", "havent", "hasn't", "hasnt", "hadn't", "hadnt",
])


def _find_kw(text: str, kw: str, start: int = 0) -> int:
    """
    Return the index of kw in text (>= start), or -1 if not found.

    Digit-boundary check: if kw starts with a digit (e.g. '0 stars'),
    require that the character immediately before the match is NOT a digit.
    This prevents '0 stars' from matching inside '10 stars' or '100 stars'.
    """
    pos = start
    while True:
        idx = text.find(kw, pos)
        if idx == -1:
            return -1
        if kw[0].isdigit() and idx > 0 and text[idx - 1].isdigit():
            pos = idx + 1
            continue   # false match — digit immediately before; keep searching
        return idx


def _is_negated(text: str, match_start: int, window: int = 4) -> bool:
    """
    Return True if a negation word appears in the `window` words before match_start,
    within the same sentence (no sentence-ending punctuation between them).

    E.g. "I can't stay away from it" → 'can't' precedes 'stay away' → negated.
    E.g. "you will not pull it. Do yourself a favor and avoid" → 'not' is in a
         prior sentence, so 'do yourself a favor and avoid' is NOT negated.
    Window kept small (4) to avoid distant words interfering.
    """
    prefix = text[:match_start]
    words_before = prefix.split()[-window:]
    if not set(words_before) & _NEGATION_WORDS:
        return False
    # A sentence boundary (. ! ? or newline) between the negation word and the
    # keyword means the negation does not carry across the sentence break.
    last_punct = max(
        prefix.rfind('.'), prefix.rfind('!'),
        prefix.rfind('?'), prefix.rfind('\n'),
    )
    if last_punct >= 0:
        # Only words in the current sentence (after the last sentence-ender) count.
        after_punct = prefix[last_punct + 1:]
        return bool(set(after_punct.split()) & _NEGATION_WORDS)
    return True


def _complaint_allowed(theme_id: str, stars: int) -> bool:
    """
    Star-based gate for complaint themes.
      5★ → never tag any complaint (genuine 5★ reviewers don't write complaints;
           the rare gaming of the star system is an acceptable miss).
      4★ → only functional complaints (bugs, login, shipping, CS, cashout).
           Subjective/attitude complaints (rigged, scam, addiction…) at 4★ are
           almost always disclaimers or sarcasm, not genuine gripes.
      3★ and below → all complaint themes allowed.
    """
    if stars >= 5:
        return False
    if stars == 4 and theme_id not in _COMPLAINT_FUNCTIONAL:
        return False
    return True


def classify_themes(review: dict) -> tuple:
    """
    Return (theme_ids: list[str], reasons: dict[str, str]).

    reasons maps theme_id → the specific phrase that triggered it,
    for display in the dashboard 'why categorized' tooltip.

    Accuracy layers (applied in order):
      1. Star gate: 5★ → no complaint themes; 4★ → functional complaints only;
         1-2★ → no praise themes.
      2. Digit-boundary: '0 stars' must not match inside '10 stars'.
      3. Negation window: if a negation word appears in the 8 words before a
         keyword, the match is discarded (e.g. "can't stay away from it").
      4. Catch-all themes fire only when no specific theme of the same polarity
         was found.
    """
    text  = ((review.get("title") or "") + " " + (review.get("body") or "")).lower()
    # Normalize smart/curly apostrophes to straight (common in mobile keyboard input)
    text = text.replace('’', "'").replace('‘', "'")
    stars = int(review.get("stars") or 3)

    complaint_ids = {t["id"] for t in COMPLAINT_THEMES if t["id"] != _CATCHALL_COMPLAINT}
    praise_ids    = {t["id"] for t in PRAISE_THEMES    if t["id"] != _CATCHALL_PRAISE}

    matched_ids  = []
    reasons      = {}

    # ── Check non-catchall themes ────────────────────────────────────────────
    for t in ALL_THEMES:
        if t["id"] in (_CATCHALL_COMPLAINT, _CATCHALL_PRAISE):
            continue
        is_complaint = t["id"] in complaint_ids
        is_praise    = t["id"] in praise_ids

        # Star gates
        if is_complaint and not _complaint_allowed(t["id"], stars):
            continue
        if is_praise and stars < 3:
            continue   # 1-2★ → skip praise themes

        for kw in t["keywords"]:
            idx = _find_kw(text, kw)
            if idx == -1:
                continue
            # Negation check: skip if keyword is preceded by a negation word
            if _is_negated(text, idx):
                continue
            matched_ids.append(t["id"])
            reasons[t["id"]] = kw   # first non-negated phrase wins
            break

    matched_set = set(matched_ids)

    # ── Catch-all complaint — only if no specific complaint found AND star gate ──
    has_specific_complaint = bool(matched_set & complaint_ids)
    if not has_specific_complaint and _complaint_allowed(_CATCHALL_COMPLAINT, stars):
        for t in COMPLAINT_THEMES:
            if t["id"] == _CATCHALL_COMPLAINT:
                for kw in t["keywords"]:
                    idx = _find_kw(text, kw)
                    if idx == -1:
                        continue
                    if _is_negated(text, idx):
                        continue
                    matched_ids.append(_CATCHALL_COMPLAINT)
                    reasons[_CATCHALL_COMPLAINT] = kw
                    break
                break

    # ── Catch-all praise — only if no specific praise found AND stars ≥ 3 ─────
    has_specific_praise = bool(matched_set & praise_ids)
    if not has_specific_praise and stars >= 3:
        for t in PRAISE_THEMES:
            if t["id"] == _CATCHALL_PRAISE:
                for kw in t["keywords"]:
                    idx = _find_kw(text, kw)
                    if idx == -1:
                        continue
                    if _is_negated(text, idx):
                        continue
                    matched_ids.append(_CATCHALL_PRAISE)
                    reasons[_CATCHALL_PRAISE] = kw
                    break
                break

    # ── Hard fallback: every review gets a bucket ────────────────────────────
    if not matched_ids:
        if stars <= 3:
            # 1-3★ with no specific theme → Unclassified Complaint
            matched_ids.append(_CATCHALL_COMPLAINT)
            reasons[_CATCHALL_COMPLAINT] = "__star_fallback__"
        else:
            # 4-5★ with no specific theme → Unclassified Praise
            matched_ids.append(_CATCHALL_PRAISE)
            reasons[_CATCHALL_PRAISE] = "__star_fallback__"

    return matched_ids[:8], reasons  # cap at 8 themes per review


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


def _generate_narrative(total, pos_pct, neg_pct, delta, prev_pos_pct,
                        top_complaints, top_praise):
    """Return a 2-3 sentence analyst-style narrative for a period."""
    if total == 0:
        return "No reviews recorded this period."

    # Sentence 1 — volume + direction
    if delta is None or delta == 0:
        s1 = f"{total} reviews this period."
    elif delta > 0:
        s1 = f"{total} reviews this period — up {delta:+d} from the prior period."
    else:
        s1 = f"{total} reviews this period — down {abs(delta)} from the prior period."

    # Sentence 2 — sentiment
    if pos_pct >= 60:
        mood = "strong positive sentiment"
    elif pos_pct >= 45:
        mood = "mixed-to-positive sentiment"
    elif neg_pct >= 50:
        mood = "predominantly negative sentiment"
    elif neg_pct >= 35:
        mood = "mixed-to-negative sentiment"
    else:
        mood = "neutral-leaning sentiment"

    if prev_pos_pct is not None:
        pp_delta = pos_pct - prev_pos_pct
        if pp_delta >= 5:
            s2 = (f"Sentiment registered {mood} ({pos_pct}% positive), "
                  f"improving {pp_delta}pp over the prior period.")
        elif pp_delta <= -5:
            s2 = (f"Sentiment landed at {mood} ({pos_pct}% positive), "
                  f"declining {abs(pp_delta)}pp from the prior period.")
        else:
            s2 = (f"Sentiment held at {mood} ({pos_pct}% positive), "
                  f"roughly steady vs. the prior period.")
    else:
        s2 = (f"Sentiment registered {mood}, "
              f"with {pos_pct}% positive and {neg_pct}% negative.")

    # Sentence 3 — themes
    c_names = [t['name'] for t in top_complaints[:2] if t['count'] > 0]
    p_names = [t['name'] for t in top_praise[:2]    if t['count'] > 0]
    if c_names and p_names:
        c_str = " and ".join(f'“{n}”' for n in c_names)
        p_str = " and ".join(f'“{n}”' for n in p_names)
        s3 = f"Leading complaints centred on {c_str}; top praise highlighted {p_str}."
    elif c_names:
        c_str = " and ".join(f'“{n}”' for n in c_names)
        s3 = f"Most-voiced complaints targeted {c_str}."
    elif p_names:
        p_str = " and ".join(f'“{n}”' for n in p_names)
        s3 = f"Reviewers most praised {p_str}."
    else:
        s3 = ""

    return " ".join(x for x in [s1, s2, s3] if x)


def _period_brand_stats(brs, prev_brs, all_names, complaint_ids, praise_ids):
    """
    Compute full stats for one brand in one period.

    Parameters
    ----------
    brs       : list of review dicts for this period
    prev_brs  : list of review dicts for the previous period (or None)
    all_names : {theme_id: theme_name}
    complaint_ids, praise_ids : sets of theme IDs

    Returns a dict suitable for insertion into brands_data[bid].
    """
    total = len(brs)
    pos = sum(1 for r in brs if r.get('sentiment') == 'positive')
    neu = sum(1 for r in brs if r.get('sentiment') == 'neutral')
    neg = sum(1 for r in brs if r.get('sentiment') == 'negative')
    pos_pct = round(pos / total * 100) if total else 0
    neu_pct = round(neu / total * 100) if total else 0
    neg_pct = max(0, 100 - pos_pct - neu_pct)

    prev_total = len(prev_brs) if prev_brs is not None else 0
    prev_pos   = sum(1 for r in prev_brs if r.get('sentiment') == 'positive') if prev_brs else 0
    prev_pos_pct = round(prev_pos / prev_total * 100) if prev_total else None
    delta = total - prev_total if prev_brs is not None else 0

    c_cnt, p_cnt   = defaultdict(int), defaultdict(int)
    c_quotes, p_quotes = defaultdict(list), defaultdict(list)

    for r in brs:
        body = (r.get('body') or '').strip()
        for t in r.get('themes') or []:
            if t in complaint_ids:
                c_cnt[t] += 1
                if len(c_quotes[t]) < 2 and len(body) > 20:
                    c_quotes[t].append(body[:200])
            if t in praise_ids:
                p_cnt[t] += 1
                if len(p_quotes[t]) < 2 and len(body) > 20:
                    p_quotes[t].append(body[:200])

    top_complaints = [
        {'id': k, 'name': all_names.get(k, k), 'count': v, 'quotes': c_quotes[k]}
        for k, v in sorted(c_cnt.items(), key=lambda x: -x[1])[:8]
    ]
    top_praise = [
        {'id': k, 'name': all_names.get(k, k), 'count': v, 'quotes': p_quotes[k]}
        for k, v in sorted(p_cnt.items(), key=lambda x: -x[1])[:8]
    ]

    narrative = _generate_narrative(
        total=total, pos_pct=pos_pct, neg_pct=neg_pct,
        delta=delta, prev_pos_pct=prev_pos_pct,
        top_complaints=top_complaints, top_praise=top_praise,
    )

    return {
        'total': total,
        'pos': pos, 'neu': neu, 'neg': neg,
        'pos_pct': pos_pct, 'neu_pct': neu_pct, 'neg_pct': neg_pct,
        'delta': delta,
        'prev_pos_pct': prev_pos_pct,
        'top_complaints': top_complaints,
        'top_praise': top_praise,
        'narrative': narrative,
    }


def compute_timeline(reviews: list) -> list:
    """Group reviews into Mon-Sun weeks, compute per-brand + all-brand stats."""
    from collections import defaultdict as _dd
    complaint_ids = {t['id'] for t in COMPLAINT_THEMES}
    praise_ids    = {t['id'] for t in PRAISE_THEMES}
    all_names     = {t['id']: t['name'] for t in COMPLAINT_THEMES + PRAISE_THEMES}

    # bucket: week_monday -> brand -> [reviews]
    buckets = _dd(lambda: _dd(list))
    for r in reviews:
        try:
            d = date.fromisoformat(r.get('date', ''))
        except Exception:
            continue
        monday = d - timedelta(days=d.weekday())
        buckets[monday][r.get('brand', '')].append(r)

    sorted_weeks = sorted(buckets.keys())
    result = []
    for i, ws in enumerate(sorted_weeks):
        we = ws + timedelta(days=6)
        brands_data = {}
        for bid in BRAND_ORDER:
            brs      = buckets[ws].get(bid, [])
            prev_brs = buckets[sorted_weeks[i-1]].get(bid, []) if i > 0 else None
            brands_data[bid] = _period_brand_stats(
                brs, prev_brs, all_names, complaint_ids, praise_ids)

        # All-brands aggregate
        all_brs      = [r for b in buckets[ws].values()              for r in b]
        prev_all_brs = [r for b in buckets[sorted_weeks[i-1]].values() for r in b] if i > 0 else None
        brands_data['all'] = _period_brand_stats(
            all_brs, prev_all_brs, all_names, complaint_ids, praise_ids)

        result.append({
            'week_start': ws.isoformat(),
            'week_end':   we.isoformat(),
            'label':      f"{ws.strftime('%b %-d')}–{we.strftime('%b %-d')}",
            'month':      ws.strftime('%b'),
            'month_num':  ws.month,
            'brands':     brands_data,
        })
    return result


def compute_monthly_timeline(reviews: list) -> list:
    """Group reviews into calendar months, compute per-brand + all-brand stats."""
    import calendar as _cal
    from collections import defaultdict as _dd
    complaint_ids = {t['id'] for t in COMPLAINT_THEMES}
    praise_ids    = {t['id'] for t in PRAISE_THEMES}
    all_names     = {t['id']: t['name'] for t in COMPLAINT_THEMES + PRAISE_THEMES}

    buckets: dict = _dd(lambda: _dd(list))
    for r in reviews:
        try:
            d = date.fromisoformat(r.get('date', ''))
        except Exception:
            continue
        buckets[(d.year, d.month)][r.get('brand', '')].append(r)

    sorted_periods = sorted(buckets.keys())
    result = []
    for i, (yr, mo) in enumerate(sorted_periods):
        _, last_day = _cal.monthrange(yr, mo)
        mstart = date(yr, mo, 1)
        mend   = date(yr, mo, last_day)
        brands_data = {}
        for bid in BRAND_ORDER:
            brs      = buckets[(yr, mo)].get(bid, [])
            prev_brs = buckets[sorted_periods[i-1]].get(bid, []) if i > 0 else None
            brands_data[bid] = _period_brand_stats(
                brs, prev_brs, all_names, complaint_ids, praise_ids)

        all_brs      = [r for b in buckets[(yr, mo)].values()              for r in b]
        prev_all_brs = [r for b in buckets[sorted_periods[i-1]].values() for r in b] if i > 0 else None
        brands_data['all'] = _period_brand_stats(
            all_brs, prev_all_brs, all_names, complaint_ids, praise_ids)

        result.append({
            'period_start': mstart.isoformat(),
            'period_end':   mend.isoformat(),
            'label':        mstart.strftime('%B %Y'),
            'short_label':  mstart.strftime('%b %Y'),
            'year':         yr,
            'month':        mo,
            'brands':       brands_data,
        })
    return result


def compute_quarterly_timeline(reviews: list) -> list:
    """Group reviews into calendar quarters (Q1=Jan-Mar … Q4=Oct-Dec)."""
    import calendar as _cal
    from collections import defaultdict as _dd
    complaint_ids = {t['id'] for t in COMPLAINT_THEMES}
    praise_ids    = {t['id'] for t in PRAISE_THEMES}
    all_names     = {t['id']: t['name'] for t in COMPLAINT_THEMES + PRAISE_THEMES}

    _QSTART = {1: 1, 2: 4, 3: 7, 4: 10}
    _QEND   = {1: 3, 2: 6, 3: 9, 4: 12}

    def _quarter(d): return (d.month - 1) // 3 + 1

    buckets: dict = _dd(lambda: _dd(list))
    for r in reviews:
        try:
            d = date.fromisoformat(r.get('date', ''))
        except Exception:
            continue
        buckets[(d.year, _quarter(d))][r.get('brand', '')].append(r)

    sorted_periods = sorted(buckets.keys())
    result = []
    for i, (yr, q) in enumerate(sorted_periods):
        end_mo       = _QEND[q]
        _, last_day  = _cal.monthrange(yr, end_mo)
        qstart = date(yr, _QSTART[q], 1)
        qend   = date(yr, end_mo, last_day)
        brands_data = {}
        for bid in BRAND_ORDER:
            brs      = buckets[(yr, q)].get(bid, [])
            prev_brs = buckets[sorted_periods[i-1]].get(bid, []) if i > 0 else None
            brands_data[bid] = _period_brand_stats(
                brs, prev_brs, all_names, complaint_ids, praise_ids)

        all_brs      = [r for b in buckets[(yr, q)].values()              for r in b]
        prev_all_brs = [r for b in buckets[sorted_periods[i-1]].values() for r in b] if i > 0 else None
        brands_data['all'] = _period_brand_stats(
            all_brs, prev_all_brs, all_names, complaint_ids, praise_ids)

        result.append({
            'period_start': qstart.isoformat(),
            'period_end':   qend.isoformat(),
            'label':        f"Q{q} {yr}",
            'short_label':  f"Q{q} '{str(yr)[2:]}",
            'year':         yr,
            'quarter':      q,
            'brands':       brands_data,
        })
    return result


def compute_themes(reviews: list) -> tuple:
    """Compute complaint and praise theme counts per brand.

    Themes are counted from ALL reviews (not filtered by overall sentiment)
    because a single review can contain both a complaint signal and a praise
    signal — e.g. "loved the odds but can't cash out."  The phrase-level
    keywords are specific enough to self-select: positive phrasing won't
    appear in complaint keyword lists and vice-versa.
    """
    for r in reviews:
        if not r.get("themes"):
            ids, reasons = classify_themes(r)
            r["themes"] = ids
            r["theme_reasons"] = reasons
        elif not r.get("theme_reasons"):
            # Back-fill reasons for reviews classified in a prior pass
            _, reasons = classify_themes(r)
            r["theme_reasons"] = reasons

    def count(theme_list, all_reviews):
        out = []
        for t in theme_list:
            row = {"id": t["id"], "name": t["name"]}
            for bid in BRAND_ORDER:
                key = BRANDS[bid]["key"]
                row[key] = sum(
                    1 for r in all_reviews
                    if r.get("brand") == bid and t["id"] in r.get("themes", [])
                )
            out.append(row)
        return out

    return count(COMPLAINT_THEMES, reviews), count(PRAISE_THEMES, reviews)


def compute_digest(reviews: list, ratings: dict, sentiment: dict,
                   complaint_themes: list, praise_themes: list) -> dict:
    """Build WEEKLY_DIGEST — structured editorial object for the digest layout.

    Returns a dict with:
      week, total_new, arena_club, competitors, weekly_analysis
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

        top_comp_sorted   = sorted(complaint_themes, key=lambda t: t.get(bkey, 0), reverse=True)
        top_praise_sorted = sorted(praise_themes,    key=lambda t: t.get(bkey, 0), reverse=True)

        top_complaints = [
            {"id": t["id"], "name": t["name"], "count": t.get(bkey, 0)}
            for t in top_comp_sorted if t.get(bkey, 0) > 0
        ][:3]
        top_praises = [
            {"id": t["id"], "name": t["name"], "count": t.get(bkey, 0)}
            for t in top_praise_sorted if t.get(bkey, 0) > 0
        ][:3]

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
            "top_complaints":   top_complaints,
            "top_praises":      top_praises,
        }

    ac    = brand_section("arena-club")
    comps = {bid: brand_section(bid) for bid in ["courtyard", "rbt", "icybox"]}

    # ── Weekly analysis — 3-paragraph narrative ──────────────────────────────
    def senti_dir(sd_dict):
        pos_d = (sd_dict.get("pos_delta") or 0) * 100
        neg_d = (sd_dict.get("neg_delta") or 0) * 100
        if pos_d > 3:  return "improving"
        if neg_d > 3:  return "declining"
        return "steady"

    ac_sd  = sdel.get("arena-club", {})
    cy_sd  = sdel.get("courtyard",  {})
    rbt_sd = sdel.get("rbt",        {})
    icy_sd = sdel.get("icybox",     {})

    ac_new  = ac["new_reviews"]
    cy_new  = comps["courtyard"]["new_reviews"]
    rbt_new = comps["rbt"]["new_reviews"]
    icy_new = comps["icybox"]["new_reviews"]
    total_new_cnt = ac_new + cy_new + rbt_new + icy_new

    ac_pos  = sentiment["arena-club"]["pos"]
    ac_neg  = sentiment["arena-club"]["neg"]
    cy_neg  = sentiment["courtyard"]["neg"]
    rbt_neg = sentiment["rbt"]["neg"]
    icy_neg = sentiment["icybox"]["neg"]

    # Para 1: What happened
    brand_lines = [
        f"Arena Club collected {ac_new} new reviews (sentiment {senti_dir(ac_sd)})",
        f"Courtyard {cy_new} ({senti_dir(cy_sd)})",
        f"Rips by Triumph {rbt_new} ({senti_dir(rbt_sd)})",
        f"IcyBox {icy_new} ({senti_dir(icy_sd)})",
    ]
    p1 = (
        f"This week brought {total_new_cnt:,} new reviews across all four brands. "
        + "; ".join(brand_lines) + "."
    )

    # Para 2: Why it matters for Arena Club
    p2_parts = []
    ac_pos_d = (ac_sd.get("pos_delta") or 0) * 100
    ac_neg_d = (ac_sd.get("neg_delta") or 0) * 100
    if ac_pos_d > 3:
        p2_parts.append(
            f"Arena Club's positive sentiment climbed {ac_pos_d:.0f} percentage points this week — "
            f"a signal that recent product changes are landing."
        )
    elif ac_neg_d > 3:
        p2_parts.append(
            f"Arena Club's negative sentiment ticked up {ac_neg_d:.0f} points this week — "
            f"find the complaint themes driving the shift and address them quickly."
        )
    else:
        p2_parts.append(
            f"Arena Club's sentiment held steady at {ac_pos}% positive this week."
        )

    struggling = []
    if cy_neg >= 50:  struggling.append(f"Courtyard ({cy_neg}% negative)")
    if icy_neg >= 50: struggling.append(f"IcyBox ({icy_neg}% negative)")
    if rbt_neg >= 40: struggling.append(f"Rips by Triumph ({rbt_neg}% negative)")
    if struggling:
        joined = " and ".join(struggling)
        verb   = "are" if len(struggling) > 1 else "is"
        p2_parts.append(
            f"{joined} {verb} generating persistently high negative rates — "
            f"these are collectors actively looking for something better, "
            f"and they represent Arena Club's most immediate growth opportunity."
        )

    # Top complaint across all competitors combined
    comp_totals: Counter = Counter()
    for t in complaint_themes:
        total_comp = sum(t.get(BRANDS[b]["key"], 0) for b in ["courtyard", "rbt", "icybox"])
        if total_comp > 0:
            comp_totals[t["name"]] += total_comp
    if comp_totals:
        top_mkt_comp = comp_totals.most_common(1)[0][0]
        p2_parts.append(
            f"The market's loudest complaint across competitors is '{top_mkt_comp}' — "
            f"exactly the kind of problem Arena Club's positioning is built to answer."
        )
    p2 = " ".join(p2_parts)

    # Para 3: What to do / our edge
    p3_parts = []
    ac_top_comp_name   = ac["top_complaints"][0]["name"]   if ac["top_complaints"]   else None
    ac_top_praise_name = ac["top_praises"][0]["name"]      if ac["top_praises"]      else None

    if ac_top_comp_name:
        p3_parts.append(
            f"The immediate priority is addressing '{ac_top_comp_name}' — "
            f"it's Arena Club's top complaint theme and should inform both product decisions "
            f"and the lead message in paid creative this week."
        )

    worst_comp = max(
        [("Courtyard", cy_neg), ("IcyBox", icy_neg), ("Rips by Triumph", rbt_neg)],
        key=lambda x: x[1]
    )
    if worst_comp[1] >= 45:
        p3_parts.append(
            f"{worst_comp[0]} at {worst_comp[1]}% negative is the most vulnerable competitor right now — "
            f"their frustrated users are a warm acquisition audience for Arena Club."
        )

    if ac_top_praise_name:
        p3_parts.append(
            f"On the positive side, '{ac_top_praise_name}' is what satisfied Arena Club users are "
            f"actually saying — that language belongs in ads, in the App Store listing, "
            f"and in any content that a potential new user might encounter."
        )

    if not p3_parts:
        p3_parts.append(
            "Sentiment is stable across all four brands this week. "
            "Maintain current creative mix and continue building review volume — "
            "AC's review count remains a key competitive gap to close."
        )
    p3 = " ".join(p3_parts)

    weekly_analysis = {
        "paragraph_1": p1,
        "paragraph_2": p2,
        "paragraph_3": p3,
    }

    return {
        "week":             f"{wk_start}–{wk_end}",
        "total_new":        sum(len(new_by_brand[b]) for b in BRAND_ORDER),
        "arena_club":       ac,
        "competitors":      comps,
        "weekly_analysis":  weekly_analysis,
    }


def generate_insights(reviews: list, ratings: dict, sentiment: dict,
                      complaint_themes: list, praise_themes: list) -> dict:
    """Generate the restructured Insights page data:
    weekly_momentum (last 5 AC weeks), edge_report, competitor_map,
    audience_segments, advertising_brief.
    """
    from datetime import date, timedelta

    by_brand = defaultdict(list)
    new_by_brand = defaultdict(list)
    for r in reviews:
        bid = r.get("brand")
        if bid in BRAND_ORDER:
            by_brand[bid].append(r)
            if r.get("is_new"):
                new_by_brand[bid].append(r)

    today      = date.today()
    wk_start   = (today - timedelta(days=6)).strftime("%b %-d")
    wk_end     = today.strftime("%b %-d, %Y")
    week_label = f"{wk_start}–{wk_end}"

    # ── Helpers ──────────────────────────────────────────────────────────────
    def bkey(bid): return BRANDS[bid]["key"]

    def top_themes_for(brand_id, theme_list, n=3):
        k = bkey(brand_id)
        return [
            {"name": t["name"], "id": t["id"], "count": t.get(k, 0)}
            for t in sorted(theme_list, key=lambda t: t.get(k, 0), reverse=True)
            if t.get(k, 0) > 0
        ][:n]

    def pct(num, total): return round(num / total * 100) if total else 0

    # ── 1. Weekly Momentum (Arena Club, last 5 weeks) ────────────────────────
    ac_reviews = by_brand["arena-club"]
    # Bucket by Mon-Sun week
    week_buckets: dict = defaultdict(list)
    for r in ac_reviews:
        d_str = r.get("date", "")
        if not d_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        monday = d - timedelta(days=d.weekday())
        week_buckets[monday].append(r)

    sorted_weeks = sorted(week_buckets.keys(), reverse=True)[:5]

    complaint_ids = {t["id"] for t in complaint_themes}
    praise_ids    = {t["id"] for t in praise_themes}
    all_names     = {t["id"]: t["name"] for t in complaint_themes + praise_themes}

    weekly_momentum = []
    for mon in sorted_weeks:
        wbrs   = week_buckets[mon]
        sun    = mon + timedelta(days=6)
        total  = len(wbrs)
        pos    = sum(1 for r in wbrs if r.get("sentiment") == "positive")
        neg    = sum(1 for r in wbrs if r.get("sentiment") == "negative")
        pos_p  = pct(pos, total)
        neg_p  = pct(neg, total)
        new_ct = sum(1 for r in wbrs if r.get("is_new"))

        c_cnt: Counter = Counter()
        p_cnt: Counter = Counter()
        for r in wbrs:
            for t in (r.get("themes") or []):
                if t in complaint_ids: c_cnt[t] += 1
                if t in praise_ids:   p_cnt[t] += 1

        top_c = [{"name": all_names.get(k, k), "count": v}
                 for k, v in c_cnt.most_common(3)]
        top_p = [{"name": all_names.get(k, k), "count": v}
                 for k, v in p_cnt.most_common(3)]

        # One-liner summary
        if pos_p >= 60:
            mood = "strong week for AC"
        elif neg_p >= 55:
            mood = "challenging week for AC"
        else:
            mood = "mixed week for AC"
        top_c_name = top_c[0]["name"] if top_c else None
        top_p_name = top_p[0]["name"] if top_p else None
        summary_parts = [f"{total} reviews — {pos_p}% positive, {neg_p}% negative."]
        if top_c_name:
            summary_parts.append(f"Top complaint: {top_c_name}.")
        if top_p_name:
            summary_parts.append(f"Top praise: {top_p_name}.")
        summary = " ".join(summary_parts)

        weekly_momentum.append({
            "week":         f"{mon.strftime('%b %-d')}–{sun.strftime('%b %-d')}",
            "week_full":    f"{mon.strftime('%b %-d')}–{sun.strftime('%b %-d, %Y')}",
            "total":        total,
            "new_reviews":  new_ct,
            "pos_pct":      pos_p,
            "neg_pct":      neg_p,
            "top_complaints": top_c,
            "top_praises":    top_p,
            "summary":      summary,
        })

    # ── 2. Edge Report ────────────────────────────────────────────────────────
    ac_k = bkey("arena-club")
    comp_keys = {bid: bkey(bid) for bid in ["courtyard", "rbt", "icybox"]}
    comp_names = {bid: BRANDS[bid]["name"] for bid in ["courtyard", "rbt", "icybox"]}

    # Strengths: praise themes where AC leads
    strengths = []
    for t in sorted(praise_themes, key=lambda x: x.get(ac_k, 0), reverse=True):
        ac_cnt = t.get(ac_k, 0)
        if ac_cnt == 0:
            continue
        ac_total_for_theme = len(by_brand["arena-club"])
        ac_rate = ac_cnt / ac_total_for_theme * 100 if ac_total_for_theme else 0

        comp_rates = {}
        for bid, ck in comp_keys.items():
            bt = len(by_brand[bid])
            comp_rates[bid] = t.get(ck, 0) / bt * 100 if bt else 0

        best_comp_rate = max(comp_rates.values()) if comp_rates else 0
        best_comp_name = max(comp_rates, key=comp_rates.get) if comp_rates else ""

        gap_pp = round(ac_rate - best_comp_rate, 1)
        vs_text = (
            f"AC rate: {ac_rate:.1f}% vs {comp_names.get(best_comp_name,'')}: {best_comp_rate:.1f}% "
            f"(+{gap_pp}pp gap)" if gap_pp > 0
            else f"AC rate: {ac_rate:.1f}% — competitors at similar level"
        )

        strengths.append({
            "name":         t["name"],
            "id":           t["id"],
            "count":        ac_cnt,
            "ac_rate":      round(ac_rate, 1),
            "vs_competitors": vs_text,
            "gap_pp":       gap_pp,
            "is_unique_edge": gap_pp >= 2,
        })

    # Weaknesses: complaint themes where AC has volume
    weaknesses = []
    ac_total_reviews = len(by_brand["arena-club"])
    for t in sorted(complaint_themes, key=lambda x: x.get(ac_k, 0), reverse=True):
        ac_cnt = t.get(ac_k, 0)
        if ac_cnt == 0:
            continue
        ac_rate = ac_cnt / ac_total_reviews * 100 if ac_total_reviews else 0

        comp_rates = {}
        for bid, ck in comp_keys.items():
            bt = len(by_brand[bid])
            comp_rates[bid] = t.get(ck, 0) / bt * 100 if bt else 0
        avg_comp_rate = sum(comp_rates.values()) / len(comp_rates) if comp_rates else 0

        if ac_rate >= avg_comp_rate * 0.8:
            severity = "high"
        elif ac_rate >= avg_comp_rate * 0.4:
            severity = "medium"
        else:
            severity = "low"

        vs_text = (
            f"AC: {ac_rate:.1f}% vs market avg: {avg_comp_rate:.1f}%"
        )

        weaknesses.append({
            "name":        t["name"],
            "id":          t["id"],
            "count":       ac_cnt,
            "ac_rate":     round(ac_rate, 1),
            "severity":    severity,
            "vs_competitors": vs_text,
        })

    edge_report = {
        "strengths":  strengths[:6],
        "weaknesses": weaknesses[:6],
    }

    # ── 3. Competitor Vulnerability Map ──────────────────────────────────────
    comp_colors = {
        "courtyard": "#5B8DD9",
        "rbt":       "#E8823A",
        "icybox":    "#9B59B6",
    }
    competitor_map = []
    for bid in ["courtyard", "rbt", "icybox"]:
        ck    = bkey(bid)
        blist = by_brand[bid]
        btotal = len(blist)
        neg_ct = sum(1 for r in blist if r.get("sentiment") == "negative")
        pos_ct = sum(1 for r in blist if r.get("sentiment") == "positive")
        neg_p  = pct(neg_ct, btotal)
        pos_p  = pct(pos_ct, btotal)

        # Top 5 complaint themes
        top5_comp = [
            {"name": t["name"], "id": t["id"], "count": t.get(ck, 0),
             "rate": round(t.get(ck, 0) / btotal * 100, 1) if btotal else 0}
            for t in sorted(complaint_themes, key=lambda x: x.get(ck, 0), reverse=True)
            if t.get(ck, 0) > 0
        ][:5]

        # Top 3 praise themes
        top3_praise = [
            {"name": t["name"], "id": t["id"], "count": t.get(ck, 0)}
            for t in sorted(praise_themes, key=lambda x: x.get(ck, 0), reverse=True)
            if t.get(ck, 0) > 0
        ][:3]

        # Where AC directly wins on each competitor's top complaint
        ac_advantages = []
        for comp_t in top5_comp[:3]:
            ac_cnt_for_theme = next(
                (t.get(ac_k, 0) for t in complaint_themes if t["id"] == comp_t["id"]), 0
            )
            ac_rate_for_theme = (
                ac_cnt_for_theme / ac_total_reviews * 100 if ac_total_reviews else 0
            )
            comp_rate = comp_t["rate"]
            if ac_rate_for_theme < comp_rate * 0.5:
                ac_advantages.append(
                    f"{comp_t['name']}: {comp_names[bid]} {comp_rate:.1f}% vs AC {ac_rate_for_theme:.1f}%"
                )

        opp_size = "large" if neg_ct > 200 else ("medium" if neg_ct > 80 else "small")

        # Find a real quote from a negative review of this competitor
        neg_reviews = [r for r in blist if r.get("sentiment") == "negative"
                       and len((r.get("body") or "")) > 40]
        quote_text = ""
        if neg_reviews:
            import random as _random
            sample = sorted(neg_reviews, key=lambda r: -len(r.get("body", "") or ""))[:10]
            q = sample[0]
            quote_text = (q.get("body") or "")[:180].strip()

        competitor_map.append({
            "brand":           BRANDS[bid]["name"],
            "brand_id":        bid,
            "color":           comp_colors[bid],
            "short":           BRANDS[bid]["short"],
            "total_reviews":   btotal,
            "neg_count":       neg_ct,
            "neg_pct":         neg_p,
            "pos_pct":         pos_p,
            "top_complaints":  top5_comp,
            "top_praises":     top3_praise,
            "ac_advantages":   ac_advantages,
            "opportunity_size": opp_size,
            "quote":           quote_text,
        })

    # ── 4. Audience Segments ─────────────────────────────────────────────────
    # Segment 1: Displaced competitor users (negative reviews on competitors)
    total_comp_neg = sum(
        sum(1 for r in by_brand[bid] if r.get("sentiment") == "negative")
        for bid in ["courtyard", "rbt", "icybox"]
    )
    # Segment 2: Loyal AC fans (positive AC reviewers)
    ac_pos_ct = sum(1 for r in by_brand["arena-club"] if r.get("sentiment") == "positive")
    # Segment 3: Android gap (RBT is iOS only; RBT positive reviewers who might move to Android)
    rbt_pos = sum(1 for r in by_brand["rbt"] if r.get("sentiment") == "positive")
    # Segment 4: Trust-seekers (negative reviews mentioning scam/trust across all brands)
    trust_comp_ids = {t["id"] for t in complaint_themes if "trust" in t["id"] or "scam" in t["id"].lower()}
    trust_neg_ct = sum(
        1 for r in reviews
        if r.get("brand") in ["courtyard", "rbt", "icybox"]
        and any(t in trust_comp_ids for t in (r.get("themes") or []))
    )

    segments = [
        {
            "name":              "Displaced Competitor Users",
            "color":             "#f87171",
            "bg":                "rgba(248,113,113,.05)",
            "size":              total_comp_neg,
            "size_label":        f"{total_comp_neg:,} negative competitor reviews",
            "who_they_are":      "Users who tried Courtyard, Rips, or IcyBox and left disappointed. Already convinced they need a new platform — not cold traffic.",
            "what_data_shows":   f"{total_comp_neg:,} total negative competitor reviews in archive. Courtyard and IcyBox both hold ~51% negative rates. These users are actively venting, which means they are actively considering alternatives.",
            "where_from":        "App Store reviews, Google Play reviews, Reddit threads about competitor apps.",
            "pain_point":        "Scam suspicions, broken customer service, and poor value/odds — the exact complaints that dominate competitor review data.",
            "ac_fit":            "Strong. AC's verifiable ratings, real CS response times, and Slab Safe guarantee directly address every pain point in this segment.",
        },
        {
            "name":              "Loyal Arena Club Advocates",
            "color":             "#22c55e",
            "bg":                "rgba(34,197,94,.05)",
            "size":              ac_pos_ct,
            "size_label":        f"{ac_pos_ct:,} positive AC reviews",
            "who_they_are":      "Existing AC users who are already happy — a referral engine and social proof asset. Not a growth segment but a retention and amplification one.",
            "what_data_shows":   f"{ac_pos_ct:,} positive AC reviews. These users have expressed satisfaction in writing — the highest-intent source of testimonials and UGC.",
            "where_from":        "Existing AC user base. In-app review prompts, App Store, Google Play.",
            "pain_point":        "Risk of churn if issues are not addressed. Need acknowledgment and continued product quality.",
            "ac_fit":            "Perfect — already converted. Value is in re-engagement, referral programs, and extracting testimonials for paid creative.",
        },
        {
            "name":              "Android Card Collectors",
            "color":             "#5B8DD9",
            "bg":                "rgba(91,141,217,.05)",
            "size":              rbt_pos,
            "size_label":        f"{rbt_pos:,} Rips positive reviewers (iOS only gap)",
            "who_they_are":      "Card collectors who want to rip but are locked out of Rips by Triumph (iOS only). Android users are a completely unserved market for the rip-and-reveal format.",
            "what_data_shows":   f"Rips by Triumph has {rbt_pos:,} positive reviews but zero Android presence. Every Android user who wants what RBT offers has no alternative except Arena Club.",
            "where_from":        "Google Play store searches, Android card collector communities, Reddit r/tradingcards.",
            "pain_point":        "No viable rip-and-reveal app on Android. RBT's iOS-only stance leaves this audience completely unsatisfied.",
            "ac_fit":            "Very strong. AC has an Android app — this is a structural platform gap AC can own outright.",
        },
        {
            "name":              "Trust-First Buyers",
            "color":             "#E8823A",
            "bg":                "rgba(232,130,58,.05)",
            "size":              trust_neg_ct,
            "size_label":        f"{trust_neg_ct:,} scam/trust complaints across competitors",
            "who_they_are":      "Users who researched other rip apps and saw the scam/fraud reviews before downloading. Skeptical by nature — legitimacy and proof are the only things that move them.",
            "what_data_shows":   f"{trust_neg_ct:,} reviews across competitors mention scam or fraud concerns. This is the single biggest complaint cluster in the entire market.",
            "where_from":        "App Store review research, Reddit due-diligence posts, Google 'is [app] legit' searches.",
            "pain_point":        "Industry-wide trust deficit. They've seen the scam accusations and won't install without proof of legitimacy.",
            "ac_fit":            "Strong — IF AC leads with verifiable proof: real rating count, operating history, BBB standing, Slab Safe guarantee. Claims without evidence won't work on this segment.",
        },
    ]

    # ── 5. Advertising Brief ─────────────────────────────────────────────────
    total_comp_reviews = sum(len(by_brand[bid]) for bid in ["courtyard", "rbt", "icybox"])
    total_comp_neg_all = sum(
        sum(1 for r in by_brand[bid] if r.get("sentiment") == "negative")
        for bid in ["courtyard", "rbt", "icybox"]
    )

    # Opportunities = places where AC can win based on data
    opportunities = []

    # Opp 1: Competitor trust gap
    trust_total = sum(
        t.get(bkey(bid), 0)
        for t in complaint_themes if "trust" in t["id"]
        for bid in ["courtyard", "rbt", "icybox"]
    )
    if trust_total > 0:
        opportunities.append({
            "label":       "Trust Deficit Across Entire Market",
            "description": f"{trust_total:,} trust/scam complaints across Courtyard, Rips, and IcyBox. The market is primed for a legitimacy play — any brand that can prove it is not a scam owns this positioning.",
            "size":        "large",
            "ac_angle":    "Verifiable ratings, Slab Safe, operating history, BBB.",
        })

    # Opp 2: Android gap
    rbt_total = len(by_brand["rbt"])
    opportunities.append({
        "label":       "Android Platform Gap (Rips by Triumph is iOS only)",
        "description": f"Rips by Triumph has {rbt_total:,} reviews and zero Android presence. Every Android card collector seeking a rip-and-reveal app has nowhere else to go.",
        "size":        "large",
        "ac_angle":    "AC's Android app is the only viable option for this audience.",
    })

    # Opp 3: CS gap (Courtyard)
    cy_cs_cnt = next((t.get("cy", 0) for t in complaint_themes if t["id"] == "cs-bad"), 0)
    if cy_cs_cnt > 0:
        opportunities.append({
            "label":       f"Courtyard Customer Service Collapse ({cy_cs_cnt} complaints)",
            "description": f"Courtyard has {cy_cs_cnt} CS complaint reviews. Their support is consistently called out as unresponsive. Users actively looking for alternatives cite this as their reason for leaving.",
            "size":        "medium",
            "ac_angle":    "AC's response times and support quality are a provable differentiator.",
        })

    # Opp 4: IcyBox audience mismatch
    icy_total = len(by_brand["icybox"])
    icy_neg_p = sentiment["icybox"]["neg"]
    if icy_neg_p >= 45:
        opportunities.append({
            "label":       f"IcyBox Mixed Audience ({icy_neg_p}% negative)",
            "description": f"IcyBox serves both watch and card collectors — a confused value proposition that leaves card buyers feeling misplaced. {icy_neg_p}% of their reviews are negative.",
            "size":        "medium",
            "ac_angle":    "AC is purpose-built for cards, with no identity confusion.",
        })

    # Proof points
    ac_rat = ratings.get("arena-club", {})
    as_r   = ac_rat.get("appstore", {})
    as_rating = as_r.get("rating", "?")
    as_count  = as_r.get("count", "?")
    ac_neg_p  = sentiment["arena-club"]["neg"]
    ac_pos_p  = sentiment["arena-club"]["pos"]

    proof_points = [
        f"Arena Club: {as_rating}★ App Store across {as_count} verified ratings",
        f"{ac_pos_p}% positive sentiment vs {ac_neg_p}% negative (vs 51%+ negative for Courtyard and IcyBox)",
        f"{total_comp_neg_all:,} negative competitor reviews = {total_comp_neg_all:,} unsatisfied users who have already tried the alternatives",
        f"Rips by Triumph is iOS-only — Android card collectors have no competitor to turn to except AC",
        f"Slab Safe guarantee directly addresses the market's #1 complaint: not worth the money / poor value",
    ]

    advertising_brief = {
        "total_addressable_neg":  total_comp_neg_all,
        "total_comp_reviews":     total_comp_reviews,
        "opportunities":          opportunities,
        "proof_points":           proof_points,
    }

    return {
        "week":               week_label,
        "updated":            today.isoformat(),
        "weekly_momentum":    weekly_momentum,
        "edge_report":        edge_report,
        "competitor_map":     competitor_map,
        "audience_segments":  segments,
        "advertising_brief":  advertising_brief,
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
                "themes":       r.get("themes", []),
                "theme_reasons": r.get("theme_reasons", {}),
            }, ensure_ascii=False)
        )
    return "[\n" + ",\n".join(lines) + "\n]"


def scrape_appstore_data() -> dict:
    """
    Fetch each App Store page and extract:
      - chart position + category
      - live rating count string (e.g. "4.9K")
      - live average rating
    Returns {brand_id: {"chart":"#165","category":"Shopping","count":"4.9K","rating":4.5}}
    """
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    results = {}
    for bid, url in APP_STORE_URLS.items():
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            entry = {}

            # Chart position
            m = re.search(r'"position"\s*:\s*(\d+)', html)
            cat_m = re.search(r'"genre(?:Name)?"\s*:\s*"([^"]+)"', html)
            if m:
                entry["chart"]    = f"#{m.group(1)}"
                entry["category"] = cat_m.group(1) if cat_m else ""

            # Rating count — JSON-LD aggregateRating.ratingCount
            count_m = re.search(r'"ratingCount"\s*:\s*(\d+)', html)
            if count_m:
                n = int(count_m.group(1))
                if n >= 1_000_000:
                    entry["count"] = f"{n/1_000_000:.1f}M"
                elif n >= 1_000:
                    entry["count"] = f"{n/1000:.1f}K"
                else:
                    entry["count"] = str(n)

            # Average rating — JSON-LD aggregateRating.ratingValue
            rating_m = re.search(r'"ratingValue"\s*:\s*([\d.]+)', html)
            if rating_m:
                entry["rating"] = round(float(rating_m.group(1)), 1)

            if entry:
                results[bid] = entry
                print(f"  {bid}: {entry.get('chart','?')} {entry.get('category','')} | "
                      f"{entry.get('rating','?')}★ {entry.get('count','?')} ratings")
            else:
                print(f"  {bid}: no data found in page HTML")

        except Exception as e:
            print(f"  {bid}: fetch failed — {e}")
        time.sleep(1.5)
    return results


# Keep old name as alias for backward compat
def scrape_chart_positions() -> dict:
    return scrape_appstore_data()


def scrape_google_play_data() -> dict:
    """
    Fetch live Google Play ratings for each brand (IcyBox excluded — iOS only).
    Returns {brand_id: {"rating": 4.4, "count": "1.5K", "installs": "100K+"}}
    Requires: pip install google-play-scraper
    """
    try:
        from google_play_scraper import app as gp_app
    except ImportError:
        print("  ⚠️  google-play-scraper not installed — skipping Google Play ratings")
        print("       Run: pip install google-play-scraper")
        return {}

    results = {}
    for brand_id, package in GOOGLE_PLAY_PACKAGES.items():
        try:
            info = gp_app(package, lang="en", country="us")
            rating       = round(info.get("score", 0) or 0, 1)
            rating_count = info.get("ratings", 0) or 0
            installs     = info.get("realInstalls", 0) or 0

            # Format rating count
            if rating_count >= 1_000_000:
                count_str = f"{rating_count/1_000_000:.1f}M"
            elif rating_count >= 1_000:
                count_str = f"{rating_count/1000:.1f}K"
            else:
                count_str = str(rating_count)

            # Format install bucket
            if installs >= 1_000_000:
                installs_str = f"{installs//1_000_000}M+"
            elif installs >= 100_000:
                installs_str = f"{(installs//100_000)*100}K+"
            elif installs >= 10_000:
                installs_str = f"{(installs//10_000)*10}K+"
            elif installs >= 1_000:
                installs_str = f"{installs//1_000}K+"
            else:
                installs_str = str(installs) if installs else "?"

            results[brand_id] = {
                "rating":   rating,
                "count":    count_str,
                "installs": installs_str,
            }
            print(f"  {brand_id}: {rating}★ {count_str} ratings | {installs_str} installs")
        except Exception as e:
            print(f"  {brand_id}: Google Play fetch failed — {e}")
        time.sleep(1.5)

    return results


def update_chart_positions(html: str, charts: dict) -> str:
    """Patch the STORE_META const in the HTML with fresh chart positions."""
    if not charts:
        return html
    for bid, data in charts.items():
        chart = data.get("chart", "—")
        cat   = data.get("category", "")
        # Replace "chart": "..." and "category": "..." for this brand's appstore entry
        # We look for the brand key then replace within a reasonable window
        pattern = (
            rf'("{re.escape(bid)}"[\s\S]{{0,300}}"appstore"\s*:\s*\{{[^}}]*?"chart"\s*:\s*")([^"]*)'
        )
        html = re.sub(pattern, lambda m: m.group(0).replace(m.group(2), chart), html)
        pattern2 = (
            rf'("{re.escape(bid)}"[\s\S]{{0,400}}"appstore"\s*:\s*\{{[^}}]*?"category"\s*:\s*")([^"]*)'
        )
        html = re.sub(pattern2, lambda m: m.group(0).replace(m.group(2), cat), html)
    return html


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

    # Load dynamic themes (auto-discovered from previous runs) and merge into globals
    if DYNAMIC_THEMES_FILE.exists():
        try:
            _dyn = json.loads(DYNAMIC_THEMES_FILE.read_text())
            _dyn_c = _dyn.get("complaint", [])
            _dyn_p = _dyn.get("praise", [])
            if _dyn_c:
                _ins = next((i for i, t in enumerate(COMPLAINT_THEMES)
                             if t["id"] == _CATCHALL_COMPLAINT), len(COMPLAINT_THEMES) - 1)
                for t in reversed(_dyn_c):
                    COMPLAINT_THEMES.insert(_ins, t)
                ALL_THEMES[:] = COMPLAINT_THEMES + PRAISE_THEMES
                print(f"  Loaded {len(_dyn_c)} dynamic complaint themes from previous runs")
            if _dyn_p:
                _ins = next((i for i, t in enumerate(PRAISE_THEMES)
                             if t["id"] == _CATCHALL_PRAISE), len(PRAISE_THEMES) - 1)
                for t in reversed(_dyn_p):
                    PRAISE_THEMES.insert(_ins, t)
                ALL_THEMES[:] = COMPLAINT_THEMES + PRAISE_THEMES
                print(f"  Loaded {len(_dyn_p)} dynamic praise themes from previous runs")
        except Exception as _e:
            print(f"  ⚠️  Could not load dynamic themes: {_e}")

    # Load
    print("\nLoading archive...")
    reviews = load_archive()
    if not reviews:
        print("  No reviews — aborting. Run run_weekly.py first.")
        return
    print(f"  {len(reviews)} reviews loaded")

    print(f"  Using full archive — all {len(reviews)} reviews (no rolling window)")

    # ── Star-based sentiment override ──────────────────────────────────────────
    # 4-5 stars = positive, 3 stars = neutral, 1-2 stars = negative
    # This overrides any AI-classified sentiment to keep it consistent and simple
    def star_sentiment(stars):
        if stars is None: return None
        if stars >= 4: return "positive"
        if stars == 3: return "neutral"
        return "negative"  # 1-2 stars

    overridden = 0
    for r in reviews:
        stars = r.get("stars")
        if stars is not None:
            new_sent = star_sentiment(int(stars))
            if new_sent != r.get("sentiment"):
                r["sentiment"] = new_sent
                overridden += 1
    print(f"  Sentiment override: {overridden} reviews reclassified by star rating")
    # ──────────────────────────────────────────────────────────────────────────

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

    # Scrape live App Store data (chart positions + live ratings/counts)
    # Must happen BEFORE injecting RATINGS so live counts go into the dashboard
    print("\nScraping live App Store data...")
    charts = scrape_appstore_data()
    if charts:
        for bid, cdata in charts.items():
            if bid in ratings:
                if "count" in cdata:
                    ratings[bid]["appstore"]["count"] = cdata["count"]
                if "rating" in cdata:
                    ratings[bid]["appstore"]["rating"] = cdata["rating"]
        print(f"  Live App Store data applied for {len(charts)} brands")
    else:
        print("  Could not fetch App Store data — keeping fallback values")

    # Scrape live Google Play data (ratings + install count)
    print("\nScraping live Google Play data...")
    gp_data = scrape_google_play_data()
    if gp_data:
        for bid, gdata in gp_data.items():
            if bid not in ratings:
                continue
            if ratings[bid].get("google") is None:
                ratings[bid]["google"] = {}
            if "count" in gdata:
                ratings[bid]["google"]["count"] = gdata["count"]
            if "rating" in gdata:
                ratings[bid]["google"]["rating"] = gdata["rating"]
            if "installs" in gdata:
                ratings[bid]["installs"] = gdata["installs"]  # top-level field used by dashboard
        print(f"  Live Google Play data applied for {len(gp_data)} brands")
    else:
        print("  Could not fetch Google Play data — keeping fallback values")

    # Read dashboard
    print(f"\nReading {DASHBOARD.name}...")
    html = DASHBOARD.read_text(encoding="utf-8")

    # Inject all constants
    today = date.today()
    today_iso = today.isoformat()
    all_dates = [r.get("date","") for r in reviews if r.get("date","")]
    earliest_iso = min(all_dates) if all_dates else today_iso
    earliest_dt  = date.fromisoformat(earliest_iso)
    window_label = f"{earliest_dt.strftime('%b %-d')} – {today.strftime('%b %-d')}"
    html = re.sub(r'const DATA_DATE\s*=\s*"[^"]*";', f'const DATA_DATE = "{today_iso}";', html)
    html = re.sub(r'const DATA_WINDOW\s*=\s*"[^"]*";', f'const DATA_WINDOW = "{window_label}";', html)
    today = today_iso  # keep rest of code consistent
    html = inject(html, "INSIGHTS",              js_insights(insights))
    html = inject(html, "REVIEWS",               js_reviews(reviews_sorted))
    html = inject(html, "RATINGS",               js_ratings(ratings))
    html = inject(html, "SENTIMENT",             js_sentiment(sentiment))
    html = inject(html, "COMPLAINT_THEMES",      js_themes(complaint_themes))
    html = inject(html, "PRAISE_THEMES",         js_themes(praise_themes))
    html = inject(html, "WEEKLY_DIGEST",         js_weekly_digest(digest))
    timeline          = compute_timeline(reviews)
    monthly_timeline  = compute_monthly_timeline(reviews)
    quarterly_timeline = compute_quarterly_timeline(reviews)
    html = inject(html, "TIMELINE_DATA",          json.dumps(timeline,           ensure_ascii=False))
    html = inject(html, "MONTHLY_TIMELINE_DATA",  json.dumps(monthly_timeline,   ensure_ascii=False))
    html = inject(html, "QUARTERLY_TIMELINE_DATA", json.dumps(quarterly_timeline, ensure_ascii=False))
    # Theme metadata for JS tooltip rendering (id + name + type)
    complaint_meta = [{"id": t["id"], "name": t["name"]} for t in COMPLAINT_THEMES]
    praise_meta    = [{"id": t["id"], "name": t["name"]} for t in PRAISE_THEMES]
    html = inject(html, "COMPLAINT_THEME_META",  json.dumps(complaint_meta, ensure_ascii=False))
    html = inject(html, "PRAISE_THEME_META",     json.dumps(praise_meta,    ensure_ascii=False))

    # Update digest header meta line
    new_count = sum(1 for r in reviews if r.get("is_new", False))
    week_label = insights["week"]
    meta_text = (f"{week_label} · {new_count} new reviews across 4 brands"
                 if new_count else
                 f"{week_label} · {len(reviews)} total reviews on file")
    html = re.sub(r'id="digestMeta">[^<]*<', f'id="digestMeta">{meta_text}<', html)

    # Patch STORE_META with fresh chart positions
    if charts:
        html = update_chart_positions(html, charts)
        print(f"  Chart positions updated for {len(charts)} brands")

    # Write back
    DASHBOARD.write_text(html, encoding="utf-8")

    print("\n" + "=" * 55)
    print("Dashboard updated!")
    print(f"  Reviews injected : {len(reviews_sorted)}")
    print(f"  Date stamped     : {today}")
    print(f"  File             : {DASHBOARD}")
    print("=" * 55)

    # ── Push to GitHub → Vercel auto-deploys ──────────────────────────────────
    import subprocess as _sp
    def _git(*args):
        r = _sp.run(["git", *args], capture_output=True, text=True, cwd=str(HERE))
        if r.returncode != 0 and r.stderr.strip():
            print(f"  git {args[0]}: {r.stderr.strip()}")
        return r.returncode == 0

    print("\nPushing to GitHub...")
    _git("add", "dashboard.html")
    # Stage dynamic_themes.json if it exists (new themes auto-discovered)
    if DYNAMIC_THEMES_FILE.exists():
        _git("add", str(DYNAMIC_THEMES_FILE))
    commit_msg = f"Weekly refresh: {date.today().isoformat()}"
    committed = _git("commit", "-m", commit_msg)
    if not committed:
        print("  Nothing new to commit (dashboard unchanged)")
    elif _git("push", "origin", "main"):
        print("  ✓ Pushed to GitHub — Vercel will auto-deploy in ~30s")
    else:
        print("  ⚠️  Git push failed — check remote / SSH key setup")

    # ── Taxonomy gap report — new reviews that hit catch-alls ──────────────────
    _taxonomy_gap_report(reviews, DASHBOARD.parent)


def _taxonomy_gap_report(reviews: list, out_dir: "Path"):
    """
    Look at reviews added this scrape cycle (is_new=True) that landed in the
    catch-all buckets.  Extract common n-gram phrases and write a short
    taxonomy_gaps.txt alongside the dashboard so each weekly run surfaces
    patterns that might need a new theme.
    """
    import re as _re
    from collections import Counter as _Counter

    MIN_BODY = 30          # skip emoji/one-word reviews
    TOP_N    = 40          # top phrases to surface

    new_reviews = [r for r in reviews if r.get("is_new")]
    if not new_reviews:
        return   # nothing new this cycle — skip silently

    # Classify the new reviews and keep only catch-all hits
    unclassified_complaint = []
    unclassified_praise    = []
    for r in new_reviews:
        body = (r.get("body") or "").strip()
        if len(body) < MIN_BODY:
            continue
        themes, reasons = classify_themes(r)
        for t in themes:
            if reasons.get(t) == "__star_fallback__":
                if t == "overall-bad":
                    unclassified_complaint.append(r)
                else:
                    unclassified_praise.append(r)

    # Build n-gram frequency table
    _STOPS = {"the","and","for","this","that","with","have","from","you","its",
               "but","not","are","was","app","they","just","like","very","really",
               "your","been","had","get","out","can","all","when","there","what",
               "about","more","dont","did","one","its","been","just","they","from",
               "that","this","with","have","would","could","should","even","also"}

    def _phrases(r_list: list, top: int):
        counts: _Counter = _Counter()
        for r in r_list:
            words = _re.findall(r"[a-z']+", (r.get("body") or "").lower())
            for n in (2, 3, 4):
                counts.update(
                    " ".join(words[i:i+n])
                    for i in range(len(words)-n+1)
                )
        filtered = {ph: c for ph, c in counts.items()
                    if c >= 2 and not all(w in _STOPS for w in ph.split())}
        return sorted(filtered.items(), key=lambda x: -x[1])[:top]

    lines = []
    def _w(s=""):
        lines.append(s)

    _today = date.today().isoformat()
    _w("=" * 60)
    _w(f"TAXONOMY GAPS — week of {_today}")
    _w(f"New reviews this cycle: {len(new_reviews)} total, "
       f"{len(unclassified_complaint)} unclassified complaints, "
       f"{len(unclassified_praise)} unclassified praise")
    _w("=" * 60)

    if unclassified_complaint:
        _w()
        _w("── UNCLASSIFIED COMPLAINTS ──────────────────────────────")
        _w(f"Top phrases ({len(unclassified_complaint)} reviews):")
        for phrase, count in _phrases(unclassified_complaint, TOP_N):
            _w(f"  {count:>3}x  {phrase}")
        _w()
        _w("Sample reviews (most substantive):")
        for r in sorted(unclassified_complaint,
                         key=lambda x: -len(x.get("body","") or ""))[:20]:
            _w(f"  [{r.get('stars','?')}★] [{r.get('brand','')}] "
               f"{(r.get('title') or '').strip()}")
            _w(f"    {(r.get('body') or '').strip()[:250]}")
            _w()
    else:
        _w()
        _w("── UNCLASSIFIED COMPLAINTS — none this cycle ✓")

    if unclassified_praise:
        _w()
        _w("── UNCLASSIFIED PRAISE ──────────────────────────────────")
        _w(f"Top phrases ({len(unclassified_praise)} reviews):")
        for phrase, count in _phrases(unclassified_praise, TOP_N):
            _w(f"  {count:>3}x  {phrase}")
        _w()
        _w("Sample reviews (most substantive):")
        for r in sorted(unclassified_praise,
                         key=lambda x: -len(x.get("body","") or ""))[:20]:
            _w(f"  [{r.get('stars','?')}★] [{r.get('brand','')}] "
               f"{(r.get('title') or '').strip()}")
            _w(f"    {(r.get('body') or '').strip()[:250]}")
            _w()
    else:
        _w()
        _w("── UNCLASSIFIED PRAISE — none this cycle ✓")

    _w()
    _w("To add themes: share this file with Claude in the Meta Ad Final project.")
    _w("=" * 60)

    out_path = out_dir / "taxonomy_gaps.txt"
    out_path.write_text("\n".join(lines))

    total_gaps = len(unclassified_complaint) + len(unclassified_praise)
    if total_gaps:
        print(f"\n  ⚠  {total_gaps} new unclassified reviews this cycle → taxonomy_gaps.txt")
    else:
        print(f"\n  ✓  All new reviews classified — no taxonomy gaps this cycle")

    # ── Auto-discover new themes (phrase appears 5+ times in new reviews) ──────
    AUTO_MIN = 5         # minimum occurrences to auto-create a theme
    _BAD_STARTS = {"the ", "and ", "for ", "with ", "that ", "this ", "just ", "are "}

    # Collect all existing keywords so we don't duplicate
    existing_kws = set()
    for t in COMPLAINT_THEMES + PRAISE_THEMES:
        for kw in t.get("keywords", []):
            existing_kws.add(kw.lower())

    def _candidate_phrases(reviews_subset, min_count):
        counts = _Counter()
        for r in reviews_subset:
            words = _re.findall(r"[a-z']+", (r.get("body") or "").lower())
            for n in (2, 3):
                counts.update(" ".join(words[i:i+n]) for i in range(len(words)-n+1))
        return {
            ph: c for ph, c in counts.items()
            if c >= min_count
            and not all(w in _STOPS for w in ph.split())
            and ph not in existing_kws
            and not any(ph.startswith(bad) for bad in _BAD_STARTS)
            and len(ph) >= 6  # skip very short phrases
        }

    # Load or init dynamic_themes.json
    dyn_file = out_dir / "data" / "dynamic_themes.json"
    if dyn_file.exists():
        try:
            _dyn_data = json.loads(dyn_file.read_text())
        except Exception:
            _dyn_data = {"complaint": [], "praise": []}
    else:
        _dyn_data = {"complaint": [], "praise": []}

    existing_dyn_ids = {t["id"] for t in _dyn_data.get("complaint", []) + _dyn_data.get("praise", [])}
    existing_dyn_kws = set()
    for t in _dyn_data.get("complaint", []) + _dyn_data.get("praise", []):
        for kw in t.get("keywords", []):
            existing_dyn_kws.add(kw.lower())

    _today_str = date.today().isoformat().replace("-", "")
    new_complaint_themes = []
    new_praise_themes    = []

    # Complaint candidates — from low-star unclassified reviews
    if unclassified_complaint:
        for phrase, count in _candidate_phrases(unclassified_complaint, AUTO_MIN).items():
            if phrase in existing_dyn_kws:
                continue
            theme_name = "Auto: " + phrase.title()
            _id_slug   = "dyn-c-" + _re.sub(r"[^a-z0-9]+", "-", phrase)[:30].strip("-")
            if _id_slug in existing_dyn_ids:
                continue
            new_complaint_themes.append({
                "id":       _id_slug,
                "name":     theme_name,
                "keywords": [phrase],
                "source":   "auto",
                "added":    date.today().isoformat(),
                "trigger_count": count,
            })
            existing_dyn_ids.add(_id_slug)
            existing_dyn_kws.add(phrase)

    # Praise candidates — from high-star unclassified reviews
    if unclassified_praise:
        for phrase, count in _candidate_phrases(unclassified_praise, AUTO_MIN).items():
            if phrase in existing_dyn_kws:
                continue
            theme_name = "Auto: " + phrase.title()
            _id_slug   = "dyn-p-" + _re.sub(r"[^a-z0-9]+", "-", phrase)[:30].strip("-")
            if _id_slug in existing_dyn_ids:
                continue
            new_praise_themes.append({
                "id":       _id_slug,
                "name":     theme_name,
                "keywords": [phrase],
                "source":   "auto",
                "added":    date.today().isoformat(),
                "trigger_count": count,
            })
            existing_dyn_ids.add(_id_slug)
            existing_dyn_kws.add(phrase)

    if new_complaint_themes or new_praise_themes:
        _dyn_data["complaint"].extend(new_complaint_themes)
        _dyn_data["praise"].extend(new_praise_themes)
        dyn_file.write_text(json.dumps(_dyn_data, indent=2))
        total_new = len(new_complaint_themes) + len(new_praise_themes)
        print(f"  ✦  Auto-added {total_new} new theme(s) to dynamic_themes.json "
              f"({len(new_complaint_themes)} complaint, {len(new_praise_themes)} praise)")
        print(f"     They will be active on next weekly run.")


if __name__ == "__main__":
    main()
