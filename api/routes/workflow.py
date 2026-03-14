import logging
import json
import asyncio
from pathlib import Path
from typing import Optional, Literal
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, Body, UploadFile, File, Form
from pydantic import BaseModel, Field
from api.models.schemas import GenerateResponse
from api.models.enums import GenerateMode, ModelType, TaskStatus
from api.middleware.auth import verify_api_key
from api.services.embedding_service import get_embedding_service
import pymysql

logger = logging.getLogger(__name__)
router = APIRouter()

# Database config
DB_CONFIG = {
    'host': 'use-cdb-b9nvte6o.sql.tencentcdb.com',
    'port': 20603,
    'user': 'user_soga',
    'password': '1IvO@*#68',
    'database': 'tudou_soga',
    'charset': 'utf8mb4'
}


# ============================================================================
# Advanced Workflow API - Phase 1
# ============================================================================

class WorkflowAnalyzeRequest(BaseModel):
    """Workflow analysis request"""
    prompt: str = Field(..., min_length=1, max_length=2000, description="User prompt")
    mode: Literal["face_reference", "full_body_reference", "first_frame"] = Field(
        ..., description="Workflow mode"
    )
    top_k_image_loras: int = Field(default=5, ge=1, le=20, description="Top K image LORAs")
    top_k_video_loras: int = Field(default=5, ge=1, le=20, description="Top K video LORAs")


class ImageLoraRecommendation(BaseModel):
    """Image LORA recommendation"""
    lora_id: int
    name: str
    description: Optional[str]
    trigger_words: list[str]
    category: Optional[str]
    similarity: float
    preview_url: Optional[str]


class VideoLoraRecommendation(BaseModel):
    """Video LORA recommendation"""
    lora_id: int
    name: str
    description: Optional[str]
    trigger_words: list[str]
    mode: str  # I2V, T2V, both
    noise_stage: str  # high, low, single
    category: Optional[str]
    similarity: float
    preview_url: Optional[str]


class WorkflowAnalyzeResponse(BaseModel):
    """Workflow analysis response"""
    original_prompt: str
    optimized_t2i_prompt: Optional[str] = None
    optimized_i2v_prompt: Optional[str] = None
    image_loras: list[ImageLoraRecommendation]
    video_loras: list[VideoLoraRecommendation]
    images: list[dict] = Field(default_factory=list, description="Recommended reference images")
    mode: str


