import asyncio
import hashlib
import random
from time import time

import base58
from loguru import logger

from coindrpc import node
from config import config
from notification import send_new_job
from state import state
from utils import op_push, var_int, merkle_from_txids, dsha256

mining_address = [
    'ETTurTg48LZACY4mLF3iX3iWWoSgDN4WxU',
    'EZE8R2zTynC2EdQGUNc2kLv37AFuBkUyzt',
    'EYmvLBq3mYCEyCk6prUj7Nru9FFQVVj7oB',
    'EPLY4ufXX5AaKiHEkEFi69CekwUnfZXEp5',
    'EMKQVCMtrbiTTjsL2yiXhyxYh4ZwE6oSCs',
    'EPHwaVYw9gNhg9PGBRbkTVtutSLigBk1pp',
    'EdauUSEdNQ5rXPskjnEF2MbQmQqxANvigH',
    'EHNWZ6VnKNnLV8xrnacpPZ28A6hvZLkpPt',
    'EM1KBTezwrTGYwz4yz2aGXvosMrAa36yQt',
    'EQoXMwUDjDgwSq6GMz8XBoDDnr68495Css'
]


async def state_updater():
    try:
        res = await node.getblocktemplate()
        json_obj = res['result']
        version_int: int = json_obj['version']
        height_int: int = json_obj['height']
        bits_hex: str = json_obj['bits']
        prev_hash_hex: str = json_obj['previousblockhash']
        txs_list: list = json_obj['transactions']
        coinbase_sats_int: int = json_obj['coinbasevalue']
        witness_hex: str = json_obj['default_witness_commitment']
        target_hex: str = json_obj['target']

        ts = int(time())
        new_witness = witness_hex != state.current_commitment
        state.current_commitment = witness_hex
        state.target = target_hex
        state.bits = bits_hex
        state.version = version_int
        state.prevHash = bytes.fromhex(prev_hash_hex)[::-1]

        new_block = False

        # The following will only change when there is a new block.
        # Force update is unnecessary
        if state.height == -1 or state.height != height_int:
            # New block, update everything
            logger.debug(f"New block {height_int - 1}, update state. New target: {target_hex}")
            new_block = True

            # Generate seed hash #
            if state.height == - 1 or height_int > state.height:
                if not state.seedHash:
                    seed_hash = bytes(32)
                    for _ in range(height_int // config.general.kawpow_epoch_length):
                        k = hashlib.new("sha3_256")
                        k.update(seed_hash)
                        seed_hash = k.digest()
                    logger.debug(f'Initialized seedhash to {seed_hash.hex()}')
                    state.seedHash = seed_hash
                elif state.height % config.general.kawpow_epoch_length == 0:
                    # Hashing is expensive, so want use the old val
                    k = hashlib.new("sha3_256")
                    k.update(state.seedHash)
                    seed_hash = k.digest()
                    logger.debug(f'updated seed hash to {seed_hash.hex()}')
                    state.seedHash = seed_hash
            elif state.height > height_int:
                # Maybe a chain reorg?

                # If the difference between heights is greater than how far we are into the epoch
                if state.height % config.general.kawpow_epoch_length - (state.height - height_int) < 0:
                    # We must go back an epoch; recalc
                    seed_hash = bytes(32)
                    for _ in range(height_int // config.general.kawpow_epoch_length):
                        k = hashlib.new("sha3_256")
                        k.update(seed_hash)
                        seed_hash = k.digest()
                    logger.debug(f'Reverted seedhash to {seed_hash}')
                    state.seedHash = seed_hash

            # Done with seed hash #
            state.height = height_int

        # The following occurs during both new blocks & new txs & nothing happens for 60s (magic number)
        if new_block or new_witness or state.timestamp + state.update_new_job < ts:
            # Generate coinbase #

            bytes_needed_sub_1 = 0
            while True:
                if state.height <= (2 ** (7 + (8 * bytes_needed_sub_1))) - 1:
                    break
                bytes_needed_sub_1 += 1

            bip34_height = state.height.to_bytes(bytes_needed_sub_1 + 1, 'little')

            # Note that there is a max allowed length of arbitrary data.
            # I forget what it is (TODO lol) but note that this string is close
            # to the max.
            arbitrary_data = b'Jonathan Livingston Seagull'
            coinbase_script = op_push(len(bip34_height)) + bip34_height + b'\0' + op_push(
                len(arbitrary_data)) + arbitrary_data
            coinbase_txin = bytes(32) + b'\xff' * 4 + var_int(len(coinbase_script)) + coinbase_script + b'\xff' * 4
            if time() - state.timestamp_block_fond > 60 * 60:
                state.address = mining_address[random.randint(0, 9)]
                state.update_new_job = 45
            else:
                state.update_new_job = 120
            vout_to_miner = b'\x76\xa9\x14' + base58.b58decode_check(state.address)[1:] + b'\x88\xac'
            vout_to_devfund = b'\xa9\x14' + base58.b58decode_check("eHNUGzw8ZG9PGC8gKtnneyMaQXQTtAUm98")[1:] + b'\x87'

            # Concerning the default_witness_commitment:
            # https://github.com/bitcoin/bips/blob/master/bip-0141.mediawiki#commitment-structure
            # Because the coinbase tx is '00'*32 in witness commit,
            # We can take what the node gives us directly without changing it
            # (This assumes that the txs are in the correct order, but I think
            # that is a safe assumption)

            witness_vout = bytes.fromhex(witness_hex)

            state.coinbase_tx = (int(1).to_bytes(4, 'little') +
                                 b'\x00\x01' +
                                 b'\x01' + coinbase_txin +
                                 b'\x03' +
                                 int(coinbase_sats_int * 0.9).to_bytes(8, 'little') + op_push(
                        len(vout_to_miner)) + vout_to_miner +
                                 int(coinbase_sats_int * 0.1).to_bytes(8, 'little') + op_push(
                        len(vout_to_devfund)) + vout_to_devfund +
                                 bytes(8) + op_push(len(witness_vout)) + witness_vout +
                                 b'\x01\x20' + bytes(32) + bytes(4))

            coinbase_no_wit = int(1).to_bytes(4, 'little') + b'\x01' + coinbase_txin + b'\x03' + \
                              int(coinbase_sats_int * 0.9).to_bytes(8, 'little') + op_push(
                len(vout_to_miner)) + vout_to_miner + \
                              int(coinbase_sats_int * 0.1).to_bytes(8, 'little') + op_push(
                len(vout_to_devfund)) + vout_to_devfund + \
                              bytes(8) + op_push(len(witness_vout)) + witness_vout + \
                              bytes(4)
            state.coinbase_txid = dsha256(coinbase_no_wit)

            # Create merkle & update txs
            txids = [state.coinbase_txid]
            incoming_txs = []
            for tx_data in txs_list:
                incoming_txs.append(tx_data['data'])
                txids.append(bytes.fromhex(tx_data['txid'])[::-1])
            state.externalTxs = incoming_txs
            merkle = merkle_from_txids(txids)

            # Done create merkle & update txs

            state.header = version_int.to_bytes(4, 'little') + state.prevHash + merkle + ts.to_bytes(4, 'little') + bytes.fromhex(bits_hex)[::-1] + state.height.to_bytes(4, 'little')

            state.headerHash = dsha256(state.header)[::-1].hex()
            state.timestamp = ts

            state.job_counter += 1

            async with state.lock:
                tasks = []
                for writer in state.all_sessions:
                    tasks.append(
                        asyncio.create_task(send_new_job(writer))
                    )
                if len(tasks):
                    await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(f'Error {e}')
        logger.error('Failed to query blocktemplate from node Sleeping for 3 sec.')
        await asyncio.sleep(3)
