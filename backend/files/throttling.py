import time
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import Throttled
from rest_framework.throttling import BaseThrottle


class SlidingWindowThrottle(BaseThrottle):
    """Cache-backed per-user sliding-window request throttle."""

    cache_key_prefix = 'ratelimit'
    lock_key_prefix = 'ratelimit-lock'
    throttle_message = 'Call Limit Reached'
    lock_timeout_seconds = 1
    lock_wait_seconds = 0.25
    lock_retry_sleep_seconds = 0.005

    def allow_request(self, request, view):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return True

        with self.user_lock(user_id):
            return self.allow_request_for_user(user_id)

    @contextmanager
    def user_lock(self, user_id):
        lock_key = f'{self.lock_key_prefix}:{user_id}'
        deadline = time.monotonic() + self.lock_wait_seconds
        acquired = cache.add(lock_key, '1', timeout=self.lock_timeout_seconds)

        while not acquired and time.monotonic() < deadline:
            time.sleep(self.lock_retry_sleep_seconds)
            acquired = cache.add(lock_key, '1', timeout=self.lock_timeout_seconds)

        if not acquired:
            raise Throttled(detail=self.throttle_message)

        try:
            yield
        finally:
            cache.delete(lock_key)

    def allow_request_for_user(self, user_id):
        now = time.time()
        period = settings.RATE_LIMIT_PERIOD
        calls = settings.RATE_LIMIT_CALLS
        cache_key = f'{self.cache_key_prefix}:{user_id}'

        timestamps = cache.get(cache_key) or []
        window_start = now - period
        timestamps = [timestamp for timestamp in timestamps if timestamp > window_start]

        if len(timestamps) >= calls:
            raise Throttled(detail=self.throttle_message)

        timestamps.append(now)
        cache.set(cache_key, timestamps, timeout=max(1, int(period * 2)))
        return True
