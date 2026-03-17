# CLAUDE.md — Project context for Claude Code

## Project
Wan2.2 Video Generation Service — FastAPI wrapper around ComfyUI for Wan2.2 T2V/I2V video generation.

## Development Workflow

**CRITICAL: This project uses a local-dev + remote-staging workflow. ALL development work MUST follow this pattern.**

### Environment Roles

**Local Machine (Development)**
- **Purpose**: Code development, git operations, file editing
- **Location**: `/Users/carlosliu/PycharmProjects/chain_video_generator`
- **Activities**:
  - Write and modify code
  - Commit changes to git
  - Push to remote repository
  - Local testing (if applicable)

**Remote Server (Testing/Staging)**
- **Purpose**: Deployment, verification, debugging, runtime testing
- **Host**: `root@148.153.121.44`
- **Password**: `docare@gogogo123`
- **Location**: `/home/gime/soft/wan22-service`
- **Activities**:
  - Pull latest code from git
  - Deploy and restart services
  - Run tests and verify functionality
  - Check logs and debug issues
  - Monitor system resources and performance
  - Gather runtime evidence and metrics

### Standard Workflow

1. **Development Phase (Local)**
   - Write/modify code locally
   - Test locally if possible
   - Commit changes with clear messages
   - Push to git repository (for version control)

2. **Fast Deployment (Recommended)**
   - Use `rsync` to sync files directly (faster than Git):
   ```bash
   # Sync specific file
   rsync -avz api/static/my_favorites.html wan22-server:/home/gime/soft/wan22-service/api/static/

   # Sync entire directory
   rsync -avz --exclude='.git' --exclude='*.log' --exclude='__pycache__' \
     api/ wan22-server:/home/gime/soft/wan22-service/api/
   ```
   - **When to restart**: Only restart if Python code changed (not needed for static files)
   - **Restart command**: `ssh wan22-server "pkill -f 'uvicorn api.main:app'; cd /home/gime/soft/wan22-service && nohup /home/gime/soft/miniconda3/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 >> api.log 2>&1 &"`

3. **Git Deployment (Alternative)**
   - SSH to remote: `ssh wan22-server`
   - Pull changes: `cd /home/gime/soft/wan22-service && git pull`
   - Restart if needed

4. **Verification Phase (Remote)**
   - Check service status: `ps aux | grep -E 'uvicorn|comfyui|redis'`
   - Check logs: `tail -f api.log` or `tail -f logs/*.log`
   - Test API endpoints
   - Monitor system resources
   - Gather evidence of success/failure

4. **Debug Phase (Remote if issues found)**
   - Analyze logs on remote server
   - Check configuration files
   - Verify data and state
   - Reproduce issues
   - Gather evidence for root cause analysis

5. **Fix Phase (Local)**
   - Return to local machine
   - Implement fixes based on remote evidence
   - Repeat cycle from step 1

### Remote Access Commands

```bash
# Connect to remote server
ssh root@148.153.121.44
# Password: docare@gogogo123

# Quick status check
cd /home/gime/soft/wan22-service
git status
git log --oneline -5
ps aux | grep -E 'uvicorn|comfyui|redis' | grep -v grep

# Service management
bash scripts/stop_all.sh
bash scripts/start_all.sh
screen -ls

# Log monitoring
tail -f api.log
tail -f logs/*.log
```

### Important Rules

- **NEVER** write code directly on the remote server
- **ALWAYS** develop locally, then deploy to remote
- **ALWAYS** verify changes on remote after deployment
- **ALWAYS** gather evidence from remote before proposing fixes
- **NEVER** assume local behavior matches remote behavior
- **ALWAYS** check remote logs and metrics for debugging

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

## Work Methodology

**Core Principle: Never assume. Always verify. Evidence over intuition.**

### Problem-Solving Workflow

**CRITICAL: Do NOT jump to code changes immediately. Follow this strict workflow:**

1. **Confirm the Problem**
   - Gather evidence and data
   - Reproduce the issue
   - Measure baseline metrics
   - Verify the problem actually exists

2. **Investigate Root Cause**
   - Trace through the system
   - Check logs, configs, and data
   - Identify the actual source of the issue
   - Rule out false leads

3. **Discuss Solutions**
   - Present findings to the user
   - Propose multiple approaches if applicable
   - Discuss trade-offs and implications
   - Get user input on preferred direction

4. **Document the Plan**
   - Write the solution plan to a document (e.g., `FIX_PLAN.md`)
   - Include: problem summary, root cause, proposed changes, expected impact
   - Get user confirmation on the documented plan

5. **Execute (After Clear Context)**
   - User should run `/clear` to start fresh context
   - Execute the documented plan step by step
   - Verify each change works as expected

**Golden Rules:**
- If you can't prove it with evidence, don't claim it
- Never modify code before understanding the full problem
- Always document before executing
- Prefer investigation over speculation
- **Do NOT create excessive documentation files** unless explicitly requested by the user
- **Do NOT start implementing fixes until the user explicitly confirms the plan and asks you to proceed**

When debugging, fixing, or deploying changes:
1. Gather evidence before diagnosing
2. Verify process actually restarted (check timestamps)
3. Test that the fix actually works (functional verification)
4. Report with evidence, not assumptions

See [METHODOLOGY.md](./METHODOLOGY.md) for the complete problem diagnosis and fix verification methodology.

## Advanced Workflow Face Swap Logic

**IMPORTANT: Face swap should only happen in Stage 2/3 (image processing), NOT in Stage 4 (video generation)**

### `full_body_reference` Mode:
- **Stage 2**: Acquire first frame (T2I or select) → **Optional** face swap (`stage2_first_frame.face_swap.enabled`)
- **Stage 3**: **Required** SeeDream editing (replaces full body features from reference)
- **Stage 4**: Video generation using edited frame → **NO face swap**

### `face_reference` Mode:
- **Stage 2**: Acquire first frame (T2I or select) → **Optional** face swap (`stage2_first_frame.face_swap.enabled`)
- **Stage 3**: **Skip** SeeDream (not needed for face-only mode)
- **Stage 4**: Video generation using frame → **NO face swap** (already done in Stage 2 if enabled)

### Key Principle:
- Face swap happens during **image preparation** (Stage 2/3)
- Video generation (Stage 4) uses the **already-processed frame**
- `stage4_video.face_swap` configuration should be **ignored/removed**
