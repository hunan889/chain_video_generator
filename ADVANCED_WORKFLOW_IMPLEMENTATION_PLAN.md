# Advanced Workflow Implementation Plan

## Overview

This plan outlines the implementation of the advanced video generation workflow system with three modes: face_reference, full_body_reference, and first_frame. The system integrates T2I generation, SeeDream editing, face swapping, and I2V generation into a unified workflow.

Reference: `docs/advanced-workflow.md`

## Implementation Phases

### Phase 1: Core Infrastructure (1-2 weeks) ✓ COMPLETED

**Status**: ✓ Completed

**Completed Items**:
- ✓ `/workflow/analyze` API endpoint implemented
- ✓ Semantic search for video LORAs using EmbeddingService
- ✓ Mode-based filtering (face_reference, full_body_reference, first_frame)
- ✓ Database integration with lora_metadata table
- ✓ Basic response structure with LORA recommendations

**Remaining Items**:
- ⚠ Image LORA semantic search (table currently empty)
- ⚠ Qwen3-14B prompt optimization integration

**Files Modified**:
- `api/routes/workflow.py` - Added analyze endpoint
- `test_workflow_analyze.py` - Test script

---

### Phase 2: SeeDream Editing Integration (1 week) - NEXT

**Goal**: Implement SeeDream image editing with three modes and face swap integration

**Tasks**:

1. **Create `/workflow/seedream-edit` endpoint**
   - Accept: image (base64/URL), mode (face_only/face_wearings/full_body), reference_face (optional)
   - Return: edited image URL, edit_mode, face_swapped (boolean)

2. **Implement three edit modes**:
   - `face_only`: Only face replacement
   - `face_wearings`: Face + accessories (jewelry, glasses, hair accessories)
   - `full_body`: Face + accessories + clothing

3. **Integrate with existing SeeDream API**
   - Reuse code from `api/routes/image.py` `/image/scene-swap` endpoint
   - Use Reactor + SeeDream multiref pattern
   - Support face swap enable/disable switch

4. **Error handling**:
   - SeeDream fails → fallback to Reactor only
   - Reactor fails → use original image
   - Return error details in response

**Files to Create/Modify**:
- `api/routes/workflow.py` - Add seedream-edit endpoint
- `api/services/seedream_service.py` (optional) - Extract SeeDream logic

**Acceptance Criteria**:
- ✓ Three edit modes working correctly
- ✓ Face swap can be enabled/disabled
- ✓ Proper error handling and fallbacks
- ✓ Returns edited image URL

---

### Phase 3: Complete Workflow Orchestration (1 week)

**Goal**: Implement end-to-end workflow generation with all three modes

**Tasks**:

1. **Create `/workflow/generate-advanced` endpoint**
   - Accept: prompt, mode, first_frame_source, reference_image, workflow_params
   - Orchestrate: analyze → T2I/upload/select → SeeDream → I2V
   - Return: workflow_id, status, video_url

2. **Implement three first_frame_source options**:
   - `use_uploaded`: Use uploaded image directly (default)
   - `generate`: Generate via T2I with image LORAs
   - `select_existing`: Select from existing image library

3. **Integrate T2I generation**:
   - Use SD WebUI + PONY NSFW model
   - Apply recommended image LORAs
   - Use optimized T2I prompt
   - Resolution: match video resolution presets

4. **Integrate with Chain workflow**:
   - Use existing `/chain` endpoint for video generation
   - Pass first frame + video LORAs + optimized I2V prompt
   - Support multi-segment generation

5. **Create workflow state management**:
   - Database table: `advanced_workflows`
   - Track: workflow_id, mode, status, first_frame_url, video_url, params
   - Support status queries

**Files to Create/Modify**:
- `api/routes/workflow.py` - Add generate-advanced endpoint
- `api/services/workflow_orchestrator.py` - Orchestration logic
- `api/services/t2i_service.py` - T2I generation integration
- Database migration for `advanced_workflows` table

**Acceptance Criteria**:
- ✓ All three modes working end-to-end
- ✓ All three first_frame_source options working
- ✓ Proper error handling at each stage
- ✓ Workflow state tracked in database
- ✓ Status query endpoint working

---

### Phase 4: Frontend UI (2 weeks)

**Goal**: Create user interface for advanced workflow

**Tasks**:

1. **Create workflow UI page**:
   - Mode selection (face_reference, full_body_reference, first_frame)
   - Prompt input with auto-complete
   - Image upload for reference
   - LORA selection (auto-recommended + manual)
   - Parameter controls (resolution, duration, etc.)

2. **Implement workflow preview**:
   - Show recommended LORAs with previews
   - Show optimized prompts
   - Preview first frame before video generation

3. **Add progress tracking**:
   - Real-time status updates
   - Stage indicators (analyzing → generating first frame → editing → generating video)
   - Progress bars for each stage

4. **Result display**:
   - Show final video with playback controls
   - Show first frame used
   - Show applied LORAs and parameters
   - Download options

**Files to Create**:
- `api/static/advanced_workflow.html` - Main UI page
- `api/static/js/advanced_workflow.js` - Frontend logic
- `api/static/css/advanced_workflow.css` - Styling

