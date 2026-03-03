# Story Mode Segment 2 Fix - Status Report

**Date**: 2026-03-03
**Status**: ✅ FIX COMPLETED - READY FOR TESTING

## Summary

Fixed the issue where Story Mode single segment generation (segment 2+) couldn't access the previous segment's last frame for identity consistency.

## Root Cause

The `list_tasks()` method in `api/services/task_manager.py` was missing the `last_frame_url` field in its response, causing the frontend to receive `null` even though:
- Backend was extracting last frames correctly (283 PNG files verified)
- Redis was storing `last_frame_url` correctly (verified with redis-cli)
- The `get_task()` method included the field

## Fix Applied

**File**: `api/services/task_manager.py`
**Line**: 164
**Change**: Added `"last_frame_url": data.get("last_frame_url") or None,`

```diff
                     tasks.append({
                         "task_id": task_id,
                         "status": data.get("status", "unknown"),
                         "mode": data.get("mode", ""),
                         "model": data.get("model", ""),
                         "progress": float(data.get("progress", 0)),
                         "video_url": data.get("video_url") or None,
+                        "last_frame_url": data.get("last_frame_url") or None,
                         "error": data.get("error") or None,
                         "params": params,
                         "created_at": created_at or None,
                         "completed_at": completed_at,
                     })
```

## Verification Completed

✅ **Backend**: Last frame extraction working (283 PNG files in uploads/)
✅ **Redis**: Storing last_frame_url correctly
✅ **API**: Now returns last_frame_url in `/api/v1/tasks` endpoint
✅ **Service**: Restarted and running on http://localhost:8000
✅ **Frontend**: Console logging already in place (7 log statements)
✅ **Frontend**: Logic already correct (Chain API for segment 2+)

### API Response Verification
```bash
$ curl -s -H "X-API-Key: wan22-default-key-change-me" http://localhost:8000/api/v1/tasks | jq '.[0].last_frame_url'
"/api/v1/results/3241c2566d3147789b93dbbdc18c5576.png"
```

## Services Running

```
1803305.wan22_api      (Detached) - Port 8000
1802354.comfyui_a14b   (Detached) - Port 8188
```

## Next Steps - MANUAL TESTING REQUIRED

### Test Procedure

1. **Open Browser**
   ```
   http://localhost:8000
   ```

2. **Open DevTools Console** (F12)

3. **Navigate to "长视频生成" tab**

4. **Enable Story Mode**
   - Check "Story 模式" checkbox
   - Verify Story Mode fields appear (Motion Frames, Boundary, CLIP 预设)

5. **Upload First Frame Image**
   - Click upload area
   - Select an image with a clear subject/character

6. **Add Segment 1**
   - Click "添加分段" if needed
   - Enter a prompt describing the scene
   - Set duration (e.g., 5.0 seconds)

7. **Generate Segment 1**
   - Click "单独生成" button for segment 1
   - Watch console for expected output:
     ```
     [generateSingleSegment] segId=1, currentIndex=0, totalSegments=1, isStoryMode=true
     [generateSingleSegment] First segment, chainSelectedFile=exists
     ```
   - Wait for completion
   - Verify console shows:
     ```
     [pollSegmentTask] Segment 1 completed, video_url=...
     [pollSegmentTask] Segment 1 last_frame_url stored: /api/v1/results/...
     ```

8. **Add Segment 2**
   - Click "添加分段"
   - Enter a prompt for the continuation
   - Set duration

9. **Generate Segment 2**
   - Click "单独生成" button for segment 2
   - Watch console for expected output:
     ```
     [generateSingleSegment] segId=2, currentIndex=1, totalSegments=2, isStoryMode=true
     [generateSingleSegment] Story Mode segment 2+: prevSegId=1, prevLastFrameUrl=/api/v1/results/...
     [generateSingleSegment] All window seg variables: seg_1_video_url, seg_1_last_frame_url, seg_1_task_id
     [generateSingleSegment] initialRefImage=found, cachedInitialRef=exists
     [generateSingleSegment] Using Chain API for Story Mode segment 2+
     ```

10. **Verify Identity Consistency**
    - Compare segment 1 and segment 2 videos
    - Check that the subject/character remains consistent
    - Verify smooth continuation between segments

### Expected Behavior

**Segment 1**:
- Uses `/api/v1/generate/i2v` endpoint
- Includes uploaded image
- Includes Story Mode parameters
- Caches initial reference image to localStorage

**Segment 2**:
- Uses `/api/v1/generate/chain` endpoint (NOT i2v)
- Includes previous segment's last frame as `image`
- Includes initial reference image as `initial_reference_image`
- Includes Story Mode parameters (motion_frames=5, boundary=0.9, clip_preset=nsfw)

### Troubleshooting

If segment 2 fails or shows wrong console output:

1. **Check console for errors**
   - Look for red error messages
   - Check if last_frame_url is null

2. **Verify window variables**
   - In console, type: `Object.keys(window).filter(k => k.startsWith('seg_'))`
   - Should show: `seg_1_video_url`, `seg_1_last_frame_url`, `seg_1_task_id`

3. **Check localStorage**
   - In console, type: `localStorage.getItem('chain_initial_ref_image')`
   - Should return a data URL (base64 image)

4. **Check API logs**
   ```bash
   screen -r wan22_api
   # Ctrl+A then D to detach
   ```

5. **Check ComfyUI logs**
   ```bash
   screen -r comfyui_a14b
   # Look for PainterLongVideo node
   # Ctrl+A then D to detach
   ```

## Documentation

- **STORY_MODE_FIX_SUMMARY.md** - Complete technical summary
- **STORY_MODE_SEGMENT2_FIX_VERIFICATION.md** - Detailed verification steps
- **STORY_MODE_SEGMENT2_DEBUG.md** - Console logging debug guide

## Commits

1. `5b09c38` - Fix: Add last_frame_url to list_tasks() response
2. `b6e4de0` - Add debug guide for Story Mode segment 2 console logging
3. `3e9883c` - Add verification guide for Story Mode segment 2 fix
4. `a3c1719` - Add comprehensive summary of Story Mode segment 2 fix

## Success Criteria

✅ API returns last_frame_url
✅ Service restarted and running
✅ Frontend logic correct
✅ Console logging in place
⏳ Manual test: Segment 1 generation
⏳ Manual test: Segment 2 generation
⏳ Manual test: Identity consistency verification

---

**Ready for manual testing. Please follow the test procedure above and report any issues.**
