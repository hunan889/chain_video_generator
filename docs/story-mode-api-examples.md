# Story Mode API Examples

## Overview

This document provides practical API examples for Story mode single segment generation.

## Prerequisites

- API key configured in `config/api_keys.yaml`
- ComfyUI instance running
- First frame image available

## Example 1: Generate Segment 1 (First Segment)

### Request

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key-here" \
  -F "image=@/path/to/first_frame.png" \
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
  }'
```

### Response

```json
{
  "chain_id": "abc123def456",
  "total_segments": 1,
  "status": "queued"
}
```

### Check Status

```bash
curl -X GET "http://localhost:8000/api/v1/chains/abc123def456" \
  -H "X-API-Key: your-api-key-here"
```

### Response

```json
{
  "chain_id": "abc123def456",
  "status": "completed",
  "total_segments": 1,
  "completed_segments": 1,
  "current_segment": 0,
  "segment_task_ids": ["task_xyz789"],
  "final_video_url": "http://localhost:8000/videos/segment1.mp4"
}
```

## Example 2: Generate Segment 2 (Continuation with Identity Consistency)

### Step 1: Get Last Frame from Segment 1

```bash
curl -X GET "http://localhost:8000/api/v1/tasks/task_xyz789" \
  -H "X-API-Key: your-api-key-here"
```

### Response

```json
{
  "task_id": "task_xyz789",
  "status": "completed",
  "video_url": "http://localhost:8000/videos/segment1.mp4",
  "last_frame_url": "http://localhost:8000/videos/segment1_last_frame.png"
}
```

### Step 2: Generate Segment 2 with Story Mode

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key-here" \
  -F "image=@segment1_last_frame.png" \
  -F "initial_reference_image=@first_frame.png" \
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
  }'
```

### Response

```json
{
  "chain_id": "def456ghi789",
  "total_segments": 1,
  "status": "queued"
}
```

## Example 3: Generate Multiple Segments in One Request

```bash
curl -X POST "http://localhost:8000/api/v1/generate/chain" \
  -H "X-API-Key: your-api-key-here" \
  -F "image=@/path/to/first_frame.png" \
  -F 'params={
    "segments": [
      {
        "prompt": "A young woman with red hair standing in a forest",
        "duration": 5.0
      },
      {
        "prompt": "The woman walking through the forest",
        "duration": 5.0
      },
      {
        "prompt": "The woman discovering a hidden cave",
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
    "auto_continue": true,
    "transition": "none"
  }'
```

## Example 4: Python Client

