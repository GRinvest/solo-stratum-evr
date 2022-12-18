import asyncio
import random
from time import time

import ujson
from loguru import logger

from coindrpc import node
from state import state
from db import redis


class Proxy:
    last_time_reported_hs = 0
    hashrate_dict = {}

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self.worker: str | None = None
        self.extra_nonce: str = ''
        self.time_block_fond: int = 0

    async def send_msg(self,
                       method: str | None,
                       params: list | bool,
                       id_: int | None = None,
                       error: list | None = None):
        if method:
            s = ujson.dumps({'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params})
        else:
            s = ujson.dumps({'jsonrpc': '2.0', 'id': id_, 'error': error, 'result': params})
        s += '\n'
        if not self._writer.is_closing():
            self._writer.write(s.encode())
            try:
                await asyncio.wait_for(self._writer.drain(), timeout=10)
            except asyncio.TimeoutError:
                if not self._writer.is_closing():
                    self._writer.close()
                    await self._writer.wait_closed()
                if self._writer in state.all_sessions:
                    state.all_sessions.remove(self._writer)

    async def handle_subscribe(self, msg: dict):
        while True:
            self.extra_nonce = '%0x' % random.getrandbits(4 * 4)
            if len(self.extra_nonce) == 4:
                break
        await self.send_msg(None, [None, self.extra_nonce], msg['id'])

    async def handle_authorize(self, msg: dict):
        self.worker = msg['params'][0].split('.')[1]
        await self.send_msg(None, True, msg['id'])
        await self.send_msg('mining.set_target', [state.target])
        await self.send_msg('mining.notify',
                            [hex(state.job_counter)[2:],
                             state.headerHash,
                             state.seedHash.hex(),
                             state.target,
                             True,
                             state.height,
                             state.bits])
        while state.lock.locked():
            await asyncio.sleep(0.01)
        state.all_sessions.add(self._writer)
        logger.success(f"Worker {self.worker} connected | ExtraNonce: {self.extra_nonce}")
        async with redis.client() as conn:
            res = await conn.get('connected_worker')
            if res:
                connected_worker = int(res) + 1
            else:
                connected_worker = 1
            await conn.set('connected_worker', connected_worker)

    async def handle_submit(self, msg: dict):

        job_id = msg['params'][1]
        nonce_hex = msg['params'][2]
        header_hex = msg['params'][3]
        mixhash_hex = msg['params'][4]

        logger.success('Possible solution')
        logger.info(self.worker)
        logger.info(job_id)
        logger.info(header_hex)

        # We can still propogate old jobs; there may be a chance that they get used
        state_ = state

        if nonce_hex[:2].lower() == '0x':
            nonce_hex = nonce_hex[2:]
        nonce_hex = bytes.fromhex(nonce_hex)[::-1].hex()
        if mixhash_hex[:2].lower() == '0x':
            mixhash_hex = mixhash_hex[2:]
        mixhash_hex = bytes.fromhex(mixhash_hex)[::-1].hex()

        block_hex = state_.build_block(nonce_hex, mixhash_hex)
        logger.info(block_hex)
        res = await node.submitblock(block_hex)
        logger.info(res)
        result = res.get('result', None)
        if result == 'inconclusive':
            # inconclusive - valid submission but other block may be better, etc.
            logger.error('Valid block but inconclusive')
        elif result == 'duplicate':
            logger.error('Valid block but duplicate')
            await self.send_msg(None, False, msg['id'], [22, 'Duplicate share'])
        elif result == 'duplicate-inconclusive':
            logger.error('Valid block but duplicate-inconclusive')
        elif result == 'inconclusive-not-best-prevblk':
            logger.error('Valid block but inconclusive-not-best-prevblk')
        elif result == 'high-hash':
            logger.error('low diff')
            await self.send_msg(None, False, msg['id'], [23, 'Low difficulty share'])
        if result not in (
                None, 'inconclusive', 'duplicate', 'duplicate-inconclusive', 'inconclusive-not-best-prevblk',
                'high-hash'):
            logger.error(res['result'])
            await self.send_msg(None, False, msg['id'], [20, res.get('result')])

        if res.get('result', 0) is None:
            self.time_block_fond = time()
            state.timestamp_block_fond = time()
            block_height = int.from_bytes(
                bytes.fromhex(block_hex[(4 + 32 + 32 + 4 + 4) * 2:(4 + 32 + 32 + 4 + 4 + 4) * 2]), 'little',
                signed=False)
            msg_ = f'Found block worker {self.worker} (may or may not be accepted by the chain): {block_height}'
            logger.success(msg_)
            await self.send_msg(None, True, msg['id'])
            await self.send_msg('client.show_message', [msg_])
            async with redis.client() as conn:
                await conn.lpush(f"block:{self.worker}", block_height)

    async def handle_eth_submitHashrate(self, msg: dict):
        res = await node.getmininginfo()
        json_obj = res['result']
        difficulty_int: int = json_obj['difficulty']
        networkhashps_int: int = json_obj['networkhashps']

        hashrate = int(msg['params'][0], 16)
        self.hashrate_dict.update({self.worker: hashrate})
        totalHashrate = 0
        for x, y in self.hashrate_dict.items():
            totalHashrate += y
        if totalHashrate != 0:
            if time() - self.last_time_reported_hs > 10 * 60:
                self.last_time_reported_hs = time()
                TTF = difficulty_int * 2 ** 32 / totalHashrate
                logger.debug(
                    f'Total Solo Pool Reported Hashrate: {round(totalHashrate / 1000000, 2)} Mh/s | Estimated time to find: {round(TTF / 60, 2)} minute')
                logger.debug(f'Network Hashrate: {round(networkhashps_int / 1000000000000, 2)} Th/s')
            TTF = difficulty_int * 2 ** 32 / hashrate
            await self.send_msg(None, True, msg['id'])
            await self.send_msg('client.show_message',
                                [f'Estimated time to find: {round(TTF / 3600, 2)} hours'])
            logger.debug(f'Worker {self.worker} Reported Hashrate: {round(hashrate / 1000000, 2)} Mh/s ')

    async def adapter_handle(self):
        while not self._reader.at_eof():
            try:
                data = await asyncio.wait_for(self._reader.readline(), timeout=60 * 60)
                if not data:
                    break
                j: dict = ujson.loads(data)
            except (TimeoutError, asyncio.TimeoutError):
                if time() - self.time_block_fond < 4 * 60 * 60:
                    continue
                else:
                    break
            except (ValueError, ConnectionResetError):
                break
            else:
                method = j.get('method')
                if method == 'mining.subscribe':
                    await self.handle_subscribe(j)
                elif method == 'mining.authorize':
                    await self.handle_authorize(j)
                elif method == 'mining.submit':
                    await self.handle_submit(j)
                elif method == 'eth_submitHashrate':
                    await self.handle_eth_submitHashrate(j)
                elif method is not None:
                    await self.send_msg(None, False, None, [20, f'Method {method} not supported'])
                else:
                    logger.error(j)
                    break


async def handle_client(reader, writer):
    """Создание и проверка подключения"""
    proxy = Proxy(reader, writer)
    try:
        await proxy.adapter_handle()
    except Exception as e:
        logger.error(e)
    finally:
        if proxy.worker:
            while state.lock.locked():
                await asyncio.sleep(0.01)
            if writer in state.all_sessions:
                state.all_sessions.remove(writer)
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
            logger.warning(f"worker disconnected {proxy.worker}")
            async with redis.client() as conn:
                res = await conn.get('connected_worker')
                if res:
                    connected_worker = int(res) - 1
                else:
                    connected_worker = 0
                await conn.set('connected_worker', connected_worker)
