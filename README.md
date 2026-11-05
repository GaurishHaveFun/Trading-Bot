# Trading-Bot
Personal Trading Bot gathering information via API's and giving out suggestions.

## Live report

The screener runs automatically every day (~4pm ET) via GitHub Actions and publishes a PDF report to GitHub Pages — no install needed to view it:

- **Daily full-market screen:** https://gaurishhavefun.github.io/Trading-Bot/
- **Single-ticker report:** https://gaurishhavefun.github.io/Trading-Bot/ticker.html (updated only when you run it manually — see below)

### Running it on demand

From the **Actions** tab → **Screener** workflow → **Run workflow**:
- Leave the `ticker` box empty to re-run the full daily screen right now (updates the main page).
- Type a ticker (e.g. `AAPL`) to run just that stock (updates the `/ticker.html` page only — the daily page is untouched).

See `.github/workflows/screener.yml` for the workflow definition and `backend/docs/running.md` for what each CLI mode does under the hood.

## Yahoo snapshot push (for Render)

Yahoo blocks Render's datacenter IPs, so `/tickers/{symbol}` and `/losers` sector
data can't come from yfinance there. Instead a machine with a normal IP pushes
Yahoo `info` snapshots to the API, which stores them in SQLite
(`.cache/yahoo_snapshot.db`) and merges them under live Schwab data.

1. Pick a random secret. Set `PUSH_SECRET` on Render (dashboard) and in `backend/.env`.
2. In `backend/.env` also set `PUSH_TARGET_URL=https://trading-bot-api-ijs9.onrender.com`.
3. Test: `cd backend && uv run python scripts/push_yahoo_snapshot.py --dry-run`, then without `--dry-run`.
4. Schedule every 30 min: copy `backend/scripts/com.tradingbot.yahoo-push.plist.example`
   to `~/Library/LaunchAgents/com.tradingbot.yahoo-push.plist`, fix the paths, then
   `launchctl load ~/Library/LaunchAgents/com.tradingbot.yahoo-push.plist`.

Render's free-tier filesystem is ephemeral: snapshots vanish on every redeploy/restart
until the next push (run the script manually to refill immediately). With `PUSH_SECRET`
unset on the server, the endpoint returns 503.
