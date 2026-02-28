# CLAUDE.md — Project context for Claude Code

## Project
Wan2.2 Video Generation Service — FastAPI wrapper around ComfyUI for Wan2.2 T2V/I2V video generation.

## Architecture
- `api/` — FastAPI app (config, routes, services, models, middleware, static UI)
- `api/config.py` — All paths resolved relative to PROJECT_ROOT via `_resolve_path()`
- `api/services/task_manager.py` — Redis-backed task queue with ComfyUI WebSocket progress tracking
- `api/services/comfyui_client.py` — HTTP/WS client for ComfyUI instances
- `api/services/workflow_builder.py` — Loads JSON workflow templates, injects parameters
- `workflows/` — ComfyUI workflow JSON templates (t2v_a14b, t2v_5b, i2v_a14b, i2v_5b)
- `config/` — api_keys.yaml, loras.yaml
- `scripts/` — setup, start/stop, model/LoRA download scripts
- `.env` — runtime config (gitignored), `.env.example` is the template

## Key patterns
- All paths in `.env` and config.py support relative (resolved from project root) or absolute
- Scripts use `SCRIPT_DIR/PROJECT_DIR` pattern + source `.env` for portability
- Two ComfyUI instances: A14B (multi-GPU, two-stage HIGH→LOW) and 5B (single GPU, turbo)
- LoRA injection: WanVideoLoraSelect (chainable) → WanVideoSetLoRAs
- Task flow: API → Redis queue → ComfyUI prompt → WS progress → save video

## Commands
```bash
bash scripts/setup.sh           # Full install (ComfyUI + venv + deps)
bash scripts/download_models.sh # Download models (~75GB)
bash scripts/start_all.sh       # Start all services (screen)
bash scripts/stop_all.sh        # Stop all services
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000  # Run API directly
```

## Tech stack
Python 3.11, FastAPI, Redis, aiohttp, websockets, ComfyUI, PyTorch (CUDA)
