# Universe Module

The `screener.universe` package defines which ticker symbols the screener processes on each run. It follows the provider pattern: a common abstract base class with concrete implementations that can be swapped without touching any other module.

## UniverseProvider (ABC)

**File:** `src/screener/universe/base.py`

`UniverseProvider` is an abstract base class with a single required method:

```python
def get_symbols(self) -> list[str]: ...
```

Any class that inherits from `UniverseProvider` and implements `get_symbols` is a valid provider. Callers (e.g., `main.py`) only depend on this interface, making it straightforward to swap data sources.

## StaticUniverse

**File:** `src/screener/universe/static.py`

Reads a list of ticker symbols from a YAML file. Intended for Phase 1 MVP usage where the universe is fixed and small.

### How it works

The constructor accepts a `path` (string or `pathlib.Path`) pointing to a YAML file. On `get_symbols()`, it opens the file, parses it with `yaml.safe_load`, and returns the `symbols` list as `list[str]`.

Expected YAML shape:

```yaml
symbols:
  - AAPL
  - MSFT
  - GOOGL
  # ...
```

### When to use it

Use `StaticUniverse` when:
- The universe is known in advance and changes rarely.
- You want deterministic, reproducible runs (no network dependency at startup).
- You are debugging or running `--ticker` mode against a small set.

The default universe file is `config/universe.yaml` (10 symbols: AAPL, MSFT, GOOGL, NVDA, META, TSLA, AMZN, JPM, V, WMT).

## SP500Universe

**File:** `src/screener/universe/sp500.py`

Fetches the full S&P 500 constituent list by scraping the Wikipedia page [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) and caches the result locally to avoid redundant network calls.

### Wikipedia scrape

`pd.read_html` is used to parse the first HTML table on the Wikipedia page, which contains the `Symbol` column. BRK.B-style symbols with a period are normalized to BRK-B (dash) to match yfinance conventions.

### Cache TTL and location

- Default cache directory: `.cache/` (relative to the working directory, i.e., `backend/.cache/`)
- Cache file: `<cache_dir>/sp500.json` — a flat JSON array of ticker strings.
- TTL: **24 hours** (86 400 seconds). The cache is considered fresh if the file's mtime is less than 24 hours old.
- On a fresh fetch, the cache directory is created automatically (`mkdir -p` equivalent).
- On a cache hit, no network request is made.

### Dot-to-dash symbol fix

Wikipedia uses periods in some symbols (e.g., `BRK.B`), but yfinance expects dashes (e.g., `BRK-B`). The `_fetch` method replaces every `.` with `-` in the `Symbol` column before returning or caching the list.

## Adding a New Provider

1. Create `src/screener/universe/<name>.py`.
2. Import and subclass `UniverseProvider`:
   ```python
   from screener.universe.base import UniverseProvider

   class MyUniverse(UniverseProvider):
       def get_symbols(self) -> list[str]:
           # your logic here
           return [...]
   ```
3. Export it from `src/screener/universe/__init__.py`.
4. Wire it into `main.py` (or config) wherever the provider is instantiated.
5. Add at least one unit test in `tests/test_universe.py`. Mock any network calls; mark live-network tests `@pytest.mark.integration`.
