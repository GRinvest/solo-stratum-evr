from time import time, time_ns
from typing import Awaitable

from aioredis import Redis
from config import config


def get_blacklist(tx: Redis.pipeline) -> Awaitable:
    return tx.smembers("blacklist")


def get_whitelist(tx: Redis.pipeline) -> Awaitable:
    return tx.smembers("whitelist")


def set_node_state(tx: Redis.pipeline, id_: str, height: int, diff: int) -> Awaitable:
    tx.hset("nodes", ':'.join([id_, "name"]), id_)
    tx.hset("nodes", ':'.join([id_, "height"]), height)
    tx.hset("nodes", ':'.join([id_, "difficulty"]), diff)
    tx.hset("nodes", ':'.join([id_, "lastBeat"]), int(time()))
    return tx


def get_node_state(tx: Redis.pipeline) -> Awaitable:
    return tx.hgetall("nodes")


async def check_pow_exist(tx: Redis.pipeline, height: int, params: list[str]):
    tx.zremrangebyscore("pow", "-inf", height - 8)
    tx.zadd("pow", {':'.join(params): height})
    _, val = await tx.execute()
    return val == 0


def write_share(
        tx: Redis,
        ms: int,
        ts: float,
        wallet: str,
        worker: str,
        diff: int,
        expire: int) -> None:
    tx.lpush("lastshares", wallet)
    tx.ltrim("lastshares", 0, 999)

    tx.hincrby(':'.join(["shares", "roundCurrent"]), wallet, diff)
    tx.zadd("hashrate", {':'.join([diff, wallet, worker, ms]): ts})
    tx.zadd(':'.join(["hashrate", wallet]), {':'.join([diff, worker, ms]): ts})
    tx.expire(':'.join(["hashrate", wallet]), expire)
    tx.hset(':'.join(["miners", wallet]), "lastShare", int(ts))


async def set_share(
        tx: Redis.pipeline,
        wallet: str,
        worker: str,
        id_: str,
        params: list[str],
        diff: int,
        height: int,
        expire: int) -> bool:
    exist = await check_pow_exist(tx, height, params)
    if exist:
        return False
    ms = time_ns()
    ts = time()
    write_share(tx, ms, ts, wallet, worker, diff, expire)
    tx.hincrby("stats", "roundShares", diff)
    await tx.execute()
    return True


async def set_block(
        tx: Redis.pipeline,
        wallet: str,
        worker: str,
        params: list[str],
        diff: int,
        node_diff: int,
        height: int,
        expire: int) -> bool:
    exist = await check_pow_exist(tx, height, params)
    if exist:
        return False

    ms = time_ns()
    ts = time()

    write_share(tx, ms, ts, wallet, worker, diff, expire)
    tx.hset("stats", "lastBlockFound", int(ts))
    tx.hdel("stats", "roundShares")
    tx.zincrby("finders", 1, ':'.join([wallet, worker]))
    tx.hincrby(':'.join(["miners", wallet]), "blocksFound", 1)
    tx.hgetall(':'.join(["shares", "roundCurrent"]))
    tx.delete(':'.join(["shares", "roundCurrent"]))
    tx.lrange("lastshares", 0, 999)
    cmds = await tx.execute()
    shares: dict = cmds[len(cmds) - 1]
    total_shares = {}
    for val in shares.values():
        total_shares[val] += 1
    for k, v in total_shares:
        tx.hincrby(':'.join(["shares", "round"+str(height), params[0]]), k, v)
    sharesMap, _ = cmds[len(cmds) - 3]


