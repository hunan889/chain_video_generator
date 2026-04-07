"""All GPU-side worker code lives under this package.

Sub-packages:
- ``gpu.comfyui_worker`` — consumes ``queue:a14b`` / ``queue:5b`` / ``queue:faceswap``
  and dispatches to local ComfyUI instances.
- ``gpu.inference_worker`` — consumes ``queue:inference`` and serves embed /
  describe / chat requests via local BGE (sentence-transformers) and vLLM
  HTTP loopback (LLM on 20001, VLM on 20010).

Both run on the GPU box (148). The api_gateway (170) talks to them only via
Redis (see ``api_gateway/services/gpu_clients/``).
"""
