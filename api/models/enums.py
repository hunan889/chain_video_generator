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
