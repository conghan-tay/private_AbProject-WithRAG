import pytest
from django.core.cache import cache
from django.test import override_settings


@pytest.fixture(autouse=True)
def isolated_generous_rate_limit():
    with override_settings(RATE_LIMIT_CALLS=1000, RATE_LIMIT_PERIOD=1):
        cache.clear()
        yield
        cache.clear()
