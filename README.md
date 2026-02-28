# Wan2.2 Video Generation Service

API service for Wan2.2 video generation (T2V / I2V) powered by ComfyUI.

## Quick Start

```bash
git clone <repo> wan22-service && cd wan22-service

# 1. Install dependencies + clone ComfyUI + create venv
sudo bash scripts/setup.sh

# 2. Configure GPU IDs and ports
vim .env

# 3. Download models (~75GB)
bash scripts/download_models.sh

# 4. Download LoRAs (optional — edit script to add CivitAI version IDs)
bash scripts/download_loras.sh

# 5. Start all services
bash scripts/start_all.sh
```

## Configuration

Copy `.env.example` to `.env` (done automatically by `setup.sh`). Key settings:

| Variable | Default | Description |
|---|---|---|
| `A14B_GPU_IDS` | `0,1` | GPUs for A14B model |
| `FIVE_B_GPU_ID` | `2` | GPU for 5B model |
| `COMFYUI_A14B_PORT` | `8188` | ComfyUI A14B port |
| `COMFYUI_5B_PORT` | `8189` | ComfyUI 5B port |
| `API_PORT` | `8000` | API server port |

## Service Management

```bash
bash scripts/start_all.sh   # Start all services (screen sessions)
bash scripts/stop_all.sh    # Stop all services
screen -r comfyui_a14b      # View A14B logs
screen -r wan22_api          # View API logs
```

## API

- Web UI: `http://<host>:8000/`
- Health: `GET /health`
- Generate T2V: `POST /api/v1/generate`
- Generate I2V: `POST /api/v1/generate/i2v`
- Task status: `GET /api/v1/tasks/{task_id}`
