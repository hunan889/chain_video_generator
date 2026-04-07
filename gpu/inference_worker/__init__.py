"""Inference worker — Redis consumer for embed/describe/chat tasks.

Runs on the GPU box (148), typically a single process on GPU 7 (shared with
Reactor since BGE only needs ~3 GB). Polls ``queue:inference`` and dispatches
tasks by ``mode``:

- ``inference_embed``    → BGE sentence-transformers (in-process)
- ``inference_describe`` → POST to 127.0.0.1:20010/v1 (vLLM Qwen2.5-VL)
- ``inference_chat``     → POST to 127.0.0.1:20001/v1 (vLLM Qwen3-14B)

Run with::

    python -m gpu.inference_worker.main
"""
