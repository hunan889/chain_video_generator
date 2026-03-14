#!/usr/bin/env python3
"""Test LoRA injection for A14B model to debug channel mismatch issue."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from api.services.workflow_builder import build_workflow, _find_lora_file
from api.models.schemas import LoraInput
from api.models.enums import GenerateMode, ModelType

# Test the problematic LoRA
lora = LoraInput(name="doggystyle_sex_multiple_angles", strength=0.8)

print("="*80)
print("Testing LoRA injection for A14B model with nsfw_v2 preset")
print("="*80)

# Build workflow with the problematic LoRA (now using I2V mode)
workflow = build_workflow(
    mode=GenerateMode.I2V,  # Changed from T2V to I2V
    model=ModelType.A14B,
    prompt="Test prompt",
    negative_prompt="",
    width=416,
    height=736,
    num_frames=81,
    fps=16,
    steps=4,
    cfg=1.0,
    shift=5.0,
    seed=12345,
    loras=[lora],
    scheduler="euler",
    model_preset="nsfw_v2",
    t5_preset="nsfw",
    image_filename="test.png",  # Required for I2V mode
)

print("\nWorkflow nodes:")
print("-"*80)

# Find all model loader nodes
model_nodes = []
for node_id, node in workflow.items():
    if node.get("class_type") == "WanVideoModelLoader":
        model_name = node.get("inputs", {}).get("model", "")
        model_nodes.append((node_id, model_name))
        print(f"Model Node {node_id}: {model_name}")

print("\nLoRA nodes:")
print("-"*80)

# Find all LoRA nodes
lora_nodes = []
for node_id, node in workflow.items():
    if node.get("class_type") == "WanVideoLoraSelect":
        lora_file = node.get("inputs", {}).get("lora", "")
        lora_nodes.append((node_id, lora_file))
        print(f"LoRA Node {node_id}: {lora_file}")

print("\n" + "="*80)
print("Analysis:")
print("="*80)

# Test _find_lora_file for both HIGH and LOW variants
base_name = "Wan2.2 - T2V - Doggystyle v5 - HIGH 14B"
print(f"\nBase name: {base_name}")

high_file = _find_lora_file(base_name, "high", None)
print(f"HIGH variant: {high_file}")

low_file = _find_lora_file(base_name, "low", None)
print(f"LOW variant: {low_file}")

print("\n" + "="*80)
print("Conclusion:")
print("="*80)

if len(model_nodes) == 2:
    print(f"✓ Found 2 model nodes (HIGH and LOW)")
else:
    print(f"✗ Expected 2 model nodes, found {len(model_nodes)}")

if len(lora_nodes) == 0:
    print(f"✓ No LoRA nodes injected (correct - LOW variant not found)")
elif len(lora_nodes) == 1:
    print(f"⚠ 1 LoRA node injected (only HIGH stage)")
elif len(lora_nodes) == 2:
    print(f"✗ 2 LoRA nodes injected (both HIGH and LOW)")
    print(f"   This will cause channel mismatch in LOW stage!")
else:
    print(f"? Unexpected number of LoRA nodes: {len(lora_nodes)}")

if low_file is None:
    print(f"✓ LOW variant correctly returns None")
else:
    print(f"✗ LOW variant should be None, but got: {low_file}")
