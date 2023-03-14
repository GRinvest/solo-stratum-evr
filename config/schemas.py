from typing import Optional

from pydantic import BaseModel


class General(BaseModel):
    kawpow_epoch_length: Optional[int] = 7500
    mining_address: Optional[str] = 'ASbnnzrPswEcvb2QBXxswUr5TksHx5Xno3'
    update_new_job: Optional[int] = 45


class Server(BaseModel):
    host: Optional[str] = '0.0.0.0'
    port: Optional[int] = 9755


class Coind(BaseModel):
    rpc_host: Optional[str] = '127.0.0.1'
    rpc_port: Optional[int] = 9766
    rpc_user: Optional[str] = 'User'
    rpc_password: Optional[str] = 'Password'


class Redis(BaseModel):
    host: Optional[str] = 'localhost'
    port: Optional[int] = 6379
    password: Optional[str] = 'Password'
    db: Optional[int] = 0


class Config(BaseModel):
    general: General
    server: Server
    coind: Coind
    redis: Redis
