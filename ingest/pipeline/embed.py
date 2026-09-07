"""CLIP image embeddings.

Only the **image tower** runs here. The text tower runs at query time inside the
API, from the same pinned checkpoint. That split is the whole reason this is
affordable: the expensive half runs once per item on a machine we control, and
the cheap half runs per query.

The pin is load-bearing
-----------------------
Text queries and image content share a vector space **only** if both came from
the same CLIP checkpoint. Mixing checkpoints does not raise an error — it
returns confident, plausible, wrong results. So the model id is pinned in one
place, stored on every row (`items.embed_model`), and asserted against the
JavaScript side by ``tests/test_model_parity.py``.

If you change ``MODEL_ID``, every existing row is stale and must be re-embedded.
The per-row column is what makes that a detectable, migratable state rather than
silent corruption.
"""

from __future__ import annotations

import io
import os

import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

#: Pinned. See module docstring before changing.
MODEL_ID = os.environ.get("EMBED_MODEL", "openai/clip-vit-base-patch32")

#: Must match the `vector(512)` column in db/migrations/001_init.sql.
EMBED_DIM = 512


class Embedder:
    def __init__(self, model_id: str = MODEL_ID, device: str | None = None) -> None:
        self.model_id = model_id
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CLIPModel.from_pretrained(model_id).to(self.device).eval()
        self.processor = CLIPProcessor.from_pretrained(model_id)

    @torch.inference_mode()
    def embed_images(self, images: list[Image.Image]) -> list[list[float]]:
        """Embed a batch and return L2-normalised vectors.

        Normalising here means cosine similarity reduces to a dot product, and
        pgvector's ``<=>`` cosine operator behaves consistently regardless of
        source image size or dynamic range.
        """
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        features = _as_tensor(self.model.get_image_features(**inputs))
        features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().tolist()

    @torch.inference_mode()
    def embed_text(self, texts: list[str]) -> list[list[float]]:
        """Text tower — used by the search-quality eval and the parity test.

        Production text embedding happens in the API, not here. This exists so
        we can evaluate retrieval offline without deploying anything.
        """
        inputs = self.processor(
            text=texts, return_tensors="pt", padding=True, truncation=True
        ).to(self.device)
        features = _as_tensor(self.model.get_text_features(**inputs))
        features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().tolist()


def _as_tensor(result) -> torch.Tensor:
    """Normalise the return type of ``get_*_features`` across transformers versions.

    transformers <5 returns a plain tensor. transformers 5.x returns a
    ``BaseModelOutputWithPooling`` whose ``pooler_output`` is *already* the
    projected vector in CLIP's shared space — 512-d for ViT-B/32, matching the
    ``vector(512)`` column.

    Getting this wrong is the dangerous kind of bug: reaching for
    ``last_hidden_state`` instead would silently yield 768-d pre-projection
    features that are not in the shared text/image space at all. Nothing would
    raise; search would simply return nonsense. Hence the explicit dimension
    assertion below rather than trusting the shape.
    """
    tensor = result if isinstance(result, torch.Tensor) else result.pooler_output
    if tensor.shape[-1] != EMBED_DIM:
        raise RuntimeError(
            f"expected {EMBED_DIM}-d embeddings, got {tensor.shape[-1]}-d. "
            "This usually means the wrong tensor was taken from the model "
            "output (pre-projection features are not in CLIP's shared space)."
        )
    return tensor


def load_image(data: bytes) -> Image.Image | None:
    """Decode bytes to RGB, or None if undecodable.

    Archives contain plenty of truncated, mislabelled, and exotic files. A bad
    image is an item we skip, never an exception that kills a long harvest.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception:
        return None
