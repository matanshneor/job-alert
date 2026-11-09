import json
from pathlib import Path

import pytest

from jobalert.config import load_settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    path = FIXTURES / name
    return json.loads(path.read_text()) if path.suffix == ".json" else path.read_text()


class FakeHttp:
    """Returns canned responses by URL substring."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def _match(self, url: str):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected URL {url}")

    def get_json(self, url, **kwargs):
        return self._match(url)

    def get_text(self, url, **kwargs):
        return self._match(url)


@pytest.fixture
def settings():
    return load_settings()
