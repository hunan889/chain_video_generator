# Story Mode Segment 2 Debug Summary

## Problem Statement
When using Story Mode and clicking "单独生成" for segment 2, it should:
1. Detect it's segment 2 (currentIndex > 0)
2. Use segment 1's last frame as motion reference
3. Use initial reference image as identity anchor
4. Call Chain API with both images to create 1-segment chain using PainterLongVideo node

Currently, segment 2 may not be correctly referencing segment 1's video.

## Changes Made

### 1. Added Console Logging in `api/static/index.html`

#### In `generateSingleSegment()` function (lines 2421-2556):
- Log segment ID, index, total segments, Story Mode status
- Log previous segment ID and last frame URL availability
- Log initial reference image availability
- Log which code path is executed (Story Mode seg2+, first seg, non-Story seg2+)
- Log all window seg_* variables to verify data storage

#### In `pollSegmentTask()` function (lines 2670-2678):
- Log when segment video completes
- Log when last_frame_url is stored in window variable
- Warn if last_frame_url is missing from API response
- Log when data is saved to localStorage

### 2. Verified Backend is Working
- ✅ API successfully extracts last frame from videos (ffmpeg_utils.py)
- ✅ API stores last_frame_url in Redis task data
- ✅ API returns last_frame_url in task status responses
- Example: Task `6fda81d4811f4a2ba62da549547942fe` has last_frame_url: `/api/v1/results/e49a77cd660e49f3909a41d9f1620e70.png`

## Testing Instructions

### Setup:
1. Open browser: http://localhost:8000
2. Open DevTools (F12) → Console tab
3. Go to "长视频生成" tab
4. Enable "Story 模式 (身份一致性)" checkbox
5. Upload a first frame image
6. Add 2 segments with prompts

### Test Sequence:

#### Step 1: Generate Segment 1
Click "单独生成" for Segment 1

**Expected Console Output:**
```
[generateSingleSegment] segId=1, currentIndex=0, totalSegments=2, isStoryMode=true
[generateSingleSegment] First segment, chainSelectedFile=exists
```

Wait for completion. When done:
```
[pollSegmentTask] Segment 1 completed, video_url=<url>
[pollSegmentTask] Segment 1 last_frame_url stored: <url>
[pollSegmentTask] Segment 1 data saved to storage
```

#### Step 2: Generate Segment 2
Click "单独生成" for Segment 2

**Expected Console Output:**
```
[generateSingleSegment] segId=2, currentIndex=1, totalSegments=2, isStoryMode=true
[generateSingleSegment] Story Mode segment 2+: prevSegId=1, prevLastFrameUrl=<url>
[generateSingleSegment] All window seg variables: [seg_1_video_url, seg_1_last_frame_url, ...]
[generateSingleSegment] initialRefImage=found, cachedInitialRef=exists
[generateSingleSegment] Using Chain API for Story Mode segment 2+
```

## Diagnostic Scenarios

### Scenario A: last_frame_url not returned by API
**Symptom:** `[pollSegmentTask] Segment 1 has NO last_frame_url in task response!`
**Cause:** API failed to extract last frame
**Action:** Check API logs for ffmpeg errors

### Scenario B: prevLastFrameUrl is null
**Symptom:** `prevLastFrameUrl=null` when generating segment 2
**Cause:** Window variable not set or page was refreshed
**Action:** 
- Check if `seg_1_last_frame_url` appears in window variables list
- Verify pollSegmentTask was running when segment 1 completed
- Check if localStorage has the data (should restore on page load)

### Scenario C: Not using Chain API
**Symptom:** Missing log: `[generateSingleSegment] Using Chain API for Story Mode segment 2+`
**Cause:** One of the conditions failed (currentIndex, isStoryMode, prevLastFrameUrl)
**Action:** Review all logged values to identify which condition is false

## Success Criteria

✅ Backend extracts and stores last_frame_url (VERIFIED)
✅ Frontend receives last_frame_url in task response
✅ Frontend stores last_frame_url in window variable
✅ Frontend saves data to localStorage
✅ Segment 2 detects currentIndex=1
✅ Segment 2 finds prevLastFrameUrl with valid URL
✅ Segment 2 uses Chain API with both images
✅ Segment 2 creates 1-segment chain using PainterLongVideo

## Next Steps

1. Run the test sequence above
2. Copy all console logs
3. Share the logs to identify where the flow breaks
4. Based on the logs, we can pinpoint the exact issue and fix it

## Files Modified

- `api/static/index.html` (commit 7a112ea)
  - Added 10+ console.log statements for debugging
  - Enhanced error detection and reporting
