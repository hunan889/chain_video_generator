# Auto Memory

## Wan2.2 Video Service
- Project at `/home/gime/soft/wan22-service/`
- ComfyUI at `/home/gime/soft/ComfyUI` (v0.12.3, Python 3.11 venv)
- Custom nodes installed: WanVideoWrapper (Kijai), MultiGPU, VideoHelperSuite
- 8x RTX 4090, GPUs 0-2 allocated for Wan2.2, GPUs 3-7 in use by other services
- WanVideoWrapper node classes: WanVideoModelLoader, WanVideoVAELoader, LoadWanVideoT5TextEncoder, WanVideoTextEncode, WanVideoSampler, WanVideoDecode, WanVideoImageToVideoEncode, WanVideoLoraSelect, WanVideoSetLoRAs
- A14B two-stage: high_noise model (start_step=0, end_step=15) → low_noise model (start_step=15, end_step=-1), share text_embeds/image_embeds
- LoRA injection: WanVideoLoraSelect (chainable via prev_lora) → WanVideoSetLoRAs to apply to model
- Models not yet downloaded — run `scripts/download_models.sh`
- HuggingFace source: Kijai/wan2.2_comfyui repo for fp8_scaled models
- 下载失败的模型文件（0字节、CivitAI未授权JSON等）直接删除，不保留