@router.post("/workflow/analyze", response_model=WorkflowAnalyzeResponse)
async def analyze_workflow(req: WorkflowAnalyzeRequest, _=Depends(verify_api_key)):
    """
    Analyze user prompt and recommend LORAs for T2I and I2V stages.

    This is Phase 1 of the advanced workflow system.
    """
    try:
        if not req.prompt.strip():
            raise HTTPException(400, "Prompt cannot be empty")

        embedding_service = get_embedding_service()

        # Parallel semantic search for image LORAs, video LORAs, and reference images
        image_lora_task = _search_image_loras(
            embedding_service,
            req.prompt,
            req.top_k_image_loras,
            min_similarity=0.6
        )
        video_lora_task = _search_video_loras(
            embedding_service,
            req.prompt,
            req.mode,
            req.top_k_video_loras,
            min_similarity=0.6
        )
        images_task = _search_reference_images(
            embedding_service,
            req.prompt,
            top_k=5,
            min_similarity=0.6
        )

        image_loras, video_loras, images = await asyncio.gather(
            image_lora_task,
            video_lora_task,
            images_task
        )

        # Optimize prompts with Qwen3-14B
        optimized_t2i_prompt, optimized_i2v_prompt = await _optimize_prompt_with_llm(
            req.prompt,
            image_loras,
            video_loras,
            req.mode
        )

        return WorkflowAnalyzeResponse(
            original_prompt=req.prompt,
            optimized_t2i_prompt=optimized_t2i_prompt,
            optimized_i2v_prompt=optimized_i2v_prompt,
            image_loras=image_loras,
            video_loras=video_loras,
            images=images,
            mode=req.mode
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workflow analysis failed: {e}", exc_info=True)
        raise HTTPException(500, f"Workflow analysis failed: {str(e)}")


async def _search_image_loras(
    embedding_service,
    query: str,
    top_k: int,
    min_similarity: float = 0.6
) -> list[ImageLoraRecommendation]:
    """Search for similar image LORAs using semantic search"""
    try:
        # Use semantic search if available
        search_results = await embedding_service.search_similar_image_loras(
            query=query,
            top_k=top_k * 3  # Get more for filtering
        )

        if not search_results:
            logger.info("No image LORAs found via semantic search")
            return []

        # Filter by similarity threshold
        filtered_results = [r for r in search_results if r.get('similarity', 0.0) >= min_similarity]

        if not filtered_results:
            logger.warning(f"No image LORAs found with similarity >= {min_similarity}")
            return []

        # Get image_lora_ids from search results
        image_lora_ids = [item['image_lora_id'] for item in filtered_results]
        similarity_map = {item['image_lora_id']: item['similarity'] for item in filtered_results}

        # Query database for LORA details
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        placeholders = ','.join(['%s'] * len(image_lora_ids))
        cursor.execute(f"""
            SELECT id, name, description, trigger_prompt, category, tags
            FROM image_lora_metadata
            WHERE id IN ({placeholders})
            AND (enabled = 1 OR enabled = TRUE)
        """, image_lora_ids)

        lora_data = cursor.fetchall()
        cursor.close()
        conn.close()

        if not lora_data:
            logger.info("No enabled image LORAs found in database")
            return []

        image_loras = []
        for lora in lora_data:
            lora_id = str(lora['id'])

            # Parse trigger_prompt as trigger_words
            trigger_words = []
            trigger_prompt = lora.get('trigger_prompt')
            if trigger_prompt:
                trigger_words = [w.strip() for w in trigger_prompt.replace(',', ' ').split() if w.strip()]

            # Parse tags JSON
            tags = lora.get('tags')
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []
            if tags and isinstance(tags, list):
                trigger_words.extend(tags)

            # Remove duplicates
            trigger_words = list(dict.fromkeys(trigger_words))

            image_loras.append(ImageLoraRecommendation(
                lora_id=int(lora_id) if lora_id.isdigit() else hash(lora_id) % 1000000,
                name=lora['name'],
                description=lora.get('description'),
                trigger_words=trigger_words[:10],
                category=lora.get('category'),
                similarity=similarity_map.get(lora_id, 0.0),
                preview_url=None
            ))

        # Sort by similarity
        image_loras.sort(key=lambda x: x.similarity, reverse=True)

        logger.info(f"Found {len(image_loras)} image LORAs via semantic search")
        return image_loras[:top_k]

    except Exception as e:
        logger.error(f"Image LORA search failed: {e}", exc_info=True)
        return []


async def _optimize_prompt_with_llm(
    prompt: str,
    image_loras: list[ImageLoraRecommendation],
    video_loras: list[VideoLoraRecommendation],
    mode: str
) -> tuple[Optional[str], Optional[str]]:
    """
    Optimize prompts using Qwen3-14B LLM.

    Returns: (optimized_t2i_prompt, optimized_i2v_prompt)
    """
    try:
        from api.services.prompt_optimizer import PromptOptimizer
        from api.config import LLM_API_KEY

        if not LLM_API_KEY:
            logger.warning("LLM_API_KEY not configured, skipping prompt optimization")
            return None, None

        optimizer = PromptOptimizer()

        # Collect trigger words
        image_trigger_words = []
        for lora in image_loras[:3]:  # Top 3 image LORAs
            image_trigger_words.extend(lora.trigger_words[:2])

        video_trigger_words = []
        for lora in video_loras[:3]:  # Top 3 video LORAs
            video_trigger_words.extend(lora.trigger_words[:2])

        # Build LORA info for context
        lora_info = []
        for lora in video_loras[:3]:
            lora_info.append({
                "name": lora.name,
                "description": lora.description or "",
                "trigger_words": lora.trigger_words[:5],
                "example_prompts": []
            })

        # Optimize T2I prompt (for first frame generation)
        optimized_t2i = None
        if mode in ["face_reference", "full_body_reference"] and image_trigger_words:
            try:
                result = await optimizer.optimize(
                    prompt=prompt,
                    trigger_words=image_trigger_words,
                    mode="t2v",  # Use t2v mode for static image
                    duration=0.0,  # Single frame
                    lora_info=[]
                )
                optimized_t2i = result.get("optimized_prompt")

                # Add emphasis on front face for T2I
                if optimized_t2i and mode == "face_reference":
                    optimized_t2i = "front view, looking at camera, detailed face, " + optimized_t2i

                logger.info(f"T2I prompt optimized: {optimized_t2i[:100]}...")
            except Exception as e:
                logger.warning(f"T2I prompt optimization failed: {e}")

        # Optimize I2V prompt (for video generation)
        optimized_i2v = None
        if video_trigger_words:
            try:
                result = await optimizer.optimize(
                    prompt=prompt,
                    trigger_words=video_trigger_words,
                    mode="i2v",
                    duration=5.0,  # Default 5 seconds
                    lora_info=lora_info
                )
                optimized_i2v = result.get("optimized_prompt")
                logger.info(f"I2V prompt optimized: {optimized_i2v[:100]}...")
            except Exception as e:
                logger.warning(f"I2V prompt optimization failed: {e}")

        return optimized_t2i, optimized_i2v

    except Exception as e:
        logger.error(f"Prompt optimization failed: {e}", exc_info=True)
        return None, None


async def _search_video_loras(
    embedding_service,
    query: str,
    mode: str,
    top_k: int,
    min_similarity: float = 0.6
) -> list[VideoLoraRecommendation]:
    """Search for similar video LORAs using semantic search"""
    try:
        # Map workflow mode to video LORA mode filter
        lora_mode = None
        if mode == "first_frame":
            lora_mode = "T2V"  # First frame mode uses T2V
        # face_reference and full_body_reference use I2V, so no filter needed

        # Search using existing embedding service
        results = await embedding_service.search_similar_loras(
            query=query,
            mode=lora_mode,
            top_k=top_k * 3  # Get more results for filtering
        )

        if not results:
            logger.warning("No video LORAs found via semantic search")
            return []

        # Filter by similarity threshold
        filtered_results = [r for r in results if r.get('similarity', 0.0) >= min_similarity]

        if not filtered_results:
            logger.warning(f"No video LORAs found with similarity >= {min_similarity}")
            return []

        # Get LORA details from database
        lora_ids = [item['lora_id'] for item in filtered_results]
        similarity_map = {item['lora_id']: item['similarity'] for item in filtered_results}

        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        placeholders = ','.join(['%s'] * len(lora_ids))
        cursor.execute(f"""
            SELECT id, name, description, trigger_words, mode, noise_stage, category, preview_url
            FROM lora_metadata
            WHERE id IN ({placeholders})
            AND (enabled = 1 OR enabled = TRUE)
        """, lora_ids)

        lora_data = cursor.fetchall()
        cursor.close()
        conn.close()

        # Build response
        video_loras = []
        for lora in lora_data:
            trigger_words = lora.get('trigger_words')
            if isinstance(trigger_words, str):
                try:
                    trigger_words = json.loads(trigger_words)
                except:
                    trigger_words = []
            if not trigger_words:
                trigger_words = []

            video_loras.append(VideoLoraRecommendation(
                lora_id=lora['id'],
                name=lora['name'],
                description=lora.get('description'),
                trigger_words=trigger_words,
                mode=lora['mode'],
                noise_stage=lora['noise_stage'],
                category=lora.get('category'),
                similarity=similarity_map.get(lora['id'], 0.0),
                preview_url=lora.get('preview_url')
            ))

        # Sort by similarity
        video_loras.sort(key=lambda x: x.similarity, reverse=True)

        logger.info(f"Found {len(video_loras)} video LORAs via semantic search")
        return video_loras[:top_k]

    except Exception as e:
        logger.error(f"Video LORA search failed: {e}", exc_info=True)
        return []


async def _search_reference_images(
    embedding_service,
    query: str,
    top_k: int,
    min_similarity: float = 0.6
) -> list[dict]:
    """Search for similar reference images using semantic search"""
    try:
        # Search using existing embedding service
        results = await embedding_service.search_similar_resources(
            query=query,
            top_k=top_k * 3  # Get more results for filtering
        )

        if not results:
            logger.warning("No reference images found via semantic search")
            return []

        # Filter by similarity threshold
        filtered_results = [r for r in results if r.get('similarity', 0.0) >= min_similarity]

        if not filtered_results:
            logger.warning(f"No reference images above similarity threshold {min_similarity}")
            return []

        # Get resource details from database
        resource_ids = [item['resource_id'] for item in filtered_results]
        similarity_map = {item['resource_id']: item['similarity'] for item in filtered_results}

        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        placeholders = ','.join(['%s'] * len(resource_ids))
        cursor.execute(f"""
            SELECT id, prompt, url
            FROM resources
            WHERE id IN ({placeholders})
        """, resource_ids)

        resources = cursor.fetchall()
        cursor.close()
        conn.close()

        # Build response
        images = []
        for resource in resources:
            images.append({
                "resource_id": resource['id'],
                "prompt": resource['prompt'] or '',
                "url": resource['url'],
                "similarity": similarity_map.get(resource['id'], 0.0)
            })

        # Sort by similarity
        images.sort(key=lambda x: x['similarity'], reverse=True)

        logger.info(f"Found {len(images)} reference images via semantic search")
        return images[:top_k]

    except Exception as e:
        logger.error(f"Reference image search failed: {e}", exc_info=True)
        return []


# ============================================================================
# Advanced Workflow API - Phase 2: SeeDream Editing
# ============================================================================

class SeeDreamEditRequest(BaseModel):
    """SeeDream image editing request"""
    scene_image: str = Field(..., description="Scene image (base64 or URL)")
    reference_face: Optional[str] = Field(None, description="Reference face image (base64 or URL)")
    mode: Literal["face_only", "face_wearings", "full_body"] = Field(
        ..., description="Edit mode"
    )
    enable_face_swap: bool = Field(default=True, description="Enable Reactor face swap before SeeDream")
    prompt: Optional[str] = Field(None, description="Custom prompt for SeeDream")
    size: str = Field(default="1024x1024", description="Output size")
    seed: Optional[int] = Field(None, description="Random seed")


class SeeDreamEditResponse(BaseModel):
    """SeeDream image editing response"""
    url: str
    edit_mode: str
    face_swapped: bool
    size: str
    seed: Optional[int]
    error: Optional[str] = None


@router.post("/workflow/seedream-edit", response_model=SeeDreamEditResponse)
async def seedream_edit(req: SeeDreamEditRequest, _=Depends(verify_api_key)):
    """
    Edit image using SeeDream with three modes.

    Modes:
    - face_only: Only face replacement
    - face_wearings: Face + accessories (jewelry, glasses, hair accessories)
    - full_body: Face + accessories + clothing

    This is Phase 2 of the advanced workflow system.
    """
    try:
        import base64
        import requests as http_requests
        from api.config import UPLOADS_DIR

        # Import SeeDream helper from image.py
        from api.routes.image import (
            _crop_and_resize, _save_result_image, _call_byteplus,
            _parse_size, FORGE_URL, SEEDREAM_MODEL, SCENE_SWAP_DEFAULT_PROMPT
        )

        # Decode or fetch scene image
        if req.scene_image.startswith('data:image'):
            scene_b64 = req.scene_image.split(',')[1]
            scene_data = base64.b64decode(scene_b64)
        elif req.scene_image.startswith('http://') or req.scene_image.startswith('https://'):
            resp = http_requests.get(req.scene_image, timeout=30)
            if resp.status_code != 200:
                raise HTTPException(400, f"Failed to fetch scene image: {resp.status_code}")
            scene_data = resp.content
        elif '/' not in req.scene_image and '.' in req.scene_image:
            # Local filename (e.g., "abc123.png")
            local_path = UPLOADS_DIR / req.scene_image
            if local_path.exists():
                scene_data = local_path.read_bytes()
            else:
                raise HTTPException(400, f"Local file not found: {req.scene_image}")
        else:
            # Try base64 decode as fallback
            try:
                scene_data = base64.b64decode(req.scene_image)
            except Exception as e:
                raise HTTPException(400, f"Invalid scene_image format: {e}")

        # Parse size
        size = _parse_size(req.size)
        output_w, output_h = map(int, size.lower().split("x"))
        crop_w = min(output_w, 1024)
        crop_h = min(output_h, 1024)

        # Preserve aspect ratio
        if output_w != crop_w or output_h != crop_h:
            ratio = output_w / output_h
            if crop_w / crop_h > ratio:
                crop_w = int(crop_h * ratio)
            else:
                crop_h = int(crop_w / ratio)
        target_w, target_h = crop_w, crop_h

        # Crop scene image
        scene_cropped = _crop_and_resize(scene_data, target_w, target_h)
        scene_b64 = base64.b64encode(scene_cropped).decode()

        face_swapped = False
        swapped_data = scene_cropped
        face_b64 = None  # Will be set if reference face is provided
        face_data = None

        # Step 1: Load reference face (if provided)
        if req.reference_face:
            # Decode or fetch reference face
            if req.reference_face.startswith('data:image'):
                face_b64 = req.reference_face.split(',')[1]
                face_data = base64.b64decode(face_b64)
            elif req.reference_face.startswith('http://') or req.reference_face.startswith('https://'):
                resp = http_requests.get(req.reference_face, timeout=30)
                if resp.status_code != 200:
                    logger.warning(f"Failed to fetch reference face: {resp.status_code}")
                    face_data = None
                else:
                    face_data = resp.content
            elif req.reference_face.startswith('/uploads/'):
                # Path like "/uploads/filename.jpg"
                filename = req.reference_face.split('/')[-1]
                local_path = UPLOADS_DIR / filename
                if local_path.exists():
                    face_data = local_path.read_bytes()
                else:
                    logger.warning(f"Local file not found: {req.reference_face}")
                    face_data = None
            elif '/' not in req.reference_face and '.' in req.reference_face:
                # Local filename (e.g., "abc123.png")
                local_path = UPLOADS_DIR / req.reference_face
                if local_path.exists():
                    face_data = local_path.read_bytes()
                else:
                    logger.warning(f"Local file not found: {req.reference_face}")
                    face_data = None
            else:
                # Try base64 decode as fallback
                try:
                    face_data = base64.b64decode(req.reference_face)
                except Exception as e:
                    logger.warning(f"Failed to decode reference_face as base64: {e}")
                    face_data = None

            if face_data:
                # Crop face image
                face_cropped = _crop_and_resize(face_data, target_w, target_h)
                face_b64 = base64.b64encode(face_cropped).decode()

                # Call Reactor (only if face swap is enabled)
                if req.enable_face_swap:
                    reactor_payload = {
                        "source_image": face_b64,
                        "target_image": scene_b64,
                        "source_faces_index": [0],
                        "face_index": [0],
                        "model": "inswapper_128.onnx",
                        "face_restorer": "CodeFormer",
                        "restorer_visibility": 1,
                        "codeformer_weight": 0.7,
                        "restore_first": 1,
                        "upscaler": "None",
                        "scale": 1,
                        "upscale_visibility": 1,
                        "device": "CUDA",
                        "mask_face": 1,
                        "det_thresh": 0.5,
                        "det_maxnum": 0,
                    }

                    logger.info(f"SeeDream Edit: Reactor face swap ({target_w}x{target_h})...")
                    try:
                        reactor_resp = http_requests.post(
                            f"{FORGE_URL}/reactor/image", json=reactor_payload, timeout=120
                        )

                        if reactor_resp.status_code == 200:
                            swapped_b64 = reactor_resp.json()["image"]
                            swapped_data = base64.b64decode(swapped_b64)
                            face_swapped = True
                            logger.info("Reactor face swap completed")
                        else:
                            logger.warning(f"Reactor failed: {reactor_resp.status_code}, using original")
                    except Exception as e:
                        logger.warning(f"Reactor error: {e}, using original")

        # Step 2: SeeDream editing with mode-specific prompts
        swapped_b64_out = base64.b64encode(swapped_data).decode()

        # Build prompt based on mode
        if req.prompt:
            full_prompt = req.prompt
        else:
            if req.mode == "face_only":
                full_prompt = "edit image 2, keep the position and pose of image 2, swap face to image 1, only change the face, keep everything else exactly the same including clothing, accessories, background"
            elif req.mode == "face_wearings":
                full_prompt = "edit image 2, keep the position and pose of image 2, swap face to image 1, change face and accessories (jewelry, glasses, hair accessories) to match image 1, keep clothing and background the same"
            elif req.mode == "full_body":
                full_prompt = "edit image 2, keep the position and pose of image 2, swap face to image 1, change face, accessories, and clothing to match image 1, keep background the same"
            else:
                full_prompt = SCENE_SWAP_DEFAULT_PROMPT

        # Prepare image list for SeeDream multiref
        if req.reference_face and face_b64:
            # We have a reference face - always pass two images to SeeDream
            image_list = [
                f"data:image/jpeg;base64,{face_b64}",        # Image 1: Reference face
                f"data:image/jpeg;base64,{swapped_b64_out}", # Image 2: Scene (possibly face-swapped)
            ]
            logger.info(f"SeeDream: Using 2 images (reference + scene), face_swapped={face_swapped}")
        else:
            # No face reference, just edit the scene
            image_list = [f"data:image/jpeg;base64,{swapped_b64_out}"]
            logger.info("SeeDream: Using 1 image (scene only)")

        # Call SeeDream
        model_name = "seedream-5-0-lite"  # Use lite version for faster processing
        payload = {
            "model": model_name,
            "prompt": full_prompt,
            "image": image_list,
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        if req.seed is not None:
            payload["seed"] = req.seed

        logger.info(f"SeeDream Edit: mode={req.mode}, prompt={full_prompt[:100]}")
        logger.info(f"SeeDream payload: model={model_name}, size={size}, num_images={len(image_list)}, seed={req.seed}")

        try:
            url = _call_byteplus(payload)
            logger.info(f"SeeDream edit completed: {url[:120]}")

            return SeeDreamEditResponse(
                url=url,
                edit_mode=req.mode,
                face_swapped=face_swapped,
                size=size,
                seed=req.seed
            )
        except HTTPException as e:
            # Fallback: return Reactor result if available
            if face_swapped:
                logger.warning(f"SeeDream failed, returning Reactor result as fallback")
                url = _save_result_image(swapped_b64_out)
                return SeeDreamEditResponse(
                    url=url,
                    edit_mode=req.mode,
                    face_swapped=face_swapped,
                    size=f"{target_w}x{target_h}",
                    seed=req.seed,
                    error="SeeDream failed, using Reactor result"
                )
            else:
                # No fallback available
                logger.error(f"SeeDream failed and no Reactor result: {e}")
                url = _save_result_image(scene_b64)
                return SeeDreamEditResponse(
                    url=url,
                    edit_mode=req.mode,
                    face_swapped=False,
                    size=f"{target_w}x{target_h}",
                    seed=req.seed,
                    error=f"SeeDream failed: {str(e)}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SeeDream edit failed: {e}", exc_info=True)
        raise HTTPException(500, f"SeeDream edit failed: {str(e)}")


# ============================================================================
# Advanced Workflow API - Phase 3: Complete Workflow Orchestration
# ============================================================================

class FirstFrameSource(str, Enum):
    """First frame source options (only for face_reference/full_body_reference modes)"""
    GENERATE = "generate"
    SELECT_EXISTING = "select_existing"


class WorkflowGenerateRequest(BaseModel):
    """Advanced workflow generation request"""
    mode: Literal["face_reference", "full_body_reference", "first_frame"] = Field(
        ..., description="Workflow mode"
    )
    user_prompt: str = Field(..., min_length=1, max_length=2000, description="User prompt")
    reference_image: Optional[str] = Field(None, description="Reference image (base64 or URL)")

    # Video generation parameters (top-level, from v2 frontend)
    resolution: Optional[str] = Field(None, description="Resolution (e.g., '480p', '720p', '1080p')")
    aspect_ratio: Optional[str] = Field(None, description="Aspect ratio (e.g., '16:9', '3:4')")
    duration: Optional[int] = Field(None, description="Duration in seconds")

    # First frame acquisition (only used for face_reference/full_body_reference modes)
    first_frame_source: Optional[FirstFrameSource] = Field(
        default=None,
        description="How to obtain first frame (only for face_reference/full_body_reference modes). If mode is first_frame, this field is ignored."
    )
    uploaded_first_frame: Optional[str] = Field(None, description="Uploaded first frame (base64 or URL) - only used when mode=first_frame")
    selected_image_url: Optional[str] = Field(None, description="Selected existing image URL (deprecated, use first_frame_source=select_existing)")

    # Auto features
    auto_analyze: bool = Field(default=True, description="Auto analyze and recommend LORAs")
    auto_lora: bool = Field(default=True, description="Auto select LORAs")
    auto_prompt: bool = Field(default=True, description="Auto optimize prompts")

    # T2I parameters (for generate mode)
    t2i_params: Optional[dict] = Field(default=None, description="T2I generation parameters")

    # SeeDream parameters
    seedream_params: Optional[dict] = Field(
        default_factory=lambda: {
            "edit_mode": "face_wearings",
            "enable_reactor_first": True,
            "strength": 0.8
        },
        description="SeeDream editing parameters"
    )

    # Video generation parameters
    video_params: Optional[dict] = Field(
        default_factory=lambda: {
            "model": "A14B",
            "resolution": "480p_3:4",
            "duration": "5s",
            "steps": 20,
            "cfg": 6.0
        },
        description="Video generation parameters"
    )

    # Internal configuration (for debugging, disabled in production)
    internal_config: Optional[dict] = Field(
        default=None,
        description="Internal configuration parameters (debug only)"
    )


class WorkflowStage(BaseModel):
    """Workflow stage status"""
    name: str
    status: Literal["pending", "running", "completed", "failed"]
    sub_stage: Optional[str] = None
    error: Optional[str] = None


class WorkflowGenerateResponse(BaseModel):
    """Advanced workflow generation response"""
    workflow_id: str
    status: Literal["queued", "running", "completed", "failed"]
    current_stage: str
    stages: list[WorkflowStage]
    chain_id: Optional[str] = None
    final_video_url: Optional[str] = None
    first_frame_url: Optional[str] = None
    edited_frame_url: Optional[str] = None
    estimated_total_time: Optional[int] = None
    error: Optional[str] = None
    progress: Optional[float] = Field(default=None, description="Overall progress (0.0-1.0)")
    video_progress: Optional[float] = Field(default=None, description="Video generation progress (0.0-1.0)")
    current_step: Optional[int] = Field(default=None, description="Current step in video generation")
    max_step: Optional[int] = Field(default=None, description="Total steps in current stage")
    total_steps: Optional[int] = Field(default=None, description="Total steps across all stages")
    completed_steps: Optional[int] = Field(default=None, description="Completed steps from previous stages")


@router.post("/workflow/generate-advanced", response_model=WorkflowGenerateResponse)
async def generate_advanced_workflow(req: WorkflowGenerateRequest, _=Depends(verify_api_key)):
    """
    Generate video using advanced workflow with three modes.

    This orchestrates the complete workflow:
    1. Analyze prompt and recommend LORAs
    2. Acquire first frame (upload/generate/select)
    3. Edit first frame with SeeDream
    4. Generate video with Chain workflow

    This is Phase 3 of the advanced workflow system.
    """
    try:
        import uuid
        import asyncio
        from api.main import task_manager
        from api.routes.workflow_executor import _execute_workflow

        # Generate workflow ID
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"

        # Log incoming request parameters
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - Incoming request:")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - mode: {req.mode}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - user_prompt: {req.user_prompt}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - first_frame_source: {req.first_frame_source}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - resolution: {req.resolution}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - aspect_ratio: {req.aspect_ratio}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - duration: {req.duration}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - auto_analyze: {req.auto_analyze}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - auto_lora: {req.auto_lora}")
        logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - auto_prompt: {req.auto_prompt}")
        if req.t2i_params:
            logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - t2i_params: {json.dumps(req.t2i_params, ensure_ascii=False)}")
        if req.seedream_params:
            logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - seedream_params: {json.dumps(req.seedream_params, ensure_ascii=False)}")
        if req.video_params:
            logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - video_params: {json.dumps(req.video_params, ensure_ascii=False)}")
        if req.internal_config:
            logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - internal_config: {json.dumps(req.internal_config, ensure_ascii=False)}")

        # Process top-level resolution/aspect_ratio/duration fields (from v2 frontend)
        # Merge them into internal_config.stage4_video.generation
        if req.resolution or req.aspect_ratio or req.duration:
            if not req.internal_config:
                req.internal_config = {}
            if "stage4_video" not in req.internal_config:
                req.internal_config["stage4_video"] = {}
            if "generation" not in req.internal_config["stage4_video"]:
                req.internal_config["stage4_video"]["generation"] = {}

            # Build resolution string from resolution + aspect_ratio
            if req.resolution and req.aspect_ratio:
                resolution_str = f"{req.resolution}_{req.aspect_ratio.replace(':', '_')}"
                req.internal_config["stage4_video"]["generation"]["resolution"] = resolution_str
                logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - Merged resolution: {resolution_str}")

            # Merge duration
            if req.duration:
                duration_str = f"{req.duration}s"
                req.internal_config["stage4_video"]["generation"]["duration"] = duration_str
                logger.info(f"[WORKFLOW_PARAMS] {workflow_id} - Merged duration: {duration_str}")

        # Initialize stages
        stages = [
            WorkflowStage(name="prompt_analysis", status="pending"),
            WorkflowStage(name="first_frame_acquisition", status="pending"),
            WorkflowStage(name="seedream_edit", status="pending"),
            WorkflowStage(name="video_generation", status="pending")
        ]

        # Save initial workflow state to Redis
        await task_manager.redis.hset(f"workflow:{workflow_id}", mapping={
            "status": "running",
            "current_stage": "prompt_analysis",
            "mode": req.mode,
            "user_prompt": req.user_prompt,
            "first_frame_source": req.first_frame_source.value if req.first_frame_source else "",
            "created_at": str(int(asyncio.get_event_loop().time())),
            "internal_config": json.dumps(req.internal_config) if req.internal_config else "{}"
        })

        logger.info(f"Advanced workflow {workflow_id} created: mode={req.mode}, source={req.first_frame_source}")

        # Start async orchestration
        asyncio.create_task(_execute_workflow(workflow_id, req, task_manager))

        return WorkflowGenerateResponse(
            workflow_id=workflow_id,
            status="running",
            current_stage="prompt_analysis",
            stages=stages,
            estimated_total_time=120
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Advanced workflow generation failed: {e}", exc_info=True)
        raise HTTPException(500, f"Advanced workflow generation failed: {str(e)}")


@router.get("/workflow/status/{workflow_id}")
async def get_workflow_status(workflow_id: str, _=Depends(verify_api_key)):
    """
    Get workflow status by ID.

    Returns current stage, progress, and results.
    """
    import time
    start_time = time.time()

    try:
        from api.main import task_manager

        # Query workflow state from Redis
        t1 = time.time()
        workflow_data = await task_manager.redis.hgetall(f"workflow:{workflow_id}")
        logger.info(f"[{workflow_id}] Redis hgetall took {(time.time()-t1)*1000:.2f}ms")

        if not workflow_data:
            raise HTTPException(404, f"Workflow {workflow_id} not found")

        status = workflow_data.get("status", "running")
        current_stage = workflow_data.get("current_stage", "prompt_analysis")

        # Build stages list
        stages = []
        stage_names = ["prompt_analysis", "first_frame_acquisition", "seedream_edit", "video_generation"]

        for stage_name in stage_names:
            stage_status = workflow_data.get(f"stage_{stage_name}", "pending")
            stage_error = workflow_data.get(f"stage_{stage_name}_error")
            stage_details = workflow_data.get(f"stage_{stage_name}_details")

            stages.append(WorkflowStage(
                name=stage_name,
                status=stage_status,
                sub_stage=stage_details,
                error=stage_error
            ))

        # Calculate overall progress based on stages
        progress = None
        video_progress = None
        current_step = None
        max_step = None
        total_steps = None
        completed_steps = None

        if status == "completed":
            progress = 1.0
            video_progress = 1.0
        elif status == "running":
            # Calculate progress based on completed stages
            completed_count = sum(1 for s in stages if s.status == "completed")
            total_stages = len(stages)
            stage_progress = completed_count / total_stages

            # If in video_generation stage, get detailed progress from chain/task
            if current_stage == "video_generation":
                chain_id = workflow_data.get("chain_id")
                if chain_id:
                    t2 = time.time()
                    chain_data = await task_manager.redis.hgetall(f"chain:{chain_id}")
                    logger.info(f"[{workflow_id}] Chain hgetall took {(time.time()-t2)*1000:.2f}ms")
                    if chain_data:
                        # Get task progress
                        current_task_id = chain_data.get("current_task_id")
                        if current_task_id:
                            t3 = time.time()
                            task_data = await task_manager.redis.hgetall(f"task:{current_task_id}")
                            logger.info(f"[{workflow_id}] Task hgetall took {(time.time()-t3)*1000:.2f}ms")
                            if task_data:
                                task_progress = float(task_data.get("progress", 0))
                                video_progress = task_progress
                                # Get detailed step info
                                current_step = int(task_data.get("current_step", 0)) if task_data.get("current_step") else None
                                max_step = int(task_data.get("max_step", 0)) if task_data.get("max_step") else None
                                total_steps = int(task_data.get("total_steps", 0)) if task_data.get("total_steps") else None
                                completed_steps = int(task_data.get("completed_steps", 0)) if task_data.get("completed_steps") else None
                                # Overall progress = stage progress + current stage progress
                                progress = (completed_count + task_progress) / total_stages
                            else:
                                progress = stage_progress
                        else:
                            progress = stage_progress
                    else:
                        progress = stage_progress
                else:
                    progress = stage_progress
            else:
                progress = stage_progress

        return WorkflowGenerateResponse(
            workflow_id=workflow_id,
            status=status,
            current_stage=current_stage,
            stages=stages,
            chain_id=workflow_data.get("chain_id"),
            final_video_url=workflow_data.get("final_video_url"),
            first_frame_url=workflow_data.get("first_frame_url"),
            edited_frame_url=workflow_data.get("edited_frame_url"),
            error=workflow_data.get("error"),
            progress=progress,
            video_progress=video_progress,
            current_step=current_step,
            max_step=max_step,
            total_steps=total_steps,
            completed_steps=completed_steps
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get workflow status: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to get workflow status: {str(e)}")
    finally:
        total_time = (time.time() - start_time) * 1000
        logger.info(f"[{workflow_id}] Total request time: {total_time:.2f}ms")


# ============================================================================
# Custom Workflow API (existing)
# ============================================================================


@router.post("/workflow/run", response_model=GenerateResponse)
async def run_custom_workflow(
    workflow_name: str = Body(...),
    params: dict = Body(...),
    model: str = Body("a14b"),
    _=Depends(verify_api_key)
):
    """
    Run a custom ComfyUI workflow with parameter substitution.

    Args:
        workflow_name: Name of workflow file (without .json extension)
        params: Dictionary of parameters to substitute in workflow
        model: Model type (a14b or 5b)

    Example params:
    {
        "prompt": "A woman walking",
        "width": 832,
        "height": 480,
        "duration": 3.3,  // Will be converted to num_frames (81 @ 24fps)
        "fps": 24,
        "steps": 20,
        "cfg": 6.0,
        "shift": 5.0,
        "seed": -1
    }

    Note: You can use either "duration" (in seconds) or "num_frames" directly.
    If "duration" is provided, it will be converted to num_frames using 4n+1 format.
    """
    from api.main import task_manager
    from api.config import PROJECT_ROOT

    # Convert duration to num_frames if provided
    if "duration" in params and "num_frames" not in params:
        duration = float(params["duration"])
        fps = int(params.get("fps", 24))
        # Convert to 4n+1 format
        frames = max(round(duration * fps), 1)
        frames = round((frames - 1) / 4) * 4 + 1
        params["num_frames"] = max(frames, 5)
        logger.info(f"Converted duration {duration}s @ {fps}fps to {params['num_frames']} frames")

    # Load workflow file
    workflow_path = PROJECT_ROOT / "workflows" / f"{workflow_name}.json"
    if not workflow_path.exists():
        raise HTTPException(404, f"Workflow '{workflow_name}' not found")

    try:
        with open(workflow_path) as f:
            workflow = json.load(f)
    except Exception as e:
        raise HTTPException(400, f"Failed to load workflow: {e}")

    # Detect ComfyUI UI export format (has "nodes" list instead of node-id keys)
    # ComfyUI /prompt API requires API format: {"1": {"class_type": ..., "inputs": ...}, ...}
    # UI format files (exported from ComfyUI browser) cannot be used directly.
    if "nodes" in workflow and isinstance(workflow.get("nodes"), list):
        raise HTTPException(
            400,
            f"Workflow '{workflow_name}' is in ComfyUI UI export format. "
            "Please use the API format workflow (export via 'Save (API format)' in ComfyUI). "
            "UI format files contain 'nodes'/'links' lists and cannot be submitted to /prompt directly."
        )

    # Validate model
    try:
        model_type = ModelType(model)
    except ValueError:
        raise HTTPException(400, f"Invalid model: {model}")

    # Check if ComfyUI instance is alive
    client = task_manager.clients.get(model_type.value)
    if not client or not await client.is_alive():
        raise HTTPException(503, f"ComfyUI {model_type.value} instance is not available")

    # Substitute parameters in workflow
    # Supports both UI format (nodes list) and API format (node-id dict)

    is_api_format = all(
        isinstance(v, dict) and "class_type" in v
        for v in workflow.values()
        if isinstance(v, dict)
    )

    # 1. Simple placeholder replacement: ${param_name}
    workflow_str = json.dumps(workflow)
    for key, value in params.items():
        placeholder = f"${{{key}}}"
        workflow_str = workflow_str.replace(placeholder, str(value))
    workflow = json.loads(workflow_str)

    if is_api_format:
        # --- API format: {"node_id": {"class_type": ..., "inputs": {...}, "_meta": {"title": ...}}} ---
        # Map params to (class_type, input_key) pairs for direct injection
        api_param_rules = {
            "num_frames": [
                ("PainterI2V", "length"),
                ("PainterLongVideo", "length"),
            ],
            "steps": [
                ("WanMoeKSamplerAdvanced", "steps"),
                ("KSampler", "steps"),
                ("KSamplerAdvanced", "steps"),
            ],
            "cfg": [
                ("WanMoeKSamplerAdvanced", "cfg"),
                ("KSampler", "cfg"),
            ],
            "shift": [
                ("PrimitiveFloat", "value"),  # "Sigma Shift" node
            ],
            "seed": [
                ("Seed (rgthree)", "seed"),
            ],
            "sampler_name": [
                ("WanMoeKSamplerAdvanced", "sampler_name"),
            ],
            "scheduler": [
                ("WanMoeKSamplerAdvanced", "scheduler"),
            ],
            "fps": [
                ("VHS_VideoCombine", "frame_rate"),
            ],
            "motion_amplitude": [
                ("FloatConstant", "value"),  # "motion amplitude" node (only one in workflow)
            ],
            "motion_frames": [
                ("INTConstant", "value"),  # "motion_frames" node (only one in workflow)
            ],
        }

        for param_key, param_value in params.items():
            rules = api_param_rules.get(param_key, [])
            for class_type, input_key in rules:
                for nid, node in workflow.items():
                    if node.get("class_type") == class_type and input_key in node.get("inputs", {}):
                        old = node["inputs"][input_key]
                        # Don't overwrite link references (lists)
                        if isinstance(old, list):
                            continue
                        node["inputs"][input_key] = param_value
                        logger.debug("API param %s=%s -> node %s (%s) input %s", param_key, param_value, nid, class_type, input_key)

        # Handle prompt injection: find CLIPTextEncode nodes titled "Positive encode"
        prompt_val = params.get("prompt")
        if prompt_val:
            for nid, node in workflow.items():
                title = node.get("_meta", {}).get("title", "")
                if node.get("class_type") == "CLIPTextEncode" and "positive" in title.lower():
                    node["inputs"]["text"] = prompt_val

        # Handle per-segment prompts: prompt_1, prompt_2, prompt_3, prompt_4
        # Override the "Prompt N" display nodes (easy showAnything) by replacing
        # their "anything" input with the user's text, disconnecting StorySplitNode
        for i in range(1, 5):
            seg_prompt = params.get(f"prompt_{i}")
            if seg_prompt:
                target_title = f"Prompt {i}"
                for nid, node in workflow.items():
                    title = node.get("_meta", {}).get("title", "")
                    if title == target_title and node.get("class_type") == "easy showAnything":
                        node["inputs"]["anything"] = seg_prompt
                        logger.debug("Injected prompt_%d into node %s", i, nid)

        # Handle per-segment frame counts: num_frames_1, num_frames_2, num_frames_3, num_frames_4
        # Also support duration_1..4 (converted to frames)
        # Segment order: PainterI2V first, then PainterLongVideo in title order
        painter_nodes = []
        for nid, node in workflow.items():
            ct = node.get("class_type", "")
            if ct == "PainterI2V" and not isinstance(node.get("inputs", {}).get("length"), list):
                painter_nodes.append((0, nid, node))  # segment 1 is always PainterI2V
            elif ct == "PainterLongVideo" and not isinstance(node.get("inputs", {}).get("length"), list):
                painter_nodes.append((1, nid, node))  # segments 2+ are PainterLongVideo
        # Sort: PainterI2V first, then PainterLongVideo by node id
        painter_nodes.sort(key=lambda x: (x[0], x[1]))

        for seg_idx, (_, nid, node) in enumerate(painter_nodes):
            seg_num = seg_idx + 1
            # Check for duration_N first, then num_frames_N
            seg_duration = params.get(f"duration_{seg_num}")
            seg_frames = params.get(f"num_frames_{seg_num}")
            if seg_duration is not None and seg_frames is None:
                duration = float(seg_duration)
                fps = int(params.get("fps", 24))
                frames = max(round(duration * fps), 1)
                frames = round((frames - 1) / 4) * 4 + 1
                seg_frames = max(frames, 5)
                logger.info(f"Segment {seg_num}: duration {duration}s -> {seg_frames} frames")
            if seg_frames is not None:
                node["inputs"]["length"] = int(seg_frames)
                logger.debug("Per-segment frames: segment %d node %s -> %s frames", seg_num, nid, seg_frames)

        # Handle input image: inject uploaded filename into LoadImage nodes
        input_image = params.get("_input_image")
        if input_image:
            for nid, node in workflow.items():
                if node.get("class_type") == "LoadImage" and "image" in node.get("inputs", {}):
                    node["inputs"]["image"] = input_image
                    logger.info("Injected input image '%s' into LoadImage node %s", input_image, nid)

    else:
        # --- UI format: {"nodes": [...], "links": [...]} ---
        node_title_map = {
            "num_frames": "Lenght",
            "length": "Lenght",
            "lenght": "Lenght",
            "steps": "Steps",
            "width": "WIDTH",
            "height": "HEIGHT",
            "prompt": "prompt_1",
            "prompt_1": "prompt_1",
            "prompt_2": "prompt_2",
            "prompt_3": "prompt_3",
            "prompt_4": "prompt_4",
        }

        for param_key, param_value in params.items():
            node_title = node_title_map.get(param_key.lower(), param_key)

            for node in workflow.get("nodes", []):
                title = node.get("title", "")

                if node.get("type") == "mxSlider" and title == node_title:
                    if "widgets_values" in node and len(node["widgets_values"]) >= 2:
                        node["widgets_values"][0] = int(param_value) if isinstance(param_value, (int, float)) else param_value
                        node["widgets_values"][1] = int(param_value) if isinstance(param_value, (int, float)) else param_value

                elif "prompt" in title.lower() and "prompt" in param_key.lower():
                    if "widgets_values" in node:
                        node["widgets_values"][0] = str(param_value)

    # Create task
    task_id = await task_manager.create_task(
        GenerateMode.T2V,  # Use T2V as default mode
        model_type,
        workflow,
        params=params
    )

    return GenerateResponse(task_id=task_id, status=TaskStatus.QUEUED)


@router.post("/workflow/run-with-image", response_model=GenerateResponse)
async def run_workflow_with_image(
    workflow_name: str = Form(...),
    params: str = Form(...),
    model: str = Form("a14b"),
    image: Optional[UploadFile] = File(None),
    _=Depends(verify_api_key)
):
    """Run workflow with optional image upload (FormData)."""
    from api.main import task_manager
    from api.config import PROJECT_ROOT

    try:
        params_dict = json.loads(params)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid params JSON: {e}")

    # If image provided, upload to ComfyUI and inject into LoadImage nodes
    uploaded_filename = None
    if image and image.filename:
        image_data = await image.read()
        if len(image_data) > 0:
            model_type_val = model
            try:
                mt = ModelType(model_type_val)
            except ValueError:
                raise HTTPException(400, f"Invalid model: {model_type_val}")

            client = task_manager.clients.get(mt.value)
            if not client or not await client.is_alive():
                raise HTTPException(503, f"ComfyUI {mt.value} instance is not available")

            # Upload image to ComfyUI input directory
            upload_result = await client.upload_image(image_data, image.filename)
            uploaded_filename = upload_result.get("name", image.filename)
            logger.info("Uploaded image to ComfyUI: %s", uploaded_filename)

    # Inject uploaded_filename into params so the main logic can use it
    if uploaded_filename:
        params_dict["_input_image"] = uploaded_filename

    # Delegate to the main workflow run logic
    return await run_custom_workflow(
        workflow_name=workflow_name,
        params=params_dict,
        model=model,
    )


@router.get("/workflow/list")
async def list_workflows(_=Depends(verify_api_key)):
    """List all available workflow files."""
    from api.config import PROJECT_ROOT

    workflows_dir = PROJECT_ROOT / "workflows"
    if not workflows_dir.exists():
        return {"workflows": []}

    workflows = []
    for file in workflows_dir.glob("*.json"):
        workflows.append({
            "name": file.stem,
            "filename": file.name,
            "size": file.stat().st_size
        })

    return {"workflows": workflows}
