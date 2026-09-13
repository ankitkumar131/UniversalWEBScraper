"""Artifact storage: raw HTML snapshots, screenshots, exports.

Local filesystem by default; S3/MinIO via boto3 when configured (settings
storage.s3.enabled). Path scheme: <artifacts_dir>/<job_id>/page_NNN.html
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class ArtifactStore:
    def __init__(self, artifacts_dir: Path, s3_config: dict[str, Any] | None = None):
        self.root = artifacts_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._s3 = None
        if s3_config and s3_config.get("enabled"):
            try:
                import boto3  # optional dependency
                self._s3 = boto3.client("s3", endpoint_url=s3_config.get("endpoint_url"))
                self._bucket = s3_config["bucket"]
                log.info("s3_artifacts_enabled", bucket=self._bucket)
            except Exception as e:  # pragma: no cover
                log.warning("s3_init_failed", error=str(e))

    async def save_html(self, job_id: str, page_number: int, html: str) -> str:
        path = self.root / job_id / f"page_{page_number:03d}.html"
        return await self._write(path, html.encode("utf-8"))

    async def save_screenshot(self, job_id: str, page_number: int, png: bytes) -> str:
        path = self.root / job_id / f"page_{page_number:03d}.png"
        return await self._write(path, png)

    async def save_export(self, job_id: str, filename: str, content: str | bytes) -> str:
        path = self.root / job_id / filename
        data = content.encode("utf-8") if isinstance(content, str) else content
        return await self._write(path, data)

    def artifact_url(self, job_id: str, filename: str) -> str:
        """Public-ish relative URL for the API to serve artifacts from."""
        return f"/api/jobs/{job_id}/artifacts/{filename}"

    async def read(self, job_id: str, filename: str) -> bytes:
        path = self.root / job_id / filename
        return await asyncio.to_thread(path.read_bytes)

    async def list_artifacts(self, job_id: str) -> list[str]:
        d = self.root / job_id
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    async def _write(self, path: Path, data: bytes) -> str:
        def _do() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return str(path)

        location = await asyncio.to_thread(_do)
        if self._s3 is not None:  # best-effort mirror to object storage
            key = f"{path.parent.name}/{path.name}"
            try:
                await asyncio.to_thread(
                    self._s3.put_object, Bucket=self._bucket, Key=key, Body=data
                )
            except Exception as e:  # pragma: no cover
                log.warning("s3_put_failed", key=key, error=str(e))
        return location
