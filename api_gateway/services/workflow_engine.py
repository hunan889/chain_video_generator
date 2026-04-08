"""Advanced Workflow Engine — 4-stage video generation orchestrator.

Ported from api/routes/workflow.py + workflow_executor.py (~5000 lines)
into a clean, modular design using Gateway's existing infrastructure.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from api_gateway.config import GatewayConfig
from api_gateway.services.task_store import TaskStore
from shared.cos.client import COSClient
from shared.task_gateway import TaskGateway

logger = logging.getLogger(__name__)

# Stage names and weights for progress calculation
STAGE_NAMES = ["prompt_analysis", "first_frame_acquisition", "seedream_edit", "video_generation"]
STAGE_WEIGHTS = {"prompt_analysis": 0.05, "first_frame_acquisition": 0.10,
                 "seedream_edit": 0.10, "video_generation": 0.75}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base. Override values take priority."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_default_internal_config(mode: str, turbo: bool = False,
                                  resolution: str | None = None,
                                  prompt_settings: dict | None = None) -> dict:
    """Build default internal_config based on mode and turbo flag.

    NOTE: ``turbo`` only controls video-sampling speed (steps/cfg/scheduler/
    interpolation). Prompt optimisation is now governed by the request-level
    ``optimize_mode`` field; this function defaults to the ``prompt_lora``
    behaviour and ``start_workflow`` overrides ``stage1`` based on the
    request before deep-merging.
    """
    ps = prompt_settings or {}

    stage1 = {
        "auto_analyze": True, "auto_lora": True,
        "auto_prompt": True,
        "auto_completion": 2,
        "inject_trigger_prompt": ps.get("inject_trigger_prompt", True),
        "inject_trigger_words": ps.get("inject_trigger_words", True),
    }

    # Stage 2: face swap config
    face_swap_enabled = mode in ("face_reference", "full_body_reference")
    stage2 = {
        "first_frame_source": "select_existing",
        "face_swap": {"enabled": face_swap_enabled, "strength": 1.0},
    }

    # Stage 3: SeeDream config
    if mode in ("first_frame", "t2v"):
        stage3 = {"enabled": False}
    elif mode == "face_reference":
        stage3 = {"enabled": not turbo, "swap_face": True, "swap_accessories": True,
                  "swap_expression": False, "swap_clothing": False} if not turbo else {"enabled": False}
    elif mode == "full_body_reference":
        stage3 = {"enabled": True, "swap_face": True, "swap_accessories": True,
                  "swap_expression": False, "swap_clothing": True}
    else:
        stage3 = {"enabled": False}

    # Stage 4: video generation
    noise_aug = 0.0 if mode == "t2v" else 0.2
    generation = {"steps": 5, "cfg": 1 if turbo else 2, "scheduler": "euler",
                  "shift": 5.0, "model_preset": "nsfw_v2", "noise_aug_strength": noise_aug}

    res = (resolution or "").lower()
    if res == "480p":
        upscale = {"enabled": False}
    elif res == "720p":
        upscale = {"enabled": True, "model": "4x_foolhardy_Remacri", "resize": 1.5}
    else:
        upscale = {"enabled": True, "model": "4x_foolhardy_Remacri", "resize": 2.0}

    stage4 = {
        "generation": generation,
        "postprocess": {
            "upscale": upscale,
            "interpolation": {"enabled": not turbo, "multiplier": 2, "profile": "fast"},
            "mmaudio": {"enabled": False},
        },
    }

    return {
        "stage1_prompt_analysis": stage1,
        "stage2_first_frame": stage2,
        "stage3_seedream": stage3,
        "stage4_video": stage4,
    }


class WorkflowEngine:
    """Orchestrates the 4-stage advanced workflow pipeline."""

    def __init__(
        self,
        gateway: TaskGateway,
        redis,
        config: GatewayConfig,
        cos_client: COSClient,
        task_store: TaskStore,
    ):
        self.gateway = gateway
        self.redis = redis
        self.config = config
        self.cos_client = cos_client
        self.task_store = task_store
        self._active_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_workflow(self, req: dict) -> dict:
        """Create and launch a workflow. Returns initial response dict."""
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        mode = req.get("mode", "t2v")
        user_prompt = req.get("user_prompt", "")
        turbo = req.get("turbo", True)
        parent_wf_id = req.get("parent_workflow_id")

        # Auto-fix: first_frame without image → t2v
        if mode == "first_frame" and not req.get("uploaded_first_frame") and not req.get("reference_image"):
            mode = "t2v"

        # Validate continuation
        if not user_prompt and not parent_wf_id:
            if mode != "t2v":
                raise ValueError("user_prompt is required (or provide parent_workflow_id for continuation)")

        # Read prompt settings from Redis
        prompt_settings = {}
        try:
            raw = await self.redis.get("system:settings")
            if raw:
                prompt_settings = json.loads(raw)
        except Exception:
            pass

        # Build config
        default_config = build_default_internal_config(mode, turbo, req.get("resolution"), prompt_settings)

        # Translate optimize_mode → stage1 flags BEFORE deep-merging the
        # request's explicit internal_config (so an internal_config override
        # still wins, but optimize_mode beats the defaults).
        opt_mode = req.get("optimize_mode") or "prompt_lora"
        _OPT_MODE_MAP = {
            "none":        {"auto_prompt": False, "auto_lora": False},
            "prompt":      {"auto_prompt": True,  "auto_lora": False},
            "prompt_lora": {"auto_prompt": True,  "auto_lora": True},
        }
        if opt_mode not in _OPT_MODE_MAP:
            logger.warning("Unknown optimize_mode=%r, falling back to prompt_lora", opt_mode)
            opt_mode = "prompt_lora"
        default_config.setdefault("stage1_prompt_analysis", {}).update(_OPT_MODE_MAP[opt_mode])
        default_config["stage1_prompt_analysis"]["optimize_mode"] = opt_mode

        if req.get("internal_config"):
            internal_config = _deep_merge(default_config, req["internal_config"])
        else:
            internal_config = default_config

        # v2 frontend: merge resolution/aspect_ratio/duration
        if req.get("resolution") or req.get("aspect_ratio") or req.get("duration"):
            gen = internal_config.setdefault("stage4_video", {}).setdefault("generation", {})
            if req.get("resolution") and req.get("aspect_ratio"):
                ar = req["aspect_ratio"].replace(":", "_")
                gen["resolution"] = f"{req['resolution']}_{ar}"
            elif req.get("resolution"):
                gen["resolution"] = req["resolution"]
            if req.get("duration"):
                gen["duration"] = f"{req['duration']}s"

        # MMAudio merge
        if req.get("mmaudio"):
            internal_config.setdefault("stage4_video", {}).setdefault("postprocess", {})["mmaudio"] = req["mmaudio"]

        # Init stages
        stages = [{"name": s, "status": "pending"} for s in STAGE_NAMES]

        # Save to Redis
        now = time.time()
        wf_data = {
            "status": "running",
            "current_stage": "prompt_analysis",
            "mode": mode,
            "user_prompt": user_prompt,
            "reference_image": req.get("reference_image", ""),
            "first_frame_source": req.get("first_frame_source", ""),
            "internal_config": json.dumps(internal_config),
            "created_at": str(now),
            "executor_heartbeat": str(now),
        }
        if parent_wf_id:
            wf_data["parent_workflow_id"] = parent_wf_id
        if req.get("uploaded_first_frame"):
            wf_data["uploaded_first_frame"] = req["uploaded_first_frame"]
        if req.get("pose_keys"):
            wf_data["pose_keys"] = json.dumps(req["pose_keys"])

        wf_key = f"workflow:{workflow_id}"
        # Redis hset doesn't accept None values — filter them out
        clean_data = {k: v for k, v in wf_data.items() if v is not None}
        await self.redis.hset(wf_key, mapping=clean_data)
        await self.redis.expire(wf_key, 7 * 86400)

        # Save to MySQL
        await self.task_store.create(
            task_id=workflow_id, task_type="workflow", category="local",
            provider="comfyui", prompt=user_prompt, model=None,
            params={"mode": mode, "turbo": turbo, "internal_config": internal_config},
            parent_task_id=parent_wf_id if parent_wf_id else None,
        )
        # Immediately mark as running in MySQL to match Redis status
        await self.task_store.update_status(workflow_id, "running")

        # Launch background execution
        task = asyncio.create_task(self._execute_workflow(workflow_id, req, internal_config, mode))
        self._active_tasks[workflow_id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(workflow_id, None))

        return {
            "workflow_id": workflow_id,
            "status": "running",
            "current_stage": "prompt_analysis",
            "stages": stages,
        }

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel a running workflow."""
        wf_key = f"workflow:{workflow_id}"
        status = await self.redis.hget(wf_key, "status")
        if status in ("completed", "failed", "cancelled"):
            return False
        await self.redis.hset(wf_key, mapping={"status": "cancelled", "error": "Cancelled by user"})
        task = self._active_tasks.get(workflow_id)
        if task and not task.done():
            task.cancel()
        await self.task_store.update_status(workflow_id, "cancelled", error="Cancelled by user")
        return True

    async def regenerate(self, workflow_id: str) -> dict:
        """Regenerate a workflow with same params."""
        wf_key = f"workflow:{workflow_id}"
        data = await self.redis.hgetall(wf_key)
        if not data:
            # Try MySQL
            mysql_data = await self.task_store.get(workflow_id)
            if not mysql_data:
                raise ValueError(f"Workflow {workflow_id} not found")
            data = {"mode": mysql_data.get("task_type", "t2v"),
                    "user_prompt": mysql_data.get("prompt", "")}

        req = {
            "mode": data.get("mode", "t2v"),
            "user_prompt": data.get("user_prompt", ""),
            "reference_image": data.get("reference_image"),
            "turbo": True,
        }
        # Restore internal_config
        ic_raw = data.get("internal_config")
        if ic_raw:
            try:
                req["internal_config"] = json.loads(ic_raw)
            except (json.JSONDecodeError, TypeError):
                pass
        # Restore parent
        parent = data.get("parent_workflow_id")
        if parent:
            req["parent_workflow_id"] = parent

        return await self.start_workflow(req)

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    async def _execute_workflow(self, workflow_id: str, req: dict,
                                internal_config: dict, mode: str):
        """Main orchestration: run stages 1-4 sequentially."""
        wf_key = f"workflow:{workflow_id}"

        try:
            # Check for continuation
            parent_workflow = None
            is_continuation = bool(req.get("parent_workflow_id"))
            origin_first_frame_url = None

            if is_continuation:
                parent_wf_key = f"workflow:{req['parent_workflow_id']}"
                parent_workflow = await self.redis.hgetall(parent_wf_key)
                if not parent_workflow or parent_workflow.get("status") != "completed":
                    raise RuntimeError(f"Parent workflow {req['parent_workflow_id']} not found or not completed")
                # Trace origin first frame for CLIP Vision anchoring
                origin_first_frame_url = parent_workflow.get("origin_first_frame_url") or \
                                         parent_workflow.get("first_frame_url")

                # Inherit reference_image from parent for continuation face swap.
                # The frontend continuation request carries face_swap.enabled via
                # internal_config (controlled by the Settings "Swap Face" toggle)
                # but does NOT include reference_image — we must inherit it.
                if not req.get("reference_image") and parent_workflow.get("reference_image"):
                    req["reference_image"] = parent_workflow["reference_image"]
                    logger.info("[%s] Inherited reference_image from parent", workflow_id)

            # ---- Stage 1: Prompt Analysis ----
            await self._update_stage(wf_key, "prompt_analysis", "running")
            from api_gateway.services.stages.prompt_analysis import analyze_prompt
            analysis = await analyze_prompt(
                user_prompt=req.get("user_prompt", ""),
                mode=mode,
                pose_keys=req.get("pose_keys"),
                internal_config=internal_config,
                config=self.config,
                redis=self.redis,
            )
            await self._update_stage(wf_key, "prompt_analysis", "completed",
                                     details={"original_prompt": analysis.original_prompt,
                                              "optimized_prompt": analysis.optimized_prompt,
                                              "video_loras": [{"name": l.get("name", ""), "weight": l.get("weight", 0.8)}
                                                              for l in analysis.video_loras],
                                              "image_loras": analysis.image_loras,
                                              "pose_keys": analysis.pose_keys,
                                              "optimize_mode": internal_config.get(
                                                  "stage1_prompt_analysis", {}
                                              ).get("optimize_mode", "prompt_lora")})
            await self.redis.hset(wf_key, "analysis_result", json.dumps({
                "video_prompt": analysis.video_prompt,
                "t2i_prompt": analysis.t2i_prompt,
                "video_loras": analysis.video_loras,
                "image_loras": analysis.image_loras,
                "pose_keys": analysis.pose_keys,
            }))

            # ---- Stage 2: First Frame Acquisition ----
            await self._update_stage(wf_key, "first_frame_acquisition", "running")
            from api_gateway.services.stages.first_frame import acquire_first_frame
            from api_gateway.services.gpu_clients.faceswap import ReactorClient
            from dataclasses import asdict
            reactor_client = ReactorClient(gateway=self.gateway, redis=self.redis, cos_prefix=self.config.cos_prefix)
            analysis_dict = asdict(analysis) if hasattr(analysis, '__dataclass_fields__') else (analysis or {})
            ff_result = await acquire_first_frame(
                workflow_id=workflow_id, mode=mode,
                uploaded_first_frame=req.get("uploaded_first_frame"),
                reference_image=req.get("reference_image") or analysis.reference_image,
                analysis_result=analysis_dict,
                face_swap_config=internal_config.get("stage2_first_frame", {}).get("face_swap", {}),
                is_continuation=is_continuation, parent_workflow=parent_workflow,
                config=self.config, redis=self.redis, cos_client=self.cos_client,
                reactor_client=reactor_client,
            )
            first_frame_url = ff_result.url if hasattr(ff_result, 'url') else ff_result
            if first_frame_url:
                await self.redis.hset(wf_key, "first_frame_url", first_frame_url)
            await self._update_stage(wf_key, "first_frame_acquisition", "completed",
                                     details={"url": first_frame_url, "source": "continuation" if is_continuation else mode})

            # ---- Stage 3: SeeDream Edit ----
            seedream_config = internal_config.get("stage3_seedream", {})
            edited_frame_url = None
            if seedream_config.get("enabled") and first_frame_url and req.get("reference_image"):
                await self._update_stage(wf_key, "seedream_edit", "running")
                try:
                    from api_gateway.services.stages.seedream_edit import edit_first_frame
                    from api_gateway.services.external.byteplus import BytePlusClient
                    from api_gateway.services.gpu_clients.faceswap import ReactorClient
                    bp = BytePlusClient(self.config.byteplus_api_key, self.config.byteplus_endpoint,
                                       self.config.byteplus_seedream_model)
                    rc = ReactorClient(gateway=self.gateway, redis=self.redis, cos_prefix=self.config.cos_prefix)
                    sd_result = await edit_first_frame(
                        workflow_id=workflow_id, first_frame_url=first_frame_url,
                        reference_image_url=req.get("reference_image"),
                        mode=mode, seedream_config=seedream_config,
                        face_swap_config=internal_config.get("stage2_first_frame", {}).get("face_swap", {}),
                        user_prompt=req.get("user_prompt", ""),
                        reactor_deferred=False,
                        is_continuation=is_continuation,
                        resolution=req.get("resolution", "480p"),
                        aspect_ratio=req.get("aspect_ratio"),
                        config=self.config,
                        byteplus_client=bp, reactor_client=rc, cos_client=self.cos_client,
                    )
                    edited_frame_url = sd_result.url if hasattr(sd_result, 'url') else sd_result
                    # Re-upload BytePlus temp URL to COS for permanent storage
                    if edited_frame_url and "ark-acg" in edited_frame_url and self.cos_client:
                        try:
                            import httpx as _httpx, tempfile, os
                            async with _httpx.AsyncClient(timeout=30.0) as _hc:
                                _resp = await _hc.get(edited_frame_url)
                                _resp.raise_for_status()
                            _ext = ".png" if "png" in edited_frame_url.lower() else ".jpeg"
                            _fname = f"first_frame_{workflow_id}{_ext}"
                            _tmp = tempfile.NamedTemporaryFile(suffix=_ext, delete=False)
                            _tmp.write(_resp.content)
                            _tmp.close()
                            edited_frame_url = self.cos_client.upload_file(_tmp.name, "frames", _fname)
                            os.unlink(_tmp.name)
                            logger.info("[%s] Re-uploaded edited frame to COS: %s", workflow_id, edited_frame_url)
                        except Exception as _e:
                            logger.warning("[%s] Failed to re-upload edited frame to COS: %s", workflow_id, _e)
                    if edited_frame_url:
                        await self.redis.hset(wf_key, "edited_frame_url", edited_frame_url)
                    await self._update_stage(wf_key, "seedream_edit", "completed",
                                             details={"url": edited_frame_url})
                except Exception as e:
                    logger.warning("[%s] SeeDream edit failed: %s", workflow_id, e)
                    await self._update_stage(wf_key, "seedream_edit", "failed", error=str(e))
                    # Non-fatal for face_reference; fatal for full_body_reference
                    if mode == "full_body_reference":
                        raise
            else:
                await self._update_stage(wf_key, "seedream_edit", "completed",
                                         details={"skipped": True})

            # ---- Stage 4: Video Generation ----
            await self._update_stage(wf_key, "video_generation", "running")
            frame_for_video = edited_frame_url or first_frame_url
            from api_gateway.services.stages.video_generation import generate_video
            vg_result = await generate_video(
                workflow_id=workflow_id, mode=mode,
                first_frame_url=frame_for_video,
                analysis_result=analysis_dict, internal_config=internal_config,
                user_prompt=req.get("user_prompt", ""),
                is_continuation=is_continuation, parent_workflow=parent_workflow,
                origin_first_frame_url=origin_first_frame_url,
                config=self.config, gateway=self.gateway, cos_client=self.cos_client,
                redis=self.redis,
            )

            chain_id = vg_result.chain_id if hasattr(vg_result, 'chain_id') else None
            final_video_url = vg_result.video_url if hasattr(vg_result, 'video_url') else None
            if chain_id:
                await self.redis.hset(wf_key, "chain_id", chain_id)
            if final_video_url:
                await self.redis.hset(wf_key, mapping={
                    "final_video_url": final_video_url,
                    "status": "completed",
                    "completed_at": str(time.time()),
                })
                # Build rich stage details
                gen_config = internal_config.get("stage4_video", {}).get("generation", {})
                postproc_config = internal_config.get("stage4_video", {}).get("postprocess", {})
                stage4_details = {
                    "chain_id": chain_id,
                    "video_url": final_video_url,
                    "model": gen_config.get("model", gen_config.get("model_preset", "")),
                    "resolution": gen_config.get("resolution", ""),
                    "duration": gen_config.get("duration", ""),
                    "steps": gen_config.get("steps"),
                    "cfg": gen_config.get("cfg"),
                    "scheduler": gen_config.get("scheduler", ""),
                    "width": getattr(vg_result, "width", 0),
                    "height": getattr(vg_result, "height", 0),
                    "prompt": getattr(vg_result, "prompt_used", ""),
                    "loras": getattr(vg_result, "loras_used", []),
                    "postprocess": {
                        "upscale": postproc_config.get("upscale", {}),
                        "interpolation": postproc_config.get("interpolation", {}),
                        "mmaudio": postproc_config.get("mmaudio", {}),
                    },
                }
                await self._update_stage(wf_key, "video_generation", "completed",
                                         details=stage4_details)
                # Persist to MySQL
                await self.task_store.update_status(workflow_id, "completed")
                await self.task_store.set_result(workflow_id, result_url=final_video_url,
                                                 thumbnail_url=first_frame_url or edited_frame_url,
                                                 extra_urls={"first_frame_url": first_frame_url,
                                                             "edited_frame_url": edited_frame_url})
            else:
                raise RuntimeError("Video generation completed but no video URL returned")

            # Store origin first frame for future continuations
            if first_frame_url and not is_continuation:
                await self.redis.hset(wf_key, "origin_first_frame_url", first_frame_url)

            logger.info("[%s] Workflow completed successfully", workflow_id)

        except asyncio.CancelledError:
            logger.info("[%s] Workflow cancelled", workflow_id)
            await self.redis.hset(wf_key, mapping={"status": "cancelled"})
        except Exception as e:
            logger.exception("[%s] Workflow failed: %s", workflow_id, e)
            await self.redis.hset(wf_key, mapping={
                "status": "failed", "error": str(e), "completed_at": str(time.time()),
            })
            # Mark current stage as failed
            current = await self.redis.hget(wf_key, "current_stage")
            if current:
                await self._update_stage(wf_key, current, "failed", error=str(e))
            await self.task_store.update_status(workflow_id, "failed", error=str(e))

    async def _update_stage(self, wf_key: str, stage: str, status: str,
                            details: dict | None = None, error: str | None = None):
        """Update stage status in Redis hash."""
        mapping = {
            f"stage_{stage}": status,
            "current_stage": stage,
            "executor_heartbeat": str(time.time()),
        }
        if details:
            mapping[f"stage_{stage}_details"] = json.dumps(details, ensure_ascii=False)
        if error:
            mapping[f"stage_{stage}_error"] = error
        await self.redis.hset(wf_key, mapping=mapping)
