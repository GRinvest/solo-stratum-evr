import aioredis
from config import config

redis = aioredis.Redis(host=config.redis.host,
                       port=config.redis.port,
                       password=config.redis.password,
                       db=config.redis.db,
                       decode_responses=True)
