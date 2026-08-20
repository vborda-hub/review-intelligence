#!/bin/bash
# Weekly Review Intelligence scrape + deploy
# Runs every Monday at 9am — installed via crontab

DIR="$HOME/Desktop/review-intelligence"
LOG="$DIR/cron.log"
PYTHON=$(which python3 2>/dev/null || echo "/usr/bin/python3")
EMAIL="vborda@arenaclub.com"

echo "=== Weekly run: $(date) ===" >> "$LOG"

cd "$DIR" || { echo "ERROR: directory not found" >> "$LOG"; exit 1; }

echo "Running weekly scraper..." >> "$LOG"
"$PYTHON" run_weekly.py >> "$LOG" 2>&1

echo "Rebuilding dashboard..." >> "$LOG"
"$PYTHON" update_dashboard.py >> "$LOG" 2>&1

echo "Committing and pushing..." >> "$LOG"
git add dashboard.html data/archive.json >> "$LOG" 2>&1

# Only commit if there are actual changes
if git diff --cached --quiet; then
    echo "No changes to commit." >> "$LOG"
else
    git commit -m "Weekly update: $(date '+%Y-%m-%d')" >> "$LOG" 2>&1
fi

git push origin main >> "$LOG" 2>&1
PUSH_EXIT=$?

if [ $PUSH_EXIT -eq 0 ]; then
    echo "✅ Vercel deploy triggered successfully." >> "$LOG"
else
    echo "❌ ERROR: git push failed (exit $PUSH_EXIT). Vercel NOT updated." >> "$LOG"
    # Send email alert via macOS mail
    echo "Review Intelligence weekly scrape ran on $(date '+%Y-%m-%d') but the git push to GitHub FAILED (exit code $PUSH_EXIT). Vercel has NOT been updated.

Please check the log at:
  ~/Desktop/review-intelligence/cron.log

Then manually run:
  cd ~/Desktop/review-intelligence && git push origin main

— Automated alert from weekly_cron.sh" | mail -s "⚠️ Review Intelligence: Vercel deploy FAILED $(date '+%Y-%m-%d')" "$EMAIL"
    echo "Alert email sent to $EMAIL." >> "$LOG"
fi

echo "Done." >> "$LOG"
