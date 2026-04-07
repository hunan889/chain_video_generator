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
WORKER_LORAS_PREFIX = "worker:loras"
POSE_EMBEDDINGS_PREFIX = "pose_embeddings"

# --- Fixed key names (queue names used by gpu/inference_worker) ---
SYSTEM_SETTINGS_KEY = "system:settings"
INFERENCE_QUEUE_NAME = "inference"  # use with queue_key()


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


def worker_loras_key(worker_id: str) -> str:
    """Redis key (string/JSON) storing the LoRA list published by a worker."""
    return f"{WORKER_LORAS_PREFIX}:{worker_id}"


def inference_queue_key() -> str:
    """Redis LIST key for the inference task queue (consumed by gpu/inference_worker)."""
    return f"{QUEUE_PREFIX}:{INFERENCE_QUEUE_NAME}"


def pose_embeddings_key(model_name: str, content_hash: str) -> str:
    """Redis STRING key for cached pose embeddings.

    The key includes both the embedding model name and a content hash so that
    changing the pose set or the model invalidates the cache automatically.
    """
    return f"{POSE_EMBEDDINGS_PREFIX}:{model_name}:{content_hash}"


def pose_embeddings_current_key(model_name: str) -> str:
    """Redis STRING key holding the current content hash for a given model."""
    return f"{POSE_EMBEDDINGS_PREFIX}:{model_name}:current"
