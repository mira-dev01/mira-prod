"""Re-hosts property photos scraped via Bright Data (see
app/services/airbnb_import.py's parse_bright_data_listing) onto Cloudinary
rather than storing Airbnb's own a0.muscache.com URLs directly. MIRA owns
the copy this way -- it survives the source listing being edited/removed,
and a resized/optimized version can be served to guests later rather than
hotlinking Airbnb's CDN.

The cloudinary SDK is sync (urllib-based, no asyncio support), so uploads
run in a thread via asyncio.to_thread to avoid blocking the event loop
during an async import job.
"""

import asyncio
import logging
import uuid

import cloudinary
import cloudinary.uploader

from app.config import settings

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured() -> bool:
    global _configured
    if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret):
        return False
    if not _configured:
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )
        _configured = True
    return True


async def upload_image_from_url(source_url: str, folder: str) -> str | None:
    """Uploads one remote image (Cloudinary fetches it server-side, no
    download through this process needed) into `folder`. Returns the
    Cloudinary-hosted secure URL, or None if Cloudinary isn't configured or
    the upload fails -- callers should treat that as "skip this image", not
    an error that aborts the whole import (same stance as
    BRIGHT_DATA_API_KEY/SMTP_* being unset elsewhere in this codebase)."""
    if not _ensure_configured():
        return None

    try:
        result = await asyncio.to_thread(
            cloudinary.uploader.upload,
            source_url,
            folder=folder,
            public_id=str(uuid.uuid4()),
            resource_type="image",
        )
        return result.get("secure_url")
    except Exception:  # noqa: BLE001 - one bad image shouldn't fail the whole import
        logger.warning("Cloudinary upload failed for %s", source_url, exc_info=True)
        return None


async def upload_images_from_urls(source_urls: list[str], folder: str, max_images: int = 10) -> list[str]:
    """Uploads up to max_images of source_urls concurrently, in listing
    order. Airbnb listings commonly have 20-30+ photos -- capped rather than
    mirroring all of them, since these are meant for a guest-facing preview,
    not a full gallery archive. Skips (doesn't raise) on individual failures
    or when Cloudinary isn't configured, so a partial/empty result is normal,
    not an error the caller needs to handle specially."""
    results = await asyncio.gather(
        *(upload_image_from_url(url, folder) for url in source_urls[:max_images])
    )
    return [url for url in results if url]
