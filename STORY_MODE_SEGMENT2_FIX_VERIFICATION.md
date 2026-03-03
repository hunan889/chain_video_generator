# Story Mode Segment 2 Fix Verification

## Issue Fixed
The `list_tasks()` method in `api/services/task_manager.py` was missing the `last_frame_url` field, causing the frontend to receive `null` even though Redis had the data stored correctly.

## Fix Applied
Added `"last_frame_url": data.get("last_frame_url") or None,` to line 164 in `task_manager.py`.

## Verification Steps

### 1. API Verification (✓ COMPLETED)
```bash
curl -s -H "X-API-Key: wan22-default-key-change-me" http://localhost:8000/api/v1/tasks | jq '[.[] | {task_id: .task_id, status: .status, last_frame_url: .last_frame_url}] | .[0:3]'
```

**Result**: API now returns `last_frame_url` correctly:
```json
{
  "task_id": "309561c52dcb406388669bceddc017a1",
  "status": "completed",
  "last_frame_url": "/api/v1/results/3241c2566d3147789b93dbbdc18c5576.png"
}
```

### 2. Frontend Console Logging Test (NEXT STEP)

Open browser DevTools Console and test Story Mode single segment generation:

#### Test Scenario 1: Generate Segment 1 (First segment with uploaded image)
1. Open http://localhost:8000 in browser
2. Open DevTools Console (F12)
3. Go to "长视频生成" tab
4. Enable "Story 模式" checkbox
5. Upload a first frame image
6. Add one segment with a prompt
7. Click "单独生成" for segment 1
8. Watch console for these logs:

**Expected Console Output**:
```
[generateSingleSegment] segId=1, currentIndex=0, totalSegments=1, isStoryMode=true
[generateSingleSegment] First segment, chainSelectedFile=exists
[generateSingleSegment] Using I2V with uploaded image
```

**Expected Behavior**:
- Should use `/api/v1/generate/i2v` endpoint
- Should include Story Mode parameters (story_mode, motion_frames, boundary, clip_preset)
- Should cache initial reference image to localStorage

#### Test Scenario 2: Generate Segment 2 (Story Mode continuation)
1. After segment 1 completes, check console for:
```
[pollSegmentTask] Segment 1 completed, video_url=...
[pollSegmentTask] Segment 1 last_frame_url stored: /api/v1/results/...
[pollSegmentTask] Segment 1 data saved to storage
```

2. Add segment 2 with a prompt
3. Click "单独生成" for segment 2
4. Watch console for these logs:

**Expected Console Output**:
```
[generateSingleSegment] segId=2, currentIndex=1, totalSegments=2, isStoryMode=true
[generateSingleSegment] Story Mode segment 2+: prevSegId=1, prevLastFrameUrl=/api/v1/results/...
[generateSingleSegment] All window seg variables: seg_1_video_url, seg_1_last_frame_url, seg_1_task_id
[generateSingleSegment] initialRefImage=found, cachedInitialRef=exists
[generateSingleSegment] Using Chain API for Story Mode segment 2+
```

**Expected Behavior**:
- Should use `/api/v1/generate/chain` endpoint (NOT i2v)
- Should include previous segment's last frame as `image`
- Should include initial reference image as `initial_reference_image`
- Should include Story Mode parameters

### 3. Verify Story Mode Parameters

When segment 2 is generated, the Chain API request should include:
```json
{
  "segments": [{"prompt": "...", "duration": 5.0}],
  "story_mode": true,
  "motion_frames": 5,
  "boundary": 0.9,
  "clip_preset": "nsfw",
  "auto_continue": false,
  "transition": "none"
}
```

### 4. Backend Verification

Check ComfyUI A14B logs to verify PainterLongVideo node is used:
```bash
screen -r comfyui_a14b
# Look for: "PainterLongVideo" node in workflow
# Ctrl+A then D to detach
```

## Key Code Locations

### Frontend (api/static/index.html)
- **Line 2421-2557**: `generateSingleSegment()` - Main logic with console logging
- **Line 2428-2510**: Story Mode segment 2+ logic using Chain API
- **Line 2670-2681**: `pollSegmentTask()` - Stores last_frame_url when segment completes

### Backend (api/services/task_manager.py)
- **Line 164**: Fixed - now includes `last_frame_url` in list_tasks response
- **Line 99**: get_task() already had last_frame_url
- **Line 346-369**: Task completion handler extracts last frame

## Success Criteria

✅ API returns last_frame_url in /api/v1/tasks endpoint
✅ Frontend console shows correct logic path for segment 1 (I2V)
✅ Frontend console shows correct logic path for segment 2 (Chain API)
✅ Segment 2 uses previous segment's last frame
✅ Segment 2 includes initial reference image for identity consistency
✅ ComfyUI workflow uses PainterLongVideo node for Story Mode

## Next Steps After Verification

If console logging shows segment 2 is using Chain API correctly:
1. Test actual video generation for segment 1
2. Wait for completion and verify last_frame_url is stored
3. Test actual video generation for segment 2
4. Verify identity consistency between segments
5. Check that character/subject remains consistent across segments

## Troubleshooting

If segment 2 still doesn't work:
1. Check console for error messages
2. Verify `window.seg_1_last_frame_url` is set after segment 1 completes
3. Verify `localStorage.getItem('chain_initial_ref_image')` exists
4. Check API logs: `screen -r wan22_api`
5. Check ComfyUI logs: `screen -r comfyui_a14b`
