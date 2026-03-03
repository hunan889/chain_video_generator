# Story Mode Single Segment Generation - Verification Checklist

## Pre-requisites
- [ ] ComfyUI services are running (A14B instance)
- [ ] Redis is running
- [ ] FastAPI service is running
- [ ] First frame image is uploaded in the chain panel

## Test Case 1: Story Mode Single Segment (Segment 2)

### Steps:
1. [ ] Open the web UI at http://localhost:8000
2. [ ] Navigate to "长视频生成" (Chain) tab
3. [ ] Enable "Story 模式 (身份一致性)" checkbox
4. [ ] Upload a first frame image (e.g., portrait photo)
5. [ ] Add segment 1 with prompt (e.g., "a person smiling")
6. [ ] Click "单独生成" on segment 1
7. [ ] Wait for segment 1 to complete
8. [ ] Add segment 2 with prompt (e.g., "the person waving")
9. [ ] Click "单独生成" on segment 2

### Expected Results:
- [ ] Segment 2 status shows "已提交 Story 模式任务"
- [ ] Chain ID is displayed (8 characters)
- [ ] Progress bar shows generation progress
- [ ] Video completes successfully
- [ ] Character/object identity is consistent between segment 1 and 2
- [ ] Motion is smooth and continuous

### Backend Verification:
```bash
# Check logs for Story mode workflow building
tail -f logs/app.log | grep "story"

# Expected log entries:
# - "Chain {chain_id}: story initial_ref_filename={filename}"
# - "Building story segment workflow"
# - "Using PainterLongVideo node"
```

## Test Case 2: Story Mode Single Segment (Segment 3+)

### Steps:
1. [ ] Continue from Test Case 1
2. [ ] Add segment 3 with prompt
3. [ ] Click "单独生成" on segment 3

### Expected Results:
- [ ] Same as Test Case 1
- [ ] Identity consistency maintained across all 3 segments

## Test Case 3: Standard Mode (Non-Story)

### Steps:
1. [ ] Disable "Story 模式" checkbox
2. [ ] Add segment 1 with prompt
3. [ ] Click "单独生成" on segment 1
4. [ ] Wait for completion
5. [ ] Add segment 2 with prompt
6. [ ] Click "单独生成" on segment 2

### Expected Results:
- [ ] Segment 2 uses standard I2V API (not chain API)
- [ ] Status shows "已提交任务" (not "Story 模式")
- [ ] Video generates successfully
- [ ] Identity may not be consistent (expected behavior)

## Test Case 4: Error Handling

### Test 4a: Missing Initial Reference Image
1. [ ] Enable Story mode
2. [ ] Do NOT upload first frame image
3. [ ] Add segment 2 (skip segment 1)
4. [ ] Click "单独生成" on segment 2

**Expected**: Error message "错误：无法获取第一段的输入图片（身份参考），请确保已上传首帧图片"

### Test 4b: Missing Previous Segment
1. [ ] Enable Story mode
2. [ ] Upload first frame image
3. [ ] Add segment 2 (skip segment 1)
4. [ ] Click "单独生成" on segment 2

**Expected**: Error message "错误：上一段（分段 1）尚未生成或最后一帧未提取，请先生成上一段"

## API Verification

### Check FormData Contents:
```bash
# Enable debug logging in extend.py
# Add after line 161:
logger.info(f"Chain API called with initial_ref_filename: {initial_ref_filename}")

# Restart service and check logs
tail -f logs/app.log | grep "initial_ref_filename"
```

### Check Redis Data:
```bash
# Connect to Redis
redis-cli

# List all chains
KEYS chain:*

# Check chain data
HGETALL chain:{chain_id}

# Expected fields:
# - status: "running" or "completed"
# - total_segments: "1"
# - params: JSON with story_mode=true
```

## Performance Comparison

### Story Mode vs Standard I2V:
- [ ] Generate 2 segments with Story mode (single generate)
- [ ] Generate 2 segments with Standard I2V (single generate)
- [ ] Compare:
  - Generation time (should be similar)
  - Identity consistency (Story mode should be better)
  - Motion smoothness (Story mode should be better)

## Known Issues / Limitations

1. **First Segment**: Must be generated before subsequent segments
2. **Image Cache**: If browser cache is cleared, localStorage may lose the initial reference image
3. **Story Mode Parameters**: Currently uses global parameters from chain panel (motion_frames, boundary, clip_preset)

## Troubleshooting

### Issue: "错误：无法获取第一段的输入图片"
**Solution**: 
- Ensure first frame image is uploaded
- Check browser localStorage: `localStorage.getItem('wan22_chain_image')`
- Re-upload the image if needed

### Issue: "错误：上一段尚未生成或最后一帧未提取"
**Solution**:
- Generate previous segment first
- Wait for previous segment to complete
- Check that `window['seg_{id}_last_frame_url']` is set in browser console

### Issue: Video generates but identity is not consistent
**Solution**:
- Verify Story mode checkbox is enabled
- Check that initial reference image is being passed (check network tab)
- Verify `initial_ref_filename` is set in backend logs
- Try adjusting `boundary` parameter (lower = stricter identity matching)

## Success Criteria

✅ All test cases pass
✅ No errors in backend logs
✅ Identity consistency is maintained across segments
✅ Performance is acceptable (similar to standard I2V)
✅ UI shows correct status messages
✅ Videos can be downloaded and played

## Date Tested
_____________

## Tested By
_____________

## Notes
_____________________________________________
_____________________________________________
_____________________________________________
