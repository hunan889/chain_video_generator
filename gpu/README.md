# gpu/

All worker-side GPU code lives here. Runs on the GPU box (148).

## Layout

```
gpu/
├── comfyui_worker/      # Redis consumer for queue:a14b / queue:5b / queue:faceswap
│                        # (was top-level gpu_worker/ before consolidation)
├── inference_worker/    # Redis consumer for queue:inference
│                        # serves embed (BGE) + describe (VLM) + chat (LLM)
└── scripts/             # Startup/stop scripts for all GPU services
```

## How the gateway talks to us

Everything goes through Redis. The gateway (170) does NOT call any HTTP
endpoint on this box directly. Instead it uses
``api_gateway/services/gpu_clients/`` to push tasks onto Redis queues, and
the workers in this folder consume them.

| Queue | Consumer | Producer (gateway client) |
|---|---|---|
| `queue:a14b` | `comfyui_worker` | `services.workflow_engine` |
| `queue:5b` | `comfyui_worker` | `services.workflow_engine` |
| `queue:faceswap` | `comfyui_worker` | `services.gpu_clients.faceswap.ReactorClient` |
| `queue:inference` | `inference_worker` | `services.gpu_clients.inference.InferenceClient` |

## GPU allocation (148, 8 × RTX 4090 24GB)

```
GPU 0: ComfyUI A14B #1
GPU 1: vLLM LLM (Qwen3-14B) TP0     ──┐ tensor-parallel pair
GPU 2: vLLM LLM (Qwen3-14B) TP1     ──┘
GPU 3: ComfyUI A14B #2
GPU 4: ComfyUI A14B #3
GPU 5: vLLM VLM (Qwen2.5-VL-7B)
GPU 6: ComfyUI A14B #4
GPU 7: Reactor (Forge) + inference_worker (BGE)
```

## Startup order

```bash
# vLLM services (LLM + VLM, both bind 127.0.0.1)
bash gpu/scripts/start_vllm_llm.sh
bash gpu/scripts/start_vllm_vlm.sh

# Inference worker (depends on the two vLLM endpoints + BGE)
bash gpu/scripts/start_inference_worker.sh

# Or all three at once with health-check between each:
bash gpu/scripts/start_all.sh
```

ComfyUI A14B instances are managed independently via
`gpu/scripts/start_comfyui_a14b.sh INSTANCE_ID=N`.

## Loopback ports (only valid inside 148)

| Service | Address | Bound to |
|---|---|---|
| vLLM LLM (Qwen3-14B) | `http://127.0.0.1:20001/v1` | `inference_worker` only |
| vLLM VLM (Qwen2.5-VL-7B) | `http://127.0.0.1:20010/v1` | `inference_worker` only |
| ComfyUI A14B #1..N | `http://127.0.0.1:8188+i` | `comfyui_worker` only |
| Forge / Reactor | `http://127.0.0.1:7867` | (unused; reactor goes through ComfyUI workflow) |

None of these are exposed to the public internet or to the gateway box.
The gateway talks to all of them indirectly via Redis.