```python
import requests
from pathlib import Path

API_BASE = "http://localhost:8000/api/v1"
API_KEY = "your-api-key-here"

def generate_segment_1(first_frame_path: Path, prompt: str):
    """Generate first segment with Story mode."""
    with open(first_frame_path, "rb") as f:
        files = {"image": f}
        data = {
            "params": {
                "segments": [{"prompt": prompt, "duration": 5.0}],
                "model": "a14b",
                "width": 832,
                "height": 480,
                "fps": 16,
                "steps": 20,
                "cfg": 1.0,
                "shift": 8.0,
                "story_mode": True,
                "motion_frames": 5,
                "boundary": 0.9,
                "clip_preset": "nsfw"
            }
        }

        response = requests.post(
            f"{API_BASE}/generate/chain",
            headers={"X-API-Key": API_KEY},
            files=files,
            data={"params": json.dumps(data["params"])}
        )
        return response.json()

def get_task_status(task_id: str):
    """Get task status and last frame URL."""
    response = requests.get(
        f"{API_BASE}/tasks/{task_id}",
        headers={"X-API-Key": API_KEY}
    )
    return response.json()

def generate_segment_continuation(
    previous_frame_path: Path,
    initial_reference_path: Path,
    prompt: str
):
    """Generate continuation segment with identity consistency."""
    with open(previous_frame_path, "rb") as prev_f, \
         open(initial_reference_path, "rb") as ref_f:

        files = {
            "image": prev_f,
            "initial_reference_image": ref_f
        }

        data = {
            "params": {
                "segments": [{"prompt": prompt, "duration": 5.0}],
                "model": "a14b",
                "width": 832,
                "height": 480,
                "fps": 16,
                "steps": 20,
                "cfg": 1.0,
                "shift": 8.0,
                "story_mode": True,
                "motion_frames": 5,
                "boundary": 0.9,
                "clip_preset": "nsfw",
                "auto_continue": False,
                "transition": "none"
            }
        }

        response = requests.post(
            f"{API_BASE}/generate/chain",
            headers={"X-API-Key": API_KEY},
            files=files,
            data={"params": json.dumps(data["params"])}
        )
        return response.json()

# Usage example
if __name__ == "__main__":
    import json
    import time

    # Generate segment 1
    print("Generating segment 1...")
    result1 = generate_segment_1(
        Path("first_frame.png"),
        "A young woman with red hair standing in a forest"
    )
    chain_id_1 = result1["chain_id"]
    print(f"Chain ID: {chain_id_1}")

    # Wait for completion
    while True:
        status = requests.get(
            f"{API_BASE}/chains/{chain_id_1}",
            headers={"X-API-Key": API_KEY}
        ).json()

        if status["status"] == "completed":
            task_id_1 = status["segment_task_ids"][0]
            break
        elif status["status"] == "failed":
            print(f"Failed: {status.get('error')}")
            exit(1)

        time.sleep(5)

    # Get last frame URL
    task_data = get_task_status(task_id_1)
    last_frame_url = task_data["last_frame_url"]
    print(f"Segment 1 completed. Last frame: {last_frame_url}")

    # Download last frame
    last_frame_data = requests.get(last_frame_url).content
    Path("segment1_last_frame.png").write_bytes(last_frame_data)

    # Generate segment 2 with identity consistency
    print("Generating segment 2 with identity consistency...")
    result2 = generate_segment_continuation(
        Path("segment1_last_frame.png"),
        Path("first_frame.png"),
        "The woman walking through the forest"
    )
    chain_id_2 = result2["chain_id"]
    print(f"Chain ID: {chain_id_2}")

    # Wait for completion
    while True:
        status = requests.get(
            f"{API_BASE}/chains/{chain_id_2}",
            headers={"X-API-Key": API_KEY}
        ).json()

        if status["status"] == "completed":
            print(f"Segment 2 completed: {status['final_video_url']}")
            break
        elif status["status"] == "failed":
            print(f"Failed: {status.get('error')}")
            exit(1)

        time.sleep(5)
```

## Example 5: JavaScript/Fetch (Browser)

