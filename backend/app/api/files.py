"""Vault file upload / delete endpoints."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_kb
from app.core.log import get_logger
from app.services.kb_registry import KBContext

logger = get_logger("API")
router = APIRouter()


_AUDIO_CONTENT_TYPES = {"audio/webm", "audio/ogg", "audio/opus", "audio/x-matroska"}
_AUDIO_EXTENSIONS = {"webm", "ogg", "opus"}


async def _transcode_to_m4a(content: bytes, src_ext: str) -> tuple[bytes, str]:
    """Transcode audio bytes → AAC/M4A via ffmpeg.

    Returns ``(transcoded_bytes, "m4a")`` on success, or the original
    ``(content, src_ext)`` if ffmpeg is unavailable or fails (graceful fallback).
    """

    def _run() -> tuple[bytes, str]:
        tmp_in = tmp_out = None
        try:
            fd, tmp_in = tempfile.mkstemp(suffix=f".{src_ext}")
            os.write(fd, content)
            os.close(fd)
            tmp_out = tmp_in[: tmp_in.rfind(".")] + ".m4a"
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    tmp_in,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    tmp_out,
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if result.returncode == 0:
                with open(tmp_out, "rb") as f:
                    return f.read(), "m4a"
            logger.warning(
                "FFmpeg transcoding failed",
                extra={"stderr": result.stderr.decode(errors="replace")[:500]},
            )
        except FileNotFoundError:
            logger.warning(
                "ffmpeg not found on PATH — storing audio without transcoding"
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "ffmpeg transcoding timed out — storing audio without transcoding"
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Audio transcoding error", extra={"error": str(exc)})
        finally:
            if tmp_in and os.path.exists(tmp_in):
                os.unlink(tmp_in)
            if tmp_out and os.path.exists(tmp_out):
                os.unlink(tmp_out)
        return content, src_ext

    return await asyncio.to_thread(_run)


@router.post("/api/v1/upload")
async def upload_file(
    file: UploadFile = File(...),
    kb: KBContext = Depends(get_kb),
):
    """Upload a file into the current KB vault attachments folder."""
    logger.info(f"Uploading file: {file.filename}")

    if not kb.vault_path:
        raise HTTPException(status_code=400, detail="No vault configured for this knowledge base")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    content_type = file.content_type or ""
    content = await file.read()

    if content_type in _AUDIO_CONTENT_TYPES or ext in _AUDIO_EXTENSIONS:
        content, ext = await _transcode_to_m4a(content, ext)
        content_type = "audio/mp4"
        filename_hint = f"recording.{ext}"
    else:
        filename_hint = file.filename or f"file.{ext}"

    # Always store in the KB vault (no S3 for Orb desktop/local).
    from app.services.local_storage import store_upload

    try:
        result = await store_upload(Path(kb.vault_path), filename_hint, content, kb.kb_id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Vault upload failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    logger.info(f"File uploaded to vault: {result['url']}")
    return {
        "filename": file.filename,
        "url": result["url"],
        "rel_path": result["key"],
        "key": result["key"],
        "status": "success",
    }


@router.delete("/api/v1/files/{file_key:path}")
async def delete_file(file_key: str, kb: KBContext = Depends(get_kb)):
    """Delete an uploaded vault attachment (key or /vault-files/... URL)."""
    from app.services.local_storage import remove_upload

    if not kb.vault_path:
        raise HTTPException(status_code=400, detail="No vault configured for this knowledge base")
    logger.info("Deleting file: %s", file_key)
    await remove_upload(Path(kb.vault_path), file_key)
    logger.info("File deleted successfully: %s", file_key)
    return {"status": "deleted", "file_key": file_key}
