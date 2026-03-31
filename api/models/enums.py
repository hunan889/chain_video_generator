from enum import Enum


class ModelType(str, Enum):
    A14B = "a14b"
    FIVE_B = "5b"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerateMode(str, Enum):
    T2V = "t2v"
    I2V = "i2v"
    EXTEND = "extend"
    VACE_REF2V = "vace_ref2v"
    VACE_V2V = "vace_v2v"
    VACE_INPAINTING = "vace_inpainting"
    VACE_FLF2V = "vace_flf2v"
