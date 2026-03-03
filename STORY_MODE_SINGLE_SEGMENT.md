# Story Mode Single Segment Generation - Implementation Summary

## Overview
Implemented support for Story mode in the "单独生成" (single generate) button for video segments. This allows individual segments (segment 2+) to maintain identity consistency by using both the previous frame AND the first segment's input image as references.

## Problem
Previously, the "单独生成" button only supported standard I2V generation, which meant:
- Segments 2+ could not use Story mode's `PainterLongVideo` node
- No identity consistency across segments when generating individually
- Users had to generate the entire chain to get Story mode benefits

## Solution
Modified the frontend and backend to detect Story mode and use the chain API with proper reference images for single segment generation.

## Implementation Details

### 1. Frontend Changes (`api/static/index.html`)

**Location**: Lines 2420-2527 in `generateSingleSegment()` function

**Key Changes**:
- Detect Story mode checkbox state: `document.getElementById('chain-story-mode').checked`
- For Story mode segments 2+, use chain API instead of direct I2V API
- Fetch the initial reference image (first segment's input) from:
  - `chainSelectedFile` (if available)
  - `localStorage.getItem('wan22_chain_image')` (fallback)
- Pass both images to chain API:
  - `image`: previous segment's last frame (motion reference)
  - `initial_reference_image`: first segment's input image (identity anchor)
- Include Story mode parameters: `motion_frames`, `boundary`, `clip_preset`

**Code Flow**:
```javascript
if (isStoryMode && currentIndex > 0) {
    // Get previous segment's last frame
    const prevLastFrameUrl = window[`seg_${prevSegId}_last_frame_url`];

    // Get first segment's input image (identity anchor)
    let initialRefBlob = chainSelectedFile || await fetch(cachedImg).then(r => r.blob());

    // Fetch both images and append to FormData
    const prevFrameBlob = await fetch(prevLastFrameUrl).then(r => r.blob());
    fd.append('image', prevFrameBlob, 'previous_frame.png');
    fd.append('initial_reference_image', initialRefBlob, 'initial_reference.png');

    // Submit to chain API
    await fetch(BASE + '/api/v1/generate/chain', { method: 'POST', body: fd });
}
```

### 2. Backend API Changes (`api/routes/extend.py`)

**Location**: Lines 125-161, 205-209

**Key Changes**:
- Added `initial_reference_image: UploadFile = File(None)` parameter to `generate_chain()` endpoint
- Process and upload initial reference image to ComfyUI
- Pass `initial_ref_filename` to all segments in the chain

**Code Flow**:
```python
@router.post("/generate/chain", response_model=ChainResponse)
async def generate_chain(
    image: UploadFile = File(None),
    initial_reference_image: UploadFile = File(None),  # NEW
    params: str = Form(...),
    _=Depends(verify_api_key),
):
    # Upload initial reference image to ComfyUI
    if initial_reference_image:
        initial_ref_data = await initial_reference_image.read()
        upload_result = await client.upload_image(initial_ref_data, local_name)
        initial_ref_filename = upload_result.get("name", local_name)

    # Pass to all segments
    for seg in segments:
        if initial_ref_filename:
            seg["initial_ref_filename"] = initial_ref_filename
```

### 3. Chain Worker Changes (`api/services/task_manager.py`)

**Location**: Lines 588-589

**Key Changes**:
- Initialize `initial_ref_filename` from segments array instead of empty string
- This allows single segment generation to use the pre-uploaded initial reference image

**Before**:
```python
initial_ref_filename = ""
```

**After**:
```python
# Check if segments already have initial_ref_filename set (from single segment generation)
initial_ref_filename = segments[0].get("initial_ref_filename", "") if segments else ""
```

## Data Flow

### Full Chain Generation (existing behavior)
1. User uploads first frame image → stored in `chainSelectedFile`
2. Chain API receives image → uploads to ComfyUI as `image_filename`
3. First segment uses `PainterI2V` with `image_filename`
4. After first segment completes, extract first frame → set as `initial_ref_filename`
5. Subsequent segments use `PainterLongVideo` with both `previous_video` and `initial_reference_image`

### Single Segment Generation (new behavior)
1. User uploads first frame image → stored in `chainSelectedFile` and `localStorage`
2. User generates segment 1 individually → uses standard I2V or chain API
3. User clicks "单独生成" on segment 2+ with Story mode enabled:
   - Frontend fetches `chainSelectedFile` or localStorage cache
   - Frontend fetches previous segment's last frame
   - Frontend calls chain API with both images
4. Backend uploads both images to ComfyUI
5. Backend creates single-segment chain with `initial_ref_filename` set
6. Chain worker uses pre-set `initial_ref_filename` from segment dict
7. `_build_story_segment()` builds workflow with `PainterLongVideo` node
8. ComfyUI generates video with identity consistency

## Story Mode Parameters

When Story mode is enabled, these parameters are passed:
- `story_mode: true`
- `motion_frames`: Number of motion reference frames (default: 5)
- `boundary`: Identity boundary threshold (default: 0.9)
- `clip_preset`: CLIP model preset (default: "nsfw")
- `auto_continue: false` (for single segment generation)
- `transition: "none"` (no transition needed for single segment)

## Testing

To test the implementation:

1. Enable Story mode checkbox in the chain panel
2. Upload a first frame image
3. Generate segment 1 (can use "单独生成" or full chain)
4. Wait for segment 1 to complete
5. Click "单独生成" on segment 2
6. Verify that:
   - The request goes to `/api/v1/generate/chain` (not `/api/v1/generate/i2v`)
   - Both `image` and `initial_reference_image` are in the FormData
   - The generated video maintains identity consistency with segment 1
   - The status shows "Story 模式" in the UI

## Benefits

1. **Flexibility**: Users can generate segments individually while maintaining Story mode benefits
2. **Efficiency**: No need to regenerate entire chain if one segment needs adjustment
3. **Identity Consistency**: Individual segments maintain character/object identity across the video
4. **User Experience**: Seamless integration with existing UI - just enable Story mode checkbox

## Files Modified

1. `/home/gime/soft/wan22-service/api/static/index.html`
   - Lines 2420-2527: Added Story mode detection and chain API call for single segments

2. `/home/gime/soft/wan22-service/api/routes/extend.py`
   - Lines 125-161: Added `initial_reference_image` parameter and upload handling
   - Lines 205-209: Pass `initial_ref_filename` to all segments

3. `/home/gime/soft/wan22-service/api/services/task_manager.py`
   - Lines 588-589: Initialize `initial_ref_filename` from segments array

## Implementation Date
2026-03-03
