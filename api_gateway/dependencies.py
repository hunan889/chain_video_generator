"""FastAPI dependency injection for the API Gateway."""

from fastapi import Request

from api_gateway.services.chain_orchestrator import ChainOrchestrator
from shared.cos.client import COSClient
from shared.task_gateway import TaskGateway


def get_gateway(request: Request) -> TaskGateway:
    """Return the TaskGateway singleton stored in app state."""
    return request.app.state.gateway


def get_cos_client(request: Request) -> COSClient:
    """Return the COSClient singleton stored in app state."""
    return request.app.state.cos_client


def get_chain_orchestrator(request: Request) -> ChainOrchestrator:
    """Return the ChainOrchestrator singleton stored in app state."""
    return request.app.state.chain_orchestrator
