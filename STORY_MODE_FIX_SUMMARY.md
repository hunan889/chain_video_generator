# Story Mode Segment 2 Fix - Summary

## Problem
When generating Story Mode segments individually (clicking "单独生成" button), segment 2 and beyond were not correctly using the previous segment's last frame and initial reference image for identity consistency.

## Root Cause
The `list_tasks()` method in `api/services/task_manager.py` was missing the `last_frame_url` field in its response, even though:
- Redis had the data stored correctly
- The `get_task()` method included the field
- FFmpeg was extracting last frames successfully (283 PNG files in uploads/)

This caused the frontend to receive `null` for `last_frame_url`, preventing segment 2+ from accessing the previous segment's last frame.

## Fix Applied

### Backend Fix (api/services/task_manager.py)
**Line 164**: Added missing field to `list_tasks()` response:
```python
"last_frame_url": data.get("last_frame_url") or None,
```

**Commit**: `5b09c38` - "Fix: Add last_frame_url to list_tasks() response"

### Verification
✅ API now returns `last_frame_url` correctly:
```bash
curl -s -H "X-API-Key: wan22-default-key-change-me" http://localhost:8000/api/v1/tasks | jq '.[0].last_frame_url'
# Output: "/api/v1/results/3241c2566d3147789b93dbbdc18c5576.png"
```

## Frontend Logic (Already Implemented)

The frontend already has the correct logic in `api/static/index.html`:

### Segment 1 Generation (Lines 2512-2544)
- Uses `/api/v1/generate/i2v` endpoint
- Includes uploaded image
- Adds Story Mode parameters if enabled
- Caches initial reference image to localStorage

### Segment 2+ Generation (Lines 2428-2510)
- Uses `/api/v1/generate/chain` endpoint (NOT i2v)
- Includes previous segment's last frame as `image`
- Includes initial reference image as `initial_reference_image`
- Includes Story Mode parameters (motion_frames, boundary, clip_preset)

### Console Logging (Already in Place)
7 console.log statements track the execution path:
- Line 2421: Segment info and Story Mode status
- Line 2433: Previous segment info for Story Mode
- Line 2434: All window seg variables
- Line 2458: Initial reference image status
- Line 2510: Confirms Chain API usage
- Line 2514: First segment info
- Line 2557: Non-Story Mode segment 2+ info

## What Changed
**Before**: Frontend received `last_frame_url: null` → couldn't access previous segment's last frame → segment 2 generation failed or used wrong logic

**After**: Frontend receives `last_frame_url: "/api/v1/results/xxx.png"` → can access previous segment's last frame → segment 2 uses Chain API with correct parameters

## Testing Required

### Manual Test (See STORY_MODE_SEGMENT2_FIX_VERIFICATION.md)
1. Open http://localhost:8000 in browser
2. Open DevTools Console (F12)
3. Go to "长视频生成" tab
4. Enable "Story 模式" checkbox
5. Upload first frame image
6. Generate segment 1 (should use I2V)
7. Wait for completion, verify console shows last_frame_url stored
8. Generate segment 2 (should use Chain API)
9. Verify console shows correct logic path
10. Check that identity consistency works in generated videos

### Expected Console Output for Segment 2
```
[generateSingleSegment] segId=2, currentIndex=1, totalSegments=2, isStoryMode=true
[generateSingleSegment] Story Mode segment 2+: prevSegId=1, prevLastFrameUrl=/api/v1/results/...
[generateSingleSegment] All window seg variables: seg_1_video_url, seg_1_last_frame_url, seg_1_task_id
[generateSingleSegment] initialRefImage=found, cachedInitialRef=exists
[generateSingleSegment] Using Chain API for Story Mode segment 2+
```

## Files Modified

1. **api/services/task_manager.py** (Line 164)
   - Added `last_frame_url` to `list_tasks()` response

2. **STORY_MODE_SEGMENT2_DEBUG.md** (New)
   - Debug guide for console logging

3. **STORY_MODE_SEGMENT2_FIX_VERIFICATION.md** (New)
   - Complete verification test plan

## Related Issues

### Previous Session Work
- Implemented Story Mode single segment generation logic
- Added console logging for debugging
- Verified backend last frame extraction works
- Verified Redis storage works

### This Session Work
- Identified API response missing last_frame_url
- Fixed list_tasks() method
- Verified API now returns correct data
- Created verification test plan

## Success Criteria

✅ Backend extracts last frames (283 PNG files exist)
✅ Redis stores last_frame_url correctly
✅ API returns last_frame_url in responses
✅ Frontend has correct logic for segment 2 (Chain API)
✅ Frontend has console logging for debugging
⏳ Manual testing required to verify end-to-end flow
⏳ Verify identity consistency in generated videos

## Next Steps

1. **Test segment 1 generation** - Verify I2V endpoint works with Story Mode
2. **Test segment 2 generation** - Verify Chain API is called with correct parameters
3. **Verify identity consistency** - Check that character/subject remains consistent
4. **Test full chain generation** - Test the "生成长视频" button with multiple segments
5. **Document any remaining issues** - If identity consistency still doesn't work, investigate PainterLongVideo node parameters

## Technical Details

### Story Mode Parameters
```json
{
  "story_mode": true,
  "motion_frames": 5,        // Number of frames for motion analysis
  "boundary": 0.9,           // Identity consistency threshold
  "clip_preset": "nsfw",     // CLIP model preset
  "auto_continue": false,    // Don't auto-generate prompts
  "transition": "none"       // No transition effects
}
```

### Chain API Request Structure (Segment 2+)
```
POST /api/v1/generate/chain
FormData:
  - image: Blob (previous segment's last frame)
  - initial_reference_image: Blob (first frame from segment 1)
  - params: JSON (segments, story_mode params, etc.)
```

### Backend Workflow
1. Task completes → `task_manager.py` line 346
2. Extract last frame → `ffmpeg_utils.py` line 10
3. Save to uploads/ → `storage.py`
4. Store URL in Redis → `task_manager.py` line 365
5. Frontend polls task → receives last_frame_url
6. Frontend stores in window global → `window.seg_1_last_frame_url`
7. Segment 2 generation → uses stored last_frame_url

## Commits
- `5b09c38` - Fix: Add last_frame_url to list_tasks() response
- `b6e4de0` - Add debug guide for Story Mode segment 2 console logging
- `3e9883c` - Add verification guide for Story Mode segment 2 fix
