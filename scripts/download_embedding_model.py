#!/usr/bin/env python3
"""
使用ModelScope SDK下载Qwen2.5-Embedding-7B模型
"""
import os
from modelscope import snapshot_download

model_dir = snapshot_download(
    'Qwen/Qwen2.5-Embedding-7B',
    cache_dir='/home/gime/soft',
    revision='master'
)

print(f"Model downloaded to: {model_dir}")
