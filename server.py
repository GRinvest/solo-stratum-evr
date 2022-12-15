import asyncio
import random
from time import time

import ujson
from loguru import logger

from coindrpc import coind
from state import state
from config import config


class Proxy:
    last_time_reported_hs = 0
    hashrate_dict = {}

    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer
        self.worker = ''
        self.extra_nonce = ''
        self.block_fond = 0

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
        if self._writer not in state.all_sessions:
            state.new_sessions.add(self._writer)
        while True:
            self.extra_nonce = '%0x' % random.getrandbits(4 * 4)
            if len(self.extra_nonce) == 4:
                break
        await self.send_msg(None, [None, self.extra_nonce], msg['id'])

    async def handle_authorize(self, msg: dict):
        self.worker = msg['params'][0].split('.')[1]
        await self.send_msg(None, True, msg['id'])
        logger.success(f"Worker {self.worker} connected | ExtraNonce: {self.extra_nonce}")

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
        res = await coind.submitblock(block_hex)
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
            self.block_fond = time()
            block_height = int.from_bytes(
                bytes.fromhex(block_hex[(4 + 32 + 32 + 4 + 4) * 2:(4 + 32 + 32 + 4 + 4 + 4) * 2]), 'little',
                signed=False)
            msg_ = f'Found block (may or may not be accepted by the chain): {block_height}'
            logger.success(msg_)
            await self.send_msg(None, True, msg['id'])
            await self.send_msg('client.show_message', [msg_])

    async def handle_eth_submitHashrate(self, msg: dict):
        res = await coind.getmininginfo()
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


async def handle_client(reader, writer):
    """Создание и проверка подключения"""
    proxy = Proxy(writer)
    try:
        while not reader.at_eof():
            try:
                data = await asyncio.wait_for(reader.readline(), timeout=60 * 60)
                if not data:
                    break
                j: dict = ujson.loads(data)
            except (TimeoutError, asyncio.TimeoutError):
                if time() - proxy.block_fond < 4 * 60 * 60:
                    continue
                else:
                    break
            except Exception as e:
                print(e)
                break
            else:
                method = j.get('method')
                if method == 'mining.subscribe':
                    await proxy.handle_subscribe(j)
                elif method == 'mining.authorize':
                    await proxy.handle_authorize(j)
                elif method == 'mining.submit':
                    await proxy.handle_submit(j)
                elif method == 'eth_submitHashrate':
                    await proxy.handle_eth_submitHashrate(j)
                elif method is not None:
                    await proxy.send_msg(None, False, None, [20, f'Method {method} not supported'])
                else:
                    logger.error(j)
                    break
    except ConnectionResetError:
        pass
    except Exception as e:
        logger.error(e)
    finally:
        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()
        if writer in state.all_sessions:
            state.all_sessions.remove(writer)
        logger.warning(f"worker disconnected {proxy.worker}")


async def run_proxy():
    server = await asyncio.start_server(
        handle_client,
        config.server.host,
        config.server.port)
    logger.success(f'Proxy server is running on port {config.server.port}')
    async with server:
        await server.serve_forever()
