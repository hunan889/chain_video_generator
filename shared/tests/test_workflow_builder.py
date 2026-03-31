"""Tests for shared.workflow_builder module."""

import importlib
from pathlib import Path

import pytest


class TestConfigure:
    """Test the configure() function that replaces api.config imports."""

    def test_configure_updates_paths(self):
        from shared.workflow_builder import configure
        import shared.workflow_builder as wb

        configure(
            workflows_dir="/tmp/wf",
            comfyui_path="/tmp/comfy",
            loras_path="/tmp/loras.yaml",
        )

        assert wb.WORKFLOWS_DIR == Path("/tmp/wf")
        assert wb.COMFYUI_PATH == Path("/tmp/comfy")
        assert wb.LORAS_PATH == Path("/tmp/loras.yaml")

    def test_configure_updates_derived_dirs(self):
        from shared.workflow_builder import configure
        import shared.workflow_builder as wb

        configure(comfyui_path="/tmp/comfy2")

        assert wb.LORAS_DIR == Path("/tmp/comfy2/models/loras")
        assert wb.DIFFUSION_DIR == Path("/tmp/comfy2/models/diffusion_models")
        assert wb.TEXT_ENCODERS_DIR == Path("/tmp/comfy2/models/text_encoders")

    def test_configure_partial_update(self):
        """Only the provided paths should change."""
        from shared.workflow_builder import configure
        import shared.workflow_builder as wb

        configure(workflows_dir="/tmp/only_wf")
        assert wb.WORKFLOWS_DIR == Path("/tmp/only_wf")
        # comfyui_path should not have changed from previous test
        # (module state persists across tests in same process)

    def test_configure_accepts_str_and_path(self):
        from shared.workflow_builder import configure
        import shared.workflow_builder as wb

        configure(workflows_dir=Path("/tmp/path_obj"))
        assert wb.WORKFLOWS_DIR == Path("/tmp/path_obj")

        configure(workflows_dir="/tmp/str_path")
        assert wb.WORKFLOWS_DIR == Path("/tmp/str_path")


class TestLoraInput:
    """Test the shared lightweight LoraInput dataclass."""

    def test_creation_with_defaults(self):
        from shared.schemas import LoraInput

        lora = LoraInput(name="test_lora")
        assert lora.name == "test_lora"
        assert lora.strength == 0.8
        assert lora.trigger_words == []
        assert lora.trigger_prompt is None

    def test_creation_with_all_fields(self):
        from shared.schemas import LoraInput

        lora = LoraInput(
            name="my_lora",
            strength=0.7,
            trigger_words=["word1", "word2"],
            trigger_prompt="custom prompt",
        )
        assert lora.name == "my_lora"
        assert lora.strength == 0.7
        assert lora.trigger_words == ["word1", "word2"]
        assert lora.trigger_prompt == "custom prompt"

    def test_creation_from_dict_kwargs(self):
        """workflow_builder uses LoraInput(**dict) pattern."""
        from shared.schemas import LoraInput

        data = {"name": "dict_lora", "strength": 0.5}
        lora = LoraInput(**data)
        assert lora.name == "dict_lora"
        assert lora.strength == 0.5


class TestSharedEnums:
    """Test that shared enums match the original api.models.enums values."""

    def test_model_type_values(self):
        from shared.enums import ModelType

        assert ModelType.A14B == "a14b"
        assert ModelType.FIVE_B == "5b"

    def test_generate_mode_values(self):
        from shared.enums import GenerateMode

        assert GenerateMode.T2V == "t2v"
        assert GenerateMode.I2V == "i2v"
        assert GenerateMode.EXTEND == "extend"
        assert GenerateMode.VACE_REF2V == "vace_ref2v"
        assert GenerateMode.VACE_V2V == "vace_v2v"
        assert GenerateMode.VACE_INPAINTING == "vace_inpainting"
        assert GenerateMode.VACE_FLF2V == "vace_flf2v"


class TestModuleExports:
    """Test that key public names are importable from shared.workflow_builder."""

    @pytest.mark.parametrize("name", [
        "build_workflow",
        "build_story_workflow",
        "build_merged_story_workflow",
        "build_interpolate_workflow",
        "build_upscale_workflow",
        "build_image_upscale_workflow",
        "build_audio_workflow",
        "build_face_swap_workflow",
        "configure",
        "MODEL_PRESETS",
        "T5_PRESETS",
        "WORKFLOW_MAP",
    ])
    def test_public_names_importable(self, name):
        import shared.workflow_builder as wb
        assert hasattr(wb, name), f"{name} not found in shared.workflow_builder"

    @pytest.mark.parametrize("name", [
        "_inject_story_postproc",
        "_inject_lossless_frame_save",
        "_inject_trigger_words",
        "_load_template",
        "_load_lora_name_map",
        "_find_lora_file",
    ])
    def test_private_names_importable(self, name):
        import shared.workflow_builder as wb
        assert hasattr(wb, name), f"{name} not found in shared.workflow_builder"
