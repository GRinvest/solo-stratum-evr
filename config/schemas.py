from typing import Optional

from pydantic import BaseModel


class General(BaseModel):
    kawpow_epoch_length: Optional[int] = 12000
    mining_address: Optional[str] = 'ETTurTg48LZACY4mLF3iX3iWWoSgDN4WxU'
    update_new_job: Optional[int] = 45


class Server(BaseModel):
    host: Optional[str] = '0.0.0.0'
    port: Optional[int] = 8888


class Coind(BaseModel):
    rpc_host: Optional[str] = '127.0.0.1'
    rpc_port: Optional[int] = 8819
    rpc_user: Optional[str] = 'User'
    rpc_password: Optional[str] = 'Password'


class Config(BaseModel):
    general: General
    server: Server
    coind: Coind
