"""Centralized Redis key patterns shared between API gateway and GPU worker.

All Redis key construction should go through these functions to ensure
consistency and prevent typo-related bugs across services.
"""

# --- Key prefixes ---
TASK_PREFIX = "task"
QUEUE_PREFIX = "queue"
CHAIN_PREFIX = "chain"
WORKFLOW_PREFIX = "workflow"
COMFYUI_INSTANCES_PREFIX = "comfyui_instances"
WORKER_HEARTBEAT_PREFIX = "worker:heartbeat"

# --- Fixed keys ---
SYSTEM_SETTINGS_KEY = "system:settings"


def task_key(task_id: str) -> str:
    """Redis HASH key for a task."""
    return f"{TASK_PREFIX}:{task_id}"


def queue_key(model: str) -> str:
    """Redis LIST key for the task queue of a model (e.g. 'a14b', '5b')."""
    return f"{QUEUE_PREFIX}:{model}"


def chain_key(chain_id: str) -> str:
    """Redis HASH key for a chain."""
    return f"{CHAIN_PREFIX}:{chain_id}"


def workflow_key(workflow_id: str) -> str:
    """Redis HASH key for a workflow."""
    return f"{WORKFLOW_PREFIX}:{workflow_id}"


def comfyui_instances_key(model: str) -> str:
    """Redis SET key for registered ComfyUI instance URLs of a model."""
    return f"{COMFYUI_INSTANCES_PREFIX}:{model}"


def worker_heartbeat_key(worker_id: str) -> str:
    """Redis HASH key for a worker's heartbeat."""
    return f"{WORKER_HEARTBEAT_PREFIX}:{worker_id}"
