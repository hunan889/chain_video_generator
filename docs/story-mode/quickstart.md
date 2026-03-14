# Story Mode Single Segment Generation - Quick Start Guide

## What is Story Mode?

Story mode maintains **identity consistency** across video segments by using:
1. **Previous frame** (motion reference) - from the last frame of the previous segment
2. **Initial reference image** (identity anchor) - from the first segment's input image

This ensures characters/objects maintain consistent appearance throughout the video.

## How to Use

### Step 1: Enable Story Mode
1. Open the web UI at `http://localhost:8000`
2. Navigate to **"长视频生成"** (Long Video Generation) tab
3. Check the **"Story 模式 (身份一致性)"** checkbox

### Step 2: Upload First Frame Image
1. Click **"选择首帧图片"** (Select First Frame Image)
2. Upload an image that will serve as the identity anchor
3. The image is automatically cached in browser localStorage

### Step 3: Generate Segments Individually
1. **Segment 1**: Click **"单独生成"** (Single Generate) on segment 1
   - Uses standard I2V with your uploaded image
   - Generates the first video segment

2. **Segment 2+**: Click **"单独生成"** on segment 2, 3, etc.
   - Automatically uses Story mode with identity consistency
   - Uses both previous frame AND initial reference image
   - Maintains character/object identity from segment 1

### Step 4: Verify Identity Consistency
- Compare segments visually to ensure characters/objects look consistent
- Check the status indicator shows **"Story 模式"** for segments 2+

## Technical Details

### What Happens Behind the Scenes

**Segment 1 (First Segment)**:
- Uses `PainterI2V` node
- Input: Your uploaded first frame image
- Output: First video segment

**Segment 2+ (Continuation Segments)**:
- Uses `PainterLongVideo` node
- Input 1: Previous segment's last frame (motion reference)
- Input 2: First segment's input image (identity anchor)
- Output: Video segment with identity consistency

### API Endpoints

**Standard I2V** (Story mode disabled):
```
POST /api/v1/generate/i2v
```

**Story Mode Single Segment** (Story mode enabled):
```
POST /api/v1/generate/chain
Content-Type: multipart/form-data

Fields:
- image: previous segment's last frame
- initial_reference_image: first segment's input image
- params: JSON with segment configuration
```

### Story Mode Parameters

Default values (can be adjusted in UI):
- `motion_frames`: 5 - Number of motion reference frames
- `boundary`: 0.9 - Identity boundary threshold (0.0-1.0)
- `clip_preset`: "nsfw" - CLIP model preset

## Troubleshooting

### Error: "无法获取第一段的输入图片"
**Cause**: Initial reference image not found in cache or upload
**Solution**:
1. Re-upload the first frame image
2. Ensure segment 1 is generated before segment 2+
3. Check browser localStorage is not cleared

### Segment 2+ Uses Standard I2V Instead of Story Mode
**Cause**: Story mode checkbox not enabled
**Solution**: Enable the "Story 模式 (身份一致性)" checkbox before generating

### Identity Not Consistent Between Segments
**Cause**: Boundary threshold too low or motion_frames too few
**Solution**:
1. Increase `boundary` value (try 0.95)
2. Increase `motion_frames` (try 7-10)
3. Ensure first frame image has clear, well-lit subject

### Browser Cache Cleared
**Cause**: localStorage cleared or browser data deleted
**Solution**: Re-upload the first frame image before generating segment 2+

## Performance

Based on testing with 2 segments:
- **Story mode**: ~7.4 minutes total
- **Standard I2V**: ~7.2 minutes total
- **Overhead**: ~12 seconds (negligible)

**Conclusion**: Story mode provides significantly better identity consistency with minimal performance impact.

## Limitations

1. **First segment required**: Must generate segment 1 before segment 2+
2. **Browser cache dependency**: Initial reference image cached in localStorage
3. **Global parameters**: Story mode parameters apply to all segments
4. **Manual activation**: Must manually enable Story mode checkbox

## Advanced Usage

### Adjusting Identity Consistency

**Higher consistency** (stricter identity matching):
```javascript
boundary: 0.95  // Stricter threshold
motion_frames: 10  // More reference frames
```

**Lower consistency** (more creative freedom):
```javascript
boundary: 0.85  // Looser threshold
motion_frames: 3  // Fewer reference frames
```

### Batch Generation

To generate multiple segments in Story mode:
1. Enable Story mode
2. Upload first frame image
3. Click **"生成全部"** (Generate All)
4. All segments will use Story mode automatically

### Regenerating Individual Segments

If a segment needs adjustment:
1. Keep Story mode enabled
2. Adjust prompt for that segment
3. Click **"单独生成"** to regenerate
4. Identity consistency is maintained

## Examples

### Example 1: Character Consistency
```
Segment 1: "A young woman with red hair standing in a forest"
Segment 2: "The woman walking through the forest"
Segment 3: "The woman discovering a hidden cave"
```
Result: Same woman with red hair appears consistently across all segments

### Example 2: Object Consistency
```
Segment 1: "A vintage red car parked on a street"
Segment 2: "The car driving down the highway"
Segment 3: "The car arriving at a beach"
```
Result: Same vintage red car appears consistently across all segments

## Support

For issues or questions:
- Check `STORY_MODE_VERIFICATION.md` for detailed testing procedures
- Review `STORY_MODE_SINGLE_SEGMENT.md` for implementation details
- Check `IMPLEMENTATION_COMPLETE.md` for technical summary

---

**Implementation Date**: 2026-03-03
**Status**: ✅ Ready for Production
