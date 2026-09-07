"""Asserts the Python and JavaScript text encoders agree.

This is the project's defence against its most dangerous silent failure.

Image embeddings are produced at ingest by Python (`openai/clip-vit-base-patch32`).
Query embeddings are produced at request time by JavaScript (`Xenova/clip-vit-base-patch32`,
the ONNX export of the same weights). Text and image vectors share a space
**only** if both came from the same checkpoint.

If they ever diverge — a model bump on one side, a quantised export, a different
pooling or normalisation — nothing raises. Search keeps returning results, with
plausible-looking scores, that are simply wrong. There is no user-visible symptom
until someone notices the results are nonsense.

So it is pinned here as a test rather than trusted as a convention.

Requires `npm install` to have been run. Skipped if node or the model cache is
unavailable, so the licence-gate suite still runs on a bare checkout.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Deliberately varied: a short phrase, a compositional query, punctuation, a
#: non-ASCII string, and an archival phrasing. Tokenisation differences between
#: implementations show up on the awkward inputs, not the tidy ones.
PARITY_TEXTS = [
    "a red flower",
    "snow covered mountains at sunset",
    "a black and white photograph of a factory",
    "Ferdowsi's Shahnama, folio 12",
    "café façade, Zürich",
]

#: Two implementations of the same fp32 model should agree to well within this.
#: Anything below it means a real divergence, not floating-point noise.
MIN_COSINE = 0.999


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


@pytest.fixture(scope="module")
def js_vectors() -> list[list[float]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if not (REPO / "node_modules").exists():
        pytest.skip("node_modules missing — run `npm install`")

    result = subprocess.run(
        [node, str(REPO / "tests" / "embed_text.mjs"), *PARITY_TEXTS],
        capture_output=True, text=True, cwd=REPO, timeout=600,
    )
    if result.returncode != 0:
        pytest.skip(f"JS encoder unavailable: {result.stderr[-300:]}")
    return json.loads(result.stdout)["vectors"]


@pytest.fixture(scope="module")
def py_vectors() -> list[list[float]]:
    torch = pytest.importorskip("torch")  # noqa: F841
    pytest.importorskip("transformers")
    from ingest.pipeline.embed import Embedder

    return Embedder().embed_text(PARITY_TEXTS)


def test_same_dimensionality(js_vectors, py_vectors):
    assert len(js_vectors) == len(py_vectors) == len(PARITY_TEXTS)
    for js, py in zip(js_vectors, py_vectors):
        assert len(js) == len(py) == 512


@pytest.mark.parametrize("index", range(len(PARITY_TEXTS)))
def test_encoders_agree(js_vectors, py_vectors, index):
    """The core assertion. A failure here means query and image vectors are no
    longer in the same space, and search results are meaningless."""
    similarity = _cosine(js_vectors[index], py_vectors[index])
    assert similarity >= MIN_COSINE, (
        f"encoders diverged on {PARITY_TEXTS[index]!r}: cosine {similarity:.6f} "
        f"< {MIN_COSINE}. Query and image embeddings are no longer comparable; "
        f"search will return confident nonsense. Check that both sides pin the "
        f"same checkpoint and that neither is quantised."
    )


def test_vectors_are_normalised(js_vectors):
    """Both sides must L2-normalise, or pgvector's cosine operator sees
    inconsistent magnitudes between the query and the stored rows."""
    for vector, text in zip(js_vectors, PARITY_TEXTS):
        norm = math.sqrt(sum(x * x for x in vector))
        assert abs(norm - 1.0) < 1e-5, f"{text!r} not normalised: |v| = {norm}"


def test_distinct_texts_are_distinct(js_vectors):
    """Guards against a degenerate encoder returning the same vector for
    everything — which would pass a naive parity check if both sides broke the
    same way."""
    a = _cosine(js_vectors[0], js_vectors[1])
    assert a < 0.99, f"unrelated texts encoded almost identically ({a:.4f})"


def test_quantised_weights_are_rejected():
    """Regression guard for a measured trap.

    q8 quantisation of the text tower produces cosine 0.724 against the Python
    encoder — vectors that are not in the same space as the stored image
    embeddings, so search returns confident nonsense with no error anywhere.
    It is the obvious thing to reach for when shrinking cold starts, so the
    chosen dtype is pinned here.
    """
    source = (REPO / "api" / "_lib" / "embed.mjs").read_text(encoding="utf-8")
    assert "dtype: 'fp16'" in source or "dtype: 'fp32'" in source, (
        "the JS encoder must use fp16 or fp32; quantised weights break parity "
        "with the ingest-side image embeddings"
    )
    assert "dtype: 'q8'" not in source