```javascript
// Generate segment 1
async function generateSegment1(imageFile, prompt) {
  const formData = new FormData();
  formData.append('image', imageFile);
  formData.append('params', JSON.stringify({
    segments: [{ prompt, duration: 5.0 }],
    model: 'a14b',
    width: 832,
    height: 480,
    fps: 16,
    steps: 20,
    cfg: 1.0,
    shift: 8.0,
    story_mode: true,
    motion_frames: 5,
    boundary: 0.9,
    clip_preset: 'nsfw'
  }));

  const response = await fetch('/api/v1/generate/chain', {
    method: 'POST',
    headers: { 'X-API-Key': 'your-api-key-here' },
    body: formData
  });

  return await response.json();
}

// Generate continuation segment
async function generateContinuation(prevFrameBlob, initialRefBlob, prompt) {
  const formData = new FormData();
  formData.append('image', prevFrameBlob, 'previous_frame.png');
  formData.append('initial_reference_image', initialRefBlob, 'initial_ref.png');
  formData.append('params', JSON.stringify({
    segments: [{ prompt, duration: 5.0 }],
    model: 'a14b',
    width: 832,
    height: 480,
    fps: 16,
    steps: 20,
    cfg: 1.0,
    shift: 8.0,
    story_mode: true,
    motion_frames: 5,
    boundary: 0.9,
    clip_preset: 'nsfw',
    auto_continue: false,
    transition: 'none'
  }));

  const response = await fetch('/api/v1/generate/chain', {
    method: 'POST',
    headers: { 'X-API-Key': 'your-api-key-here' },
    body: formData
  });

  return await response.json();
}

// Usage
const imageInput = document.getElementById('imageInput');
const imageFile = imageInput.files[0];

// Generate segment 1
const result1 = await generateSegment1(
  imageFile,
  'A young woman with red hair standing in a forest'
);

console.log('Chain ID:', result1.chain_id);

// Poll for completion
const checkStatus = async (chainId) => {
  const response = await fetch(`/api/v1/chains/${chainId}`, {
    headers: { 'X-API-Key': 'your-api-key-here' }
  });
  return await response.json();
};

// Wait for segment 1
let status1;
while (true) {
  status1 = await checkStatus(result1.chain_id);
  if (status1.status === 'completed') break;
  await new Promise(r => setTimeout(r, 5000));
}

// Get last frame
const taskId1 = status1.segment_task_ids[0];
const taskResponse = await fetch(`/api/v1/tasks/${taskId1}`, {
  headers: { 'X-API-Key': 'your-api-key-here' }
});
const taskData = await taskResponse.json();
const lastFrameUrl = taskData.last_frame_url;

// Fetch last frame as blob
const lastFrameBlob = await fetch(lastFrameUrl).then(r => r.blob());

// Generate segment 2 with identity consistency
const result2 = await generateContinuation(
  lastFrameBlob,
  imageFile,
  'The woman walking through the forest'
);

console.log('Segment 2 Chain ID:', result2.chain_id);
```

## Parameter Reference

### Required Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `segments` | array | Array of segment configurations |
| `model` | string | Model to use ("a14b" or "5b") |
| `width` | int | Video width (must be divisible by 16) |
| `height` | int | Video height (must be divisible by 16) |
| `fps` | int | Frames per second (8-24) |

### Story Mode Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `story_mode` | bool | false | Enable Story mode identity consistency |
| `motion_frames` | int | 5 | Number of motion reference frames |
| `boundary` | float | 0.9 | Identity boundary threshold (0.0-1.0) |
| `clip_preset` | string | "nsfw" | CLIP model preset |
| `auto_continue` | bool | true | Auto-generate continuation prompts with VLM |
| `transition` | string | "none" | Transition effect between segments |

### Generation Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `steps` | int | 20 | Sampling steps |
| `cfg` | float | 1.0 | Classifier-free guidance scale |
| `shift` | float | 8.0 | Timestep shift |
| `seed` | int | -1 | Random seed (-1 for random) |
| `scheduler` | string | "unipc" | Sampler scheduler |
| `negative_prompt` | string | "" | Negative prompt |

## Error Responses

### 400 Bad Request

```json
{
  "detail": "Invalid params JSON: ..."
}
```

### 404 Not Found

```json
{
  "detail": "Chain not found"
}
```

### 503 Service Unavailable

```json
{
  "detail": "ComfyUI a14b instance is not available"
}
```

## Rate Limiting

No rate limiting is currently implemented. Consider implementing rate limiting in production environments.

## Best Practices

1. **Always save the initial reference image** - Required for all continuation segments
2. **Download last frame URLs** - Store them for generating next segments
3. **Poll status endpoints** - Don't assume immediate completion
4. **Handle errors gracefully** - Check for failed status and error messages
5. **Use appropriate timeouts** - Video generation can take several minutes
6. **Validate image formats** - PNG/JPG recommended, max 10MB
7. **Cache chain IDs** - Store them for later status checks

---

**Implementation Date**: 2026-03-03
**API Version**: v1
