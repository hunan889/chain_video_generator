import json
import logging
import yaml
import httpx
from pathlib import Path
from api.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LORAS_PATH
from api.models.schemas import LoraInput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a LoRA selector for Wan2.2 AI video generation.
Given a user's video prompt, select 0-3 LoRAs from the catalog below that best match the described scene.

## LoRA Catalog:
{catalog}

## Rules:
- Select ONLY LoRAs whose effect directly matches what the user describes
- If no LoRA is relevant, return an empty list
- Maximum 3 LoRAs

## Strength guidelines:
- Single LoRA: use 0.7-0.9 (higher if it's the main focus)
- Two LoRAs: use 0.6-0.8 each (reduce to avoid conflicts)
- Three LoRAs: use 0.5-0.7 each
- Style/quality LoRAs (e.g. instagirl): 0.4-0.6
- Action/pose LoRAs (e.g. cowgirl, paizuri): 0.7-0.9
- Body/attribute LoRAs (e.g. big_breasts): 0.5-0.7
- If a LoRA is secondary to the scene, lower its strength

## Output format (ONLY valid JSON, no markdown, no explanation):
{{"loras": [{{"name": "exact_name_from_catalog", "strength": 0.7}}]}}"""


class LoraSelector:
    def __init__(self):
        self.api_key = LLM_API_KEY
        self.model = LLM_MODEL
        base = LLM_BASE_URL.rstrip("/")
        self.url = f"{base}/chat/completions"
        self._catalog: list[dict] | None = None
        self._valid_names: set[str] | None = None

    def _load_lora_catalog(self) -> tuple[list[dict], set[str]]:
        """Load LoRA catalog from loras.yaml. Cached after first call."""
        if self._catalog is not None:
            return self._catalog, self._valid_names
        try:
            with open(LORAS_PATH, "r") as f:
                data = yaml.safe_load(f)
            entries = data.get("loras", [])
            self._catalog = []
            self._valid_names = set()
            for e in entries:
                self._catalog.append({
                    "name": e["name"],
                    "description": e.get("description", ""),
                    "tags": e.get("tags", []),
                    "default_strength": e.get("default_strength", 0.8),
                    "trigger_words": e.get("trigger_words", []),
                })
                self._valid_names.add(e["name"])
        except Exception as exc:
            logger.error("Failed to load lora catalog: %s", exc)
            self._catalog = []
            self._valid_names = set()
        return self._catalog, self._valid_names
    def _get_candidates(self) -> list[dict]:
        """Return candidate LoRAs. Currently returns full catalog.
        Future: embed-based pre-filtering for large catalogs."""
        catalog, _ = self._load_lora_catalog()
        return catalog

    def _format_catalog(self, candidates: list[dict]) -> str:
        lines = []
        for c in candidates:
            parts = [f"- name: {c['name']} | {c['description']}"]
            if c["tags"]:
                parts.append(f"  tags: {', '.join(c['tags'])}")
            if c["trigger_words"]:
                short = [tw[:80] for tw in c["trigger_words"][:2]]
                parts.append(f"  trigger_words: {'; '.join(short)}")
            parts.append(f"  default_strength: {c['default_strength']}")
            lines.append("\n".join(parts))
        return "\n".join(lines)

    async def select(self, prompt: str) -> list[LoraInput]:
        """Select matching LoRAs for the given prompt via LLM."""
        candidates = self._get_candidates()
        if not candidates:
            return []
        _, valid_names = self._load_lora_catalog()
        catalog_text = self._format_catalog(candidates)
        system = SYSTEM_PROMPT.format(catalog=catalog_text)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    self.url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 512,  # Increased from 256 to avoid JSON truncation
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            # Fix incomplete JSON (missing closing brace)
            if text.startswith("{") and not text.endswith("}"):
                text = text + "}"
            result = json.loads(text)
            loras = []
            for item in result.get("loras", []):
                name = item.get("name", "")
                if name not in valid_names:
                    logger.warning("LLM suggested unknown LoRA '%s', skipping", name)
                    continue
                strength = float(item.get("strength", 0.8))
                strength = max(-2.0, min(2.0, strength))
                loras.append(LoraInput(name=name, strength=strength))
            logger.info("Auto-selected %d LoRAs for prompt: %s", len(loras),
                        [l.name for l in loras])
            return loras[:3]
        except Exception as exc:
            logger.error("LoRA auto-selection failed: %s", exc)
            return []
