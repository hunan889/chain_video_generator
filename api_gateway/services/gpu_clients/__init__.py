"""Gateway-side Redis clients for GPU services on 148.

All GPU work goes through Redis queues consumed by gpu/comfyui_worker
or gpu/inference_worker. This package provides the client APIs that
gateway business logic uses to submit work and wait for results,
while keeping the wire protocol consistent with the rest of the system
(``task:<id>`` HASH + ``queue:<name>`` LIST).
"""

from api_gateway.services.gpu_clients.faceswap import ReactorClient
from api_gateway.services.gpu_clients.inference import (
    InferenceClient,
    InferenceError,
    InferenceTimeout,
)

__all__ = [
    "ReactorClient",
    "InferenceClient",
    "InferenceError",
    "InferenceTimeout",
]
