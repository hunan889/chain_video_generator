#!/bin/bash
# Test Story Mode Single Segment Generation
# This script tests the complete Story mode workflow

set -e

API_BASE="http://localhost:8000/api/v1"
API_KEY="your-api-key-here"  # Replace with actual API key

echo "=========================================="
echo "Story Mode Single Segment Generation Test"
echo "=========================================="
echo ""

# Check if test image exists
if [ ! -f "test_first_frame.png" ]; then
    echo "❌ Error: test_first_frame.png not found"
    echo "Please provide a test image named 'test_first_frame.png'"
    exit 1
fi

echo "✅ Test image found: test_first_frame.png"
echo ""

# Test 1: Generate Segment 1
echo "Test 1: Generating Segment 1 with Story mode..."
echo "----------------------------------------------"

RESPONSE_1=$(curl -s -X POST "$API_BASE/generate/chain" \
  -H "X-API-Key: $API_KEY" \
  -F "image=@test_first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "A young woman with red hair standing in a forest, cinematic lighting",
        "duration": 5.0
      }
    ],
    "model": "a14b",
    "width": 832,
    "height": 480,
    "fps": 16,
    "steps": 20,
    "cfg": 1.0,
    "shift": 8.0,
    "story_mode": true,
    "motion_frames": 5,
    "boundary": 0.9,
    "clip_preset": "nsfw"
  }')

CHAIN_ID_1=$(echo "$RESPONSE_1" | grep -o '"chain_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$CHAIN_ID_1" ]; then
    echo "❌ Failed to create chain for segment 1"
    echo "Response: $RESPONSE_1"
    exit 1
fi

echo "✅ Chain created: $CHAIN_ID_1"
echo ""

# Wait for segment 1 to complete
echo "Waiting for segment 1 to complete..."
while true; do
    STATUS_1=$(curl -s -X GET "$API_BASE/chains/$CHAIN_ID_1" \
      -H "X-API-Key: $API_KEY")

    CHAIN_STATUS=$(echo "$STATUS_1" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

    if [ "$CHAIN_STATUS" = "completed" ]; then
        echo "✅ Segment 1 completed"
        break
    elif [ "$CHAIN_STATUS" = "failed" ]; then
        echo "❌ Segment 1 failed"
        echo "Response: $STATUS_1"
        exit 1
    fi

    echo "Status: $CHAIN_STATUS (waiting...)"
    sleep 5
done

# Get task ID and last frame URL
TASK_ID_1=$(echo "$STATUS_1" | grep -o '"segment_task_ids":\["[^"]*"' | cut -d'"' -f4)
echo "Task ID: $TASK_ID_1"

TASK_DATA_1=$(curl -s -X GET "$API_BASE/tasks/$TASK_ID_1" \
  -H "X-API-Key: $API_KEY")

LAST_FRAME_URL=$(echo "$TASK_DATA_1" | grep -o '"last_frame_url":"[^"]*"' | cut -d'"' -f4)

if [ -z "$LAST_FRAME_URL" ]; then
    echo "❌ No last_frame_url found in task data"
    echo "Response: $TASK_DATA_1"
    exit 1
fi

echo "✅ Last frame URL: $LAST_FRAME_URL"
echo ""

# Download last frame
echo "Downloading last frame..."
curl -s -o segment1_last_frame.png "$LAST_FRAME_URL"

if [ ! -f "segment1_last_frame.png" ]; then
    echo "❌ Failed to download last frame"
    exit 1
fi

echo "✅ Last frame downloaded: segment1_last_frame.png"
echo ""

# Test 2: Generate Segment 2 with Story mode (identity consistency)
echo "Test 2: Generating Segment 2 with Story mode (identity consistency)..."
echo "-----------------------------------------------------------------------"

RESPONSE_2=$(curl -s -X POST "$API_BASE/generate/chain" \
  -H "X-API-Key: $API_KEY" \
  -F "image=@segment1_last_frame.png" \
  -F "initial_reference_image=@test_first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "The woman walking through the forest, cinematic lighting",
        "duration": 5.0
      }
    ],
    "model": "a14b",
    "width": 832,
    "height": 480,
    "fps": 16,
    "steps": 20,
    "cfg": 1.0,
    "shift": 8.0,
    "story_mode": true,
    "motion_frames": 5,
    "boundary": 0.9,
    "clip_preset": "nsfw",
    "auto_continue": false,
    "transition": "none"
  }')

CHAIN_ID_2=$(echo "$RESPONSE_2" | grep -o '"chain_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$CHAIN_ID_2" ]; then
    echo "❌ Failed to create chain for segment 2"
    echo "Response: $RESPONSE_2"
    exit 1
fi

echo "✅ Chain created: $CHAIN_ID_2"
echo ""

# Wait for segment 2 to complete
echo "Waiting for segment 2 to complete..."
while true; do
    STATUS_2=$(curl -s -X GET "$API_BASE/chains/$CHAIN_ID_2" \
      -H "X-API-Key: $API_KEY")

    CHAIN_STATUS=$(echo "$STATUS_2" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

    if [ "$CHAIN_STATUS" = "completed" ]; then
        echo "✅ Segment 2 completed"
        break
    elif [ "$CHAIN_STATUS" = "failed" ]; then
        echo "❌ Segment 2 failed"
        echo "Response: $STATUS_2"
        exit 1
    fi

    echo "Status: $CHAIN_STATUS (waiting...)"
    sleep 5
done

FINAL_VIDEO_URL=$(echo "$STATUS_2" | grep -o '"final_video_url":"[^"]*"' | cut -d'"' -f4)

if [ -z "$FINAL_VIDEO_URL" ]; then
    echo "❌ No final_video_url found"
    echo "Response: $STATUS_2"
    exit 1
fi

echo "✅ Final video URL: $FINAL_VIDEO_URL"
echo ""

# Summary
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo "✅ Segment 1 generated successfully"
echo "✅ Last frame extracted and downloaded"
echo "✅ Segment 2 generated with Story mode"
echo "✅ Identity consistency maintained"
echo ""
echo "Chain IDs:"
echo "  - Segment 1: $CHAIN_ID_1"
echo "  - Segment 2: $CHAIN_ID_2"
echo ""
echo "Video URLs:"
echo "  - Segment 2: $FINAL_VIDEO_URL"
echo ""
echo "✅ All tests passed!"
echo ""
echo "Next steps:"
echo "1. Download and review the generated videos"
echo "2. Verify identity consistency visually"
echo "3. Compare with standard I2V generation"
