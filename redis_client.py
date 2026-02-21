import os
import redis

REDIS_URL = {{ Redis.REDIS_URL }}

redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True
)