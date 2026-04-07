"""FastAPI dependency injection for the API Gateway."""

import logging
from contextlib import contextmanager
from typing import Generator

import pymysql
import pymysql.cursors
from fastapi import Request

from api_gateway.config import GatewayConfig
from api_gateway.services.chain_orchestrator import ChainOrchestrator
from api_gateway.services.task_store import TaskStore
from shared.cos.client import COSClient
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)


def get_gateway(request: Request) -> TaskGateway:
    """Return the TaskGateway singleton stored in app state."""
    return request.app.state.gateway


def get_redis(request: Request):
    """Return the async Redis connection stored in app state.

    Used by anything that needs to talk to gpu/inference_worker via the
    Redis-backed InferenceClient (pose recommender, prompt optimizer, etc).
    """
    return request.app.state.redis


def get_cos_client(request: Request) -> COSClient:
    """Return the COSClient singleton stored in app state."""
    return request.app.state.cos_client


def get_chain_orchestrator(request: Request) -> ChainOrchestrator:
    """Return the ChainOrchestrator singleton stored in app state."""
    return request.app.state.chain_orchestrator


def get_config(request: Request) -> GatewayConfig:
    """Return the GatewayConfig singleton stored in app state."""
    return request.app.state.config


def get_task_store(request: Request) -> TaskStore:
    """Return the TaskStore singleton stored in app state."""
    return request.app.state.task_store


@contextmanager
def get_mysql_connection(config: GatewayConfig) -> Generator[pymysql.connections.Connection, None, None]:
    """Create a MySQL connection using DictCursor.

    Usage::

        with get_mysql_connection(config) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                rows = cur.fetchall()
    """
    conn = pymysql.connect(
        host=config.mysql_host,
        port=config.mysql_port,
        user=config.mysql_user,
        password=config.mysql_password,
        database=config.mysql_db,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    try:
        yield conn
    finally:
        conn.close()
