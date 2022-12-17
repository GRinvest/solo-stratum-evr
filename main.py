import asyncio
import sys

from loguru import logger

from config import config
from job import state_updater
from server import handle_client


async def job_manager():
    while True:
        await state_updater([list(), dict()], 20)
        await asyncio.sleep(0.1)


async def run_proxy():
    server = await asyncio.start_server(
        handle_client,
        config.server.host,
        config.server.port)
    logger.success(f'Proxy server is running on port {config.server.port}')
    async with server:
        await server.serve_forever()


async def execute():
    while True:
        logger.success('Running Session Program')
        try:
            await asyncio.gather(
                asyncio.create_task(job_manager()),
                asyncio.create_task(run_proxy())
            )
        except Exception as e:
            logger.exception(e)


if __name__ == '__main__':
    logger.remove()
    logger.add(sys.stderr,
               colorize=True,
               format="{time:DD-MM-YYYY at HH:mm:ss} - <level>{message}</level>")
    logger.add("Stratum_{time}.log", rotation="10 MB", enqueue=True)

    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        pass
    finally:
        logger.warning('Closed Session Program')
