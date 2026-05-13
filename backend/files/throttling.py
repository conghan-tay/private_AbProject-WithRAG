import time
import uuid
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import Throttled
from rest_framework.throttling import BaseThrottle


REDIS_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_start_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_seconds = tonumber(ARGV[5])

redis.call("ZREMRANGEBYSCORE", key, 0, window_start_ms)

local count = redis.call("ZCARD", key)
if count >= limit then
    redis.call("EXPIRE", key, ttl_seconds)
    return 0
end

redis.call("ZADD", key, now_ms, member)
redis.call("EXPIRE", key, ttl_seconds)
return 1
"""


@lru_cache(maxsize=4)
def get_redis_client(redis_url):
    from redis import Redis

    return Redis.from_url(redis_url)


class SlidingWindowThrottle(BaseThrottle):
    """Per-user sliding-window request throttle."""

    cache_key_prefix = 'ratelimit'
    throttle_message = 'Call Limit Reached'

    def allow_request(self, request, view):
        user_id = getattr(request, 'user_id', None)
        if not user_id:
            return True

        if settings.REDIS_URL:
            return self.allow_redis_request(user_id)

        return self.allow_cache_request(user_id)

    def allow_redis_request(self, user_id):
        now_ms = int(time.time() * 1000)
        period_ms = int(settings.RATE_LIMIT_PERIOD * 1000)
        window_start_ms = now_ms - period_ms
        ttl_seconds = max(1, int(settings.RATE_LIMIT_PERIOD * 2))
        cache_key = f'{self.cache_key_prefix}:{user_id}'
        member = f'{now_ms}:{uuid.uuid4().hex}'

        allowed = self.get_redis_client().eval(
            REDIS_SLIDING_WINDOW_SCRIPT,
            1,
            cache_key,
            now_ms,
            window_start_ms,
            settings.RATE_LIMIT_CALLS,
            member,
            ttl_seconds,
        )
        if int(allowed) != 1:
            raise Throttled(detail=self.throttle_message)
        return True

    def get_redis_client(self):
        return get_redis_client(settings.REDIS_URL)

    def allow_cache_request(self, user_id):
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