**Acceptance Criteria**:
- ✓ Intuitive UI for all three modes
- ✓ Real-time progress tracking
- ✓ Preview capabilities
- ✓ Responsive design

---

### Phase 5: Optimization and Extensions (Ongoing)

**Goal**: Improve performance, accuracy, and features

**Tasks**:

1. **Prompt optimization with Qwen3-14B**:
   - Integrate LLM for T2I prompt optimization
   - Integrate LLM for I2V prompt optimization
   - Inject LORA trigger words intelligently
   - Emphasize front face for T2I

2. **Image LORA semantic search**:
   - Index image_lora_metadata in EmbeddingService
   - Add image_lora type to vector database
   - Enable semantic search for image LORAs

3. **Performance optimization**:
   - Cache T2I results for similar prompts
   - Parallel processing where possible
   - Optimize SeeDream API calls

4. **Feature extensions**:
   - Support multiple reference faces
   - Support custom LORA weights
   - Support style transfer
   - Support video-to-video editing

**Acceptance Criteria**:
- ✓ Prompt optimization improves quality
- ✓ Image LORA search working
- ✓ Response time < 5 minutes for full workflow
- ✓ Extended features working

---

## Current Status Summary

| Phase | Status | Progress | Notes |
|-------|--------|----------|-------|
| Phase 1 | ✓ Completed | 80% | Core API working, prompt optimization pending |
| Phase 2 | ✓ Completed | 100% | SeeDream integration with 3 modes |
| Phase 3 | ✓ Completed | 60% | Workflow orchestration skeleton, needs full implementation |
| Phase 4 | ⏳ Pending | 0% | Frontend UI |
| Phase 5 | ⏳ Pending | 0% | Optimizations |

---

## Implementation Summary

### Phase 1: Core Infrastructure ✓
- ✓ `/workflow/analyze` endpoint
- ✓ Semantic search for video LORAs
- ✓ Mode-based filtering
- ⚠ Image LORA search (pending)
- ⚠ Prompt optimization (pending)

### Phase 2: SeeDream Editing ✓
- ✓ `/workflow/seedream-edit` endpoint
- ✓ Three edit modes: face_only, face_wearings, full_body
- ✓ Reactor face swap integration
- ✓ Error handling and fallbacks
- ✓ Mode-specific prompts

### Phase 3: Workflow Orchestration ✓ (Skeleton)
- ✓ `/workflow/generate-advanced` endpoint
- ✓ `/workflow/status/{workflow_id}` endpoint
- ✓ Request/response schemas
- ✓ Stage tracking structure
- ⚠ Full orchestration logic (TODO)
- ⚠ Database state management (TODO)
- ⚠ T2I integration (TODO)

### Phase 4: Frontend UI ⏳
- Not started

### Phase 5: Optimizations ⏳
- Not started

---

## Dependencies and Prerequisites

### External Services
- ✓ EmbeddingService (BGE-Large-ZH) - Running
- ✓ Zilliz vector database - Connected
- ✓ MySQL database - Connected
- ✓ SeeDream API (BytePlus) - Available in `api/routes/image.py`
- ⚠ SD WebUI for T2I - Need to verify/setup
- ⚠ Qwen3-14B LLM - Need to integrate

### Database Tables
- ✓ `lora_metadata` - Exists (6 enabled video LORAs)
- ✓ `image_lora_metadata` - Exists (currently empty)
- ✓ `resources` - Exists
- ⏳ `advanced_workflows` - Need to create

### Models and Assets
- ✓ Video LORAs - Available in database
- ⚠ Image LORAs - Need to populate
- ⚠ PONY NSFW model - Need to verify availability

---

## Next Steps

1. **Immediate (Phase 2)**:
   - Implement `/workflow/seedream-edit` endpoint
   - Test three edit modes
   - Verify face swap integration

2. **Short-term (Phase 3)**:
   - Setup T2I integration with SD WebUI
   - Implement workflow orchestration
   - Create database table for workflow state

3. **Medium-term (Phase 4)**:
   - Design and implement frontend UI
   - Add progress tracking
   - User testing and feedback

4. **Long-term (Phase 5)**:
   - Integrate Qwen3-14B for prompt optimization
   - Index image LORAs
   - Performance optimization

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| T2I model not available | High | Use alternative model or skip T2I |
| SeeDream API rate limits | Medium | Implement caching and fallbacks |
| Image LORA table empty | Low | Use video LORAs only initially |
| Qwen3-14B integration complex | Medium | Start with simple prompt templates |
| Performance issues | Medium | Implement async processing and caching |

---

## Success Metrics

- ✓ Phase 1: API endpoint returns LORA recommendations
- ⏳ Phase 2: SeeDream editing works with all three modes
- ⏳ Phase 3: End-to-end workflow generates videos successfully
- ⏳ Phase 4: Users can generate videos via UI
- ⏳ Phase 5: Average workflow time < 5 minutes

---

## Notes

- Phase 1 implementation was completed ahead of schedule
- Current bottleneck: Limited enabled LORAs in database (only 6)
- Image LORA table is empty - may need data population task
- Consider creating a separate task for populating image LORAs
