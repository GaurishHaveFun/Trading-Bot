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
