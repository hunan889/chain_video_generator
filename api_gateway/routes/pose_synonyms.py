"""Pose synonyms admin endpoints.

Reads/writes the POSE_SYNONYMS dict in api/services/pose_synonyms.py.
"""

import ast
import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["pose-synonyms"])

# Resolve path to the synonyms file (project root / api / services / pose_synonyms.py)
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SYNONYMS_FILE = os.path.join(_PROJECT_ROOT, "api", "services", "pose_synonyms.py")


class UpdateSynonymsRequest(BaseModel):
    synonyms: list[str]


def _read_synonyms() -> dict[str, list[str]]:
    """Parse POSE_SYNONYMS from the Python source file using AST."""
    if not os.path.isfile(_SYNONYMS_FILE):
        raise HTTPException(status_code=404, detail="Synonyms file not found")

    with open(_SYNONYMS_FILE, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.error("Failed to parse synonyms file: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to parse synonyms file")

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "POSE_SYNONYMS":
                    try:
                        value = ast.literal_eval(node.value)
                        return value
                    except (ValueError, TypeError) as exc:
                        logger.error("Failed to evaluate POSE_SYNONYMS: %s", exc)
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to evaluate POSE_SYNONYMS dict",
                        )

    raise HTTPException(status_code=500, detail="POSE_SYNONYMS not found in file")


def _write_synonyms(synonyms: dict[str, list[str]]) -> None:
    """Rewrite the synonyms file, preserving helper functions below the dict."""
    if not os.path.isfile(_SYNONYMS_FILE):
        raise HTTPException(status_code=404, detail="Synonyms file not found")

    with open(_SYNONYMS_FILE, "r", encoding="utf-8") as f:
        source = f.read()

    # Find the end of the POSE_SYNONYMS dict assignment.
    # Strategy: parse the AST to find the line range, then splice.
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        logger.error("Failed to parse synonyms file for write: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to parse synonyms file")

    dict_start_line = None
    dict_end_line = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "POSE_SYNONYMS":
                    dict_start_line = node.lineno  # 1-indexed
                    dict_end_line = node.end_lineno  # 1-indexed, inclusive

    if dict_start_line is None or dict_end_line is None:
        raise HTTPException(status_code=500, detail="POSE_SYNONYMS not found in file")

    lines = source.splitlines(keepends=True)

    # Build the new POSE_SYNONYMS assignment
    new_dict_str = _format_synonyms_dict(synonyms)

    # Replace lines [dict_start_line-1 .. dict_end_line-1] (inclusive)
    new_lines = lines[: dict_start_line - 1] + [new_dict_str + "\n"] + lines[dict_end_line:]

    with open(_SYNONYMS_FILE, "w", encoding="utf-8") as f:
        f.write("".join(new_lines))


def _format_synonyms_dict(synonyms: dict[str, list[str]]) -> str:
    """Format the POSE_SYNONYMS dict as readable Python source."""
    parts = ["POSE_SYNONYMS = {"]
    for key, values in synonyms.items():
        values_repr = repr(values)
        parts.append(f"    {key!r}: {values_repr},")
    parts.append("}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/admin/pose-synonyms")
def list_synonyms():
    """List all pose synonyms."""
    synonyms = _read_synonyms()
    return {"synonyms": synonyms}


@router.put("/admin/pose-synonyms/{pose_key}")
def update_synonyms(
    pose_key: str,
    req: UpdateSynonymsRequest,
):
    """Update synonyms for a specific pose key."""
    synonyms = _read_synonyms()
    synonyms[pose_key] = req.synonyms
    try:
        _write_synonyms(synonyms)
    except Exception:
        logger.exception("Failed to write synonyms file")
        raise HTTPException(status_code=500, detail="Failed to write synonyms file")

    return {"pose_key": pose_key, "synonyms": req.synonyms}
