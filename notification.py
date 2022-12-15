import asyncio

import ujson

from state import state


async def send_msg(writer: asyncio.StreamWriter,
                   method: str | None,
                   params: list | bool,
                   id_: int | None = None,
                   error: list | None = None):
    if method:
        s = ujson.dumps({'jsonrpc': '2.0', 'id': id_, 'method': method, 'params': params})
    else:
        s = ujson.dumps({'jsonrpc': '2.0', 'id': id_, 'error': error, 'result': params})
    s += '\n'
    if not writer.is_closing():
        writer.write(s.encode())
        try:
            await asyncio.wait_for(writer.drain(), timeout=10)
        except asyncio.TimeoutError:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
            if writer in state.all_sessions:
                state.all_sessions.remove(writer)
