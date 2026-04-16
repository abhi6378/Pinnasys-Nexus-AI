from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

try:
    from openai import OpenAI
    _openai_import_error: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-specific import issue
    OpenAI = None
    _openai_import_error = exc

_client: Any | None = None


def embedding_content_hash(text: str, *, model_name: str = "") -> str:
    material = f"{model_name}|{text}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class OpenAIEmbeddingService:
    def __init__(self, model_name: str | None = None):
        self.model_name = str(model_name or EMBEDDING_MODEL)

    def is_available(self) -> bool:
        return OpenAI is not None and bool(os.getenv("OPENAI_API_KEY", "").strip())

    def _get_client(self):
        global _client
        if _client is not None:
            return _client
        if OpenAI is None:
            raise RuntimeError(f"OpenAI client import failed: {_openai_import_error}")
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return _client

    def embed_text(self, text: str) -> list[float] | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if not self.is_available():
            return None
        try:
            response = self._get_client().embeddings.create(
                model=self.model_name,
                input=cleaned[:8000],
            )
            data = getattr(response, "data", None) or []
            if not data:
                return None
            return list(getattr(data[0], "embedding", None) or [])
        except Exception as exc:
            logger.warning("Embedding generation failed for model=%s: %s", self.model_name, exc)
            return None


def get_embedding_service(model_name: str | None = None) -> OpenAIEmbeddingService:
    return OpenAIEmbeddingService(model_name=model_name)
