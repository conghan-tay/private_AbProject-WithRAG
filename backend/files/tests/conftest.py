import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django

django.setup()

import pytest
from django.core.cache import cache
from django.test import override_settings


def has_openai_secret():
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if not value:
        return False

    lowered = value.lower()
    placeholder_fragments = (
        "placeholder",
        "replace",
        "changeme",
        "your-openai-api-key",
        "sk-your",
        "<openai",
        "<your",
    )
    return not any(fragment in lowered for fragment in placeholder_fragments)


def pytest_collection_modifyitems(config, items):
    skip_openai = pytest.mark.skip(
        reason="OPENAI_API_KEY is not configured for this integration test"
    )
    for item in items:
        if "requires_openai" in item.keywords and not has_openai_secret():
            item.add_marker(skip_openai)


@pytest.fixture(autouse=True)
def isolated_generous_rate_limit():
    with override_settings(RATE_LIMIT_CALLS=1000, RATE_LIMIT_PERIOD=1):
        cache.clear()
        yield
        cache.clear()
