import pytest


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch):
    """Set minimal env vars for testing."""
    monkeypatch.setenv("MCP_API_KEY", "test-api-key-1234567890abcdef")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8765")
