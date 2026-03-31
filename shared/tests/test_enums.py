"""Tests for shared.enums — write FIRST, implement after."""

import pytest
from shared.enums import ModelType, TaskStatus, GenerateMode


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

    def test_membership_count(self):
        assert len(GenerateMode) == 7

    def test_construction_from_value(self):
        assert GenerateMode("vace_ref2v") is GenerateMode.VACE_REF2V

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            GenerateMode("nonexistent")
