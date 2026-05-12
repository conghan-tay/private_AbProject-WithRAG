import os
import sys
import time
from pathlib import Path

import django
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.utils import setup_databases, teardown_databases
from django.urls import reverse
from rest_framework.test import APIClient

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()


TEST_DATABASE_CONFIG = None


def setup_module():
    global TEST_DATABASE_CONFIG
    TEST_DATABASE_CONFIG = setup_databases(verbosity=0, interactive=False)


def teardown_module():
    if TEST_DATABASE_CONFIG is not None:
        teardown_databases(TEST_DATABASE_CONFIG, verbosity=0)


class SlidingWindowThrottleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('file-list')
        cache.clear()
        self.addCleanup(cache.clear)

    @override_settings(RATE_LIMIT_CALLS=2, RATE_LIMIT_PERIOD=1)
    def test_third_request_in_window_returns_429(self):
        first = self.client.get(self.url, HTTP_USERID='throttle-user')
        second = self.client.get(self.url, HTTP_USERID='throttle-user')
        third = self.client.get(self.url, HTTP_USERID='throttle-user')

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert third.json() == {'detail': 'Call Limit Reached'}

    @override_settings(RATE_LIMIT_CALLS=2, RATE_LIMIT_PERIOD=0.1)
    def test_request_after_window_expires_is_allowed(self):
        first = self.client.get(self.url, HTTP_USERID='expiring-user')
        second = self.client.get(self.url, HTTP_USERID='expiring-user')
        blocked = self.client.get(self.url, HTTP_USERID='expiring-user')

        time.sleep(0.12)
        after_window = self.client.get(self.url, HTTP_USERID='expiring-user')

        assert first.status_code == 200
        assert second.status_code == 200
        assert blocked.status_code == 429
        assert after_window.status_code == 200

    @override_settings(RATE_LIMIT_CALLS=2, RATE_LIMIT_PERIOD=1)
    def test_rate_limit_is_scoped_per_user_id(self):
        first = self.client.get(self.url, HTTP_USERID='user-one')
        second = self.client.get(self.url, HTTP_USERID='user-one')
        blocked = self.client.get(self.url, HTTP_USERID='user-one')
        other_user = self.client.get(self.url, HTTP_USERID='user-two')

        assert first.status_code == 200
        assert second.status_code == 200
        assert blocked.status_code == 429
        assert other_user.status_code == 200

    @override_settings(RATE_LIMIT_CALLS=1, RATE_LIMIT_PERIOD=1)
    def test_custom_rate_limit_settings_are_honored(self):
        first = self.client.get(self.url, HTTP_USERID='custom-limit-user')
        second = self.client.get(self.url, HTTP_USERID='custom-limit-user')

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()['detail'] == 'Call Limit Reached'

    @override_settings(RATE_LIMIT_CALLS=1, RATE_LIMIT_PERIOD=1)
    def test_missing_user_id_is_rejected_by_middleware_before_throttle(self):
        first = self.client.get(self.url)
        second = self.client.get(self.url)

        assert first.status_code == 401
        assert first.json() == {'detail': 'UserId header required'}
        assert second.status_code == 401
        assert second.json() == {'detail': 'UserId header required'}
