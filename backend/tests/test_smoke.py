"""Smoke test: verify the package imports without error."""
import screener.main


def test_import_screener_main():
    """screener.main must be importable."""
    assert hasattr(screener.main, "main")
