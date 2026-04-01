"""Tests for shared.redis_keys — write FIRST, implement after."""

from shared.redis_keys import (
    SYSTEM_SETTINGS_KEY,
    TASK_PREFIX,
    QUEUE_PREFIX,
    CHAIN_PREFIX,
    WORKFLOW_PREFIX,
    COMFYUI_INSTANCES_PREFIX,
    WORKER_HEARTBEAT_PREFIX,
    WORKER_LORAS_PREFIX,
    task_key,
    queue_key,
    chain_key,
    workflow_key,
    comfyui_instances_key,
    worker_heartbeat_key,
    worker_loras_key,
)


class TestKeyFunctions:
    def test_task_key(self):
        assert task_key("abc123") == "task:abc123"

    def test_queue_key(self):
        assert queue_key("a14b") == "queue:a14b"
        assert queue_key("5b") == "queue:5b"

    def test_chain_key(self):
        assert chain_key("chain_001") == "chain:chain_001"

    def test_workflow_key(self):
        assert workflow_key("wf_xyz") == "workflow:wf_xyz"

    def test_comfyui_instances_key(self):
        assert comfyui_instances_key("a14b") == "comfyui_instances:a14b"

    def test_worker_heartbeat_key(self):
        assert worker_heartbeat_key("worker_1") == "worker:heartbeat:worker_1"

    def test_worker_loras_key(self):
        assert worker_loras_key("gpu-worker-1") == "worker:loras:gpu-worker-1"

    def test_system_settings_key_is_constant(self):
        assert SYSTEM_SETTINGS_KEY == "system:settings"


class TestPrefixUniqueness:
    def test_all_prefixes_are_distinct(self):
        prefixes = [
            TASK_PREFIX,
            QUEUE_PREFIX,
            CHAIN_PREFIX,
            WORKFLOW_PREFIX,
            COMFYUI_INSTANCES_PREFIX,
            WORKER_HEARTBEAT_PREFIX,
            WORKER_LORAS_PREFIX,
        ]
        assert len(prefixes) == len(set(prefixes)), "Prefix collision detected"


class TestEdgeCases:
    def test_empty_id_produces_valid_key(self):
        assert task_key("") == "task:"

    def test_special_characters_in_id(self):
        assert task_key("a:b:c") == "task:a:b:c"
