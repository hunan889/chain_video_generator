"""Tests for shared.enums — write FIRST, implement after."""

import pytest
from shared.enums import (
    ModelType, TaskStatus, GenerateMode,
    TaskCategory, category_for_mode,
)


class TestModelType:
    def test_values(self):
        assert ModelType.A14B.value == "a14b"
        assert ModelType.FIVE_B.value == "5b"

    def test_is_str_enum(self):
        assert isinstance(ModelType.A14B, str)
        assert ModelType.A14B == "a14b"

    def test_membership_count(self):
        assert len(ModelType) == 2

    def test_construction_from_value(self):
        assert ModelType("a14b") is ModelType.A14B
        assert ModelType("5b") is ModelType.FIVE_B

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ModelType("invalid")


class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.QUEUED.value == "queued"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"

    def test_is_str_enum(self):
        assert isinstance(TaskStatus.QUEUED, str)
        assert TaskStatus.COMPLETED == "completed"

    def test_membership_count(self):
        assert len(TaskStatus) == 4

    def test_construction_from_value(self):
        assert TaskStatus("failed") is TaskStatus.FAILED

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TaskStatus("unknown")


class TestGenerateMode:
    def test_values(self):
        assert GenerateMode.T2V.value == "t2v"
        assert GenerateMode.I2V.value == "i2v"
        assert GenerateMode.EXTEND.value == "extend"
        assert GenerateMode.VACE_REF2V.value == "vace_ref2v"
        assert GenerateMode.VACE_V2V.value == "vace_v2v"
        assert GenerateMode.VACE_INPAINTING.value == "vace_inpainting"
        assert GenerateMode.VACE_FLF2V.value == "vace_flf2v"
        assert GenerateMode.INTERPOLATE.value == "interpolate"
        assert GenerateMode.UPSCALE.value == "upscale"
        assert GenerateMode.AUDIO.value == "audio"
        assert GenerateMode.FACESWAP.value == "faceswap"
        assert GenerateMode.LORA_DOWNLOAD.value == "lora_download"

    def test_thirdparty_mode_values(self):
        assert GenerateMode.WAN26_T2V.value == "wan26_t2v"
        assert GenerateMode.WAN26_I2V.value == "wan26_i2v"
        assert GenerateMode.SEEDANCE_T2V.value == "seedance_t2v"
        assert GenerateMode.SEEDANCE_I2V.value == "seedance_i2v"
        assert GenerateMode.CLOTHOFF.value == "clothoff"

    def test_membership_count(self):
        assert len(GenerateMode) == 18  # 13 original + 5 third-party

    def test_construction_from_value(self):
        assert GenerateMode("vace_ref2v") is GenerateMode.VACE_REF2V

    def test_construction_thirdparty(self):
        assert GenerateMode("wan26_t2v") is GenerateMode.WAN26_T2V
        assert GenerateMode("seedance_i2v") is GenerateMode.SEEDANCE_I2V

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            GenerateMode("nonexistent")


class TestTaskCategory:
    def test_values(self):
        assert TaskCategory.LOCAL.value == "local"
        assert TaskCategory.THIRDPARTY.value == "thirdparty"
        assert TaskCategory.POSTPROCESS.value == "postprocess"
        assert TaskCategory.UTILITY.value == "utility"

    def test_is_str_enum(self):
        assert isinstance(TaskCategory.LOCAL, str)
        assert TaskCategory.LOCAL == "local"

    def test_membership_count(self):
        assert len(TaskCategory) == 4


class TestCategoryForMode:
    def test_local_modes(self):
        local_modes = [
            GenerateMode.T2V, GenerateMode.I2V, GenerateMode.EXTEND,
            GenerateMode.VACE_REF2V, GenerateMode.VACE_V2V,
            GenerateMode.VACE_INPAINTING, GenerateMode.VACE_FLF2V,
            GenerateMode.FACESWAP,
        ]
        for mode in local_modes:
            assert category_for_mode(mode) == TaskCategory.LOCAL, f"{mode} should be LOCAL"

    def test_thirdparty_modes(self):
        thirdparty_modes = [
            GenerateMode.WAN26_T2V, GenerateMode.WAN26_I2V,
            GenerateMode.SEEDANCE_T2V, GenerateMode.SEEDANCE_I2V,
            GenerateMode.CLOTHOFF,
        ]
        for mode in thirdparty_modes:
            assert category_for_mode(mode) == TaskCategory.THIRDPARTY, f"{mode} should be THIRDPARTY"

    def test_postprocess_modes(self):
        postprocess_modes = [
            GenerateMode.CONCAT, GenerateMode.INTERPOLATE,
            GenerateMode.UPSCALE, GenerateMode.AUDIO,
        ]
        for mode in postprocess_modes:
            assert category_for_mode(mode) == TaskCategory.POSTPROCESS, f"{mode} should be POSTPROCESS"

    def test_utility_modes(self):
        assert category_for_mode(GenerateMode.LORA_DOWNLOAD) == TaskCategory.UTILITY
