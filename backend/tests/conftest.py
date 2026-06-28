"""Pytest configuration and shared fixtures."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests as requiring network access"
    )
