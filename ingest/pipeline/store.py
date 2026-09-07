"""Storage: files to R2, metadata and vectors to Postgres.

Runs locally and holds write credentials. Nothing in this module is ever
deployed — the API reads with an anon key constrained by row-level security.

Idempotency
-----------
Everything here is safe to re-run. Object keys are derived deterministically
from ``(source, source_id)``, and the database write is an upsert on the same
natural key. A harvest that dies halfway can simply be run again; it will
overwrite the objects it already wrote and update the rows it already made,
rather than duplicating them.
"""

from __future__ import annotations

import io
import json
import os

import boto3
import psycopg
from botocore.config import Config
from PIL import Image

THUMB_MAX = 512  # longest edge, px


class Storage:
    def __init__(self) -> None:
        self.bucket = _require("R2_BUCKET")
        self.public_base = _require("R2_PUBLIC_BASE").rstrip("/")
        self.dsn = _require("POSTGRES_DSN")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=_require("R2_ENDPOINT"),
            aws_access_key_id=_require("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=_require("R2_SECRET_ACCESS_KEY"),
            # R2 speaks S3 but is not AWS; it ignores the region while still
            # requiring one to be present for request signing.
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    # -- files --------------------------------------------------------------

    def put_image(self, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
            # Immutable: keys are content-addressed by source id, so a given key
            # always holds the same logical item. Long cache lifetimes are free
            # correctness here, and R2 egress is already $0.
            CacheControl="public, max-age=31536000, immutable",
        )

    def put_thumbnail(self, key: str, image: Image.Image) -> tuple[int, int]:
        thumb = image.copy()
        thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
        buffer = io.BytesIO()
        # Thumbnails are for a results grid, not archival fidelity. JPEG q82
        # keeps a 24-image page well under a megabyte.
        thumb.save(buffer, format="JPEG", quality=82, optimize=True)
        self.put_image(key, buffer.getvalue(), "image/jpeg")
        return thumb.size

    # -- database -----------------------------------------------------------

    def upsert(self, row: dict) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into items (
                    source, source_id, source_url, title, description, creator,
                    license_id, license_url, license_tier, attribution,
                    raw_license_meta, r2_key, thumb_key, width, height, mime,
                    bytes, embedding, embed_model
                ) values (
                    %(source)s, %(source_id)s, %(source_url)s, %(title)s,
                    %(description)s, %(creator)s, %(license_id)s, %(license_url)s,
                    %(license_tier)s, %(attribution)s, %(raw_license_meta)s,
                    %(r2_key)s, %(thumb_key)s, %(width)s, %(height)s, %(mime)s,
                    %(bytes)s, %(embedding)s, %(embed_model)s
                )
                on conflict (source, source_id) do update set
                    source_url       = excluded.source_url,
                    title            = excluded.title,
                    description      = excluded.description,
                    creator          = excluded.creator,
                    license_id       = excluded.license_id,
                    license_url      = excluded.license_url,
                    license_tier     = excluded.license_tier,
                    attribution      = excluded.attribution,
                    raw_license_meta = excluded.raw_license_meta,
                    r2_key           = excluded.r2_key,
                    thumb_key        = excluded.thumb_key,
                    width            = excluded.width,
                    height           = excluded.height,
                    mime             = excluded.mime,
                    bytes            = excluded.bytes,
                    embedding        = excluded.embedding,
                    embed_model      = excluded.embed_model
                """,
                row,
            )
            conn.commit()

    def count(self) -> int:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("select count(*) from items")
            return cur.fetchone()[0]


def object_keys(source: str, source_id: str, extension: str) -> tuple[str, str]:
    """Deterministic keys, so re-running a harvest overwrites rather than
    duplicates. Sharded by source to keep any one prefix from growing unbounded.
    """
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in source_id)[:120]
    return (
        f"{source}/full/{safe_id}.{extension}",
        f"{source}/thumb/{safe_id}.jpg",
    )


def to_row(item, decision, *, r2_key, thumb_key, embedding, embed_model,
           width, height, mime, size_bytes, attribution) -> dict:
    """Flatten a gated item into the database row shape.

    ``raw_license_meta`` carries the source's original claim, verbatim. It is
    the evidence of what we were told and when, and nothing downstream may edit
    it.
    """
    return {
        "source": item.source,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "title": item.title,
        "description": item.description,
        "creator": item.creator,
        "license_id": decision.license_id,
        "license_url": item.license_url or decision.license.url,
        "license_tier": decision.license.tier,
        "attribution": attribution,
        "raw_license_meta": json.dumps({
            "claimed_license_url": item.license_url,
            "claimed_license_text": item.license_text,
            "claimed_license_id": item.explicit_license_id,
            "source_response": _truncate_json(item.raw),
        }),
        "r2_key": r2_key,
        "thumb_key": thumb_key,
        "width": width,
        "height": height,
        "mime": mime,
        "bytes": size_bytes,
        "embedding": str(embedding),   # pgvector accepts the '[1,2,3]' literal
        "embed_model": embed_model,
    }


def _truncate_json(payload: dict, limit: int = 8000) -> dict:
    """Keep the evidence, but don't let one verbose source bloat every row."""
    encoded = json.dumps(payload, default=str)
    if len(encoded) <= limit:
        return payload
    return {"_truncated": True, "_original_bytes": len(encoded),
            "excerpt": encoded[:limit]}


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in — "
            "ingestion needs write credentials and will not guess them."
        )
    return value
