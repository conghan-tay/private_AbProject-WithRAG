import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import Throttled
from rest_framework.throttling import BaseThrottle


class SlidingWindowThrottle(BaseThrottle):
    """Cache-backed per-user sliding-window request throttle."""

    cache_key_prefix = 'ratelimit'
    throttle_message = 'Call Limit Reached'

    def allow_request(self, request, view):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return True

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
        cache.set(cache_key, timestamps, timeout=period * 2)
        return True
