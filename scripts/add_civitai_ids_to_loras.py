#!/usr/bin/env python3
"""
Add CivitAI IDs to existing LoRA files based on loras.yaml configuration.
This allows ID-based matching instead of fuzzy filename matching.
"""
import sys
import yaml
from pathlib import Path
import safetensors.torch
import torch
import shutil

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.config import LORAS_PATH, COMFYUI_PATH

LORAS_DIR = COMFYUI_PATH / "models" / "loras"


def add_civitai_id_to_file(file_path: Path, civitai_id: int, civitai_version_id: int) -> bool:
    """Add CivitAI IDs to a safetensors file's metadata."""
    try:
        # Load existing tensors and metadata
        tensors = {}
        metadata = {}

        with safetensors.torch.safe_open(file_path, framework="pt") as f:
            # Copy existing metadata
            if f.metadata():
                metadata = dict(f.metadata())

            # Load all tensors
            for key in f.keys():
                tensors[key] = f.get_tensor(key)

        # Add CivitAI IDs to metadata
        metadata["civitai_model_id"] = str(civitai_id)
        metadata["civitai_version_id"] = str(civitai_version_id)

        # Create backup
        backup_path = file_path.with_suffix('.safetensors.bak')
        shutil.copy2(file_path, backup_path)

        # Save with updated metadata
        safetensors.torch.save_file(tensors, file_path, metadata=metadata)

        # Remove backup if successful
        backup_path.unlink()

        return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
        # Restore from backup if it exists
        backup_path = file_path.with_suffix('.safetensors.bak')
        if backup_path.exists():
            shutil.copy2(backup_path, file_path)
            backup_path.unlink()
        return False


def main():
    # Load loras.yaml
    with open(LORAS_PATH) as f:
        data = yaml.safe_load(f)

    loras_config = data.get("loras", [])

    print(f"Found {len(loras_config)} LoRA configurations")
    print(f"Scanning {LORAS_DIR}...\n")

    # Helper function to check if filename matches base
    def matches_base(filename: str, base: str) -> bool:
        fname_lower = filename.lower().replace('.safetensors', '')
        base_lower = base.lower()
        return base_lower in fname_lower

    updated_count = 0
    skipped_count = 0

    for lora_config in loras_config:
        civitai_id = lora_config.get("civitai_id")
        civitai_version_id = lora_config.get("civitai_version_id")
        base_name = lora_config.get("file")
        lora_name = lora_config.get("name")

        if not civitai_id or not civitai_version_id or not base_name:
            continue

        # Find matching files
        matching_files = [
            f for f in LORAS_DIR.glob("*.safetensors")
            if matches_base(f.name, base_name)
        ]

        if not matching_files:
            print(f"⚠️  No files found for: {lora_name} (base: {base_name})")
            continue

        for file_path in matching_files:
            # Check if ID already exists
            try:
                with safetensors.torch.safe_open(file_path, framework="pt") as f:
                    meta = f.metadata()
                    if meta and "civitai_model_id" in meta:
                        print(f"⏭️  {file_path.name}: Already has ID")
                        skipped_count += 1
                        continue
            except Exception as e:
                print(f"⚠️  {file_path.name}: Cannot read metadata: {e}")
                continue

            # Add ID
            print(f"📝 {file_path.name}: Adding ID {civitai_id}/{civitai_version_id}...", end=" ")
            if add_civitai_id_to_file(file_path, civitai_id, civitai_version_id):
                print("✅")
                updated_count += 1
            else:
                print("❌")

    print(f"\n✅ Updated: {updated_count} files")
    print(f"⏭️  Skipped: {skipped_count} files (already have IDs)")


if __name__ == "__main__":
    main()
