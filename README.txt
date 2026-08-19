REVIEW INTELLIGENCE SCRAPERS — v2
==================================

FILES:
  run_weekly.py               ← Run this every Monday (or set as a Cowork task)
  app_store_scraper.py        ← App Store fetcher (called by run_weekly)
  google_play_scraper_script.py ← Google Play fetcher (called by run_weekly)
  archive.py                  ← Manages the permanent archive (called by run_weekly)
  README.txt                  ← This file

DATA THAT GETS SAVED (created automatically in data/ folder):
  data/archive.json           ← Every review ever seen. NEVER overwritten.
                                 New reviews are ADDED each week.
  data/ratings_history.json   ← Weekly rating snapshot per brand.
                                 Used for the trend charts in the dashboard.
  data/weekly/YYYY-MM-DD.json ← Only the NEW reviews from each run.

SETUP (one time only):
  pip install google-play-scraper

RUN:
  python3 run_weekly.py

IMPORTANT — MUST RUN ON YOUR COMPUTER:
  The Apple and Google APIs block requests from cloud environments.
  Set the Cowork scheduled task to "On your computer" (not the cloud).

SCHEDULE VIA COWORK:
  1. Open a new Cowork task
  2. Click "Run this task" dropdown → "On your computer"
  3. Ask Claude to: "Run the weekly review scraper at data/run_weekly.py every Monday at 8am"

APP STORE IDs (do not change):
  Arena Club:       6499444724
  Courtyard:        6748155184
  Rips by Triumph:  6751921248
  IcyBox:           6758816716

GOOGLE PLAY PACKAGES (do not change):
  Arena Club:       com.arenaclub.mobile
  Courtyard:        io.courtyard.app
  Rips by Triumph:  com.triumpharcade.tcg
  IcyBox:           NOT ON GOOGLE PLAY (iOS only)

AFTER 6 MONTHS:
  data/archive.json will hold every review going back to your first run.
  data/ratings_history.json will have 26 weekly rating data points per brand —
  enough to build a meaningful trend chart showing rating changes over time.
