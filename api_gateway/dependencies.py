"""FastAPI dependency injection for the API Gateway."""

from fastapi import Depends, Request

from shared.cos.client import COSClient
from shared.task_gateway import TaskGateway


def get_gateway(request: Request) -> TaskGateway:
    """Return the TaskGateway singleton stored in app state."""
    return request.app.state.gateway


def get_cos_client(request: Request) -> COSClient:
    """Return the COSClient singleton stored in app state."""
    return request.app.state.cos_client
