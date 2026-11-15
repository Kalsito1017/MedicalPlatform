"""Bounded, allowlisted attachments for internal staff messages.

The bytes are stored in PostgreSQL with the message so a database backup is
also an attachment backup. Uploads are capped by configuration, restricted to
a small extension/content-type allowlist, and checked against simple magic
signatures. This is not antivirus scanning: downloaded files are always served
as attachments (never inline), with `nosniff`, and only to the message's sender
or recipient.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import UploadFile

from app.errors import ValidationFailed

_READ_CHUNK_BYTES = 64 * 1024
_MAX_FILENAME_LENGTH = 255
_ZIP_MAGIC = b"PK\x03\x04"

# Extension -> canonical content type. SVG/HTML and executables are absent on
# purpose: they must never be rendered inline or treated as active content.
ALLOWED_ATTACHMENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_TEXT_EXTENSIONS = frozenset({".txt", ".csv"})
_ZIP_EXTENSIONS = frozenset({".docx", ".xlsx"})
_ATTACHMENT_SUMMARY = "PDF, PNG, JPEG, WebP, TXT, CSV, DOCX or XLSX"


@dataclass(frozen=True)
class PreparedAttachment:
    """Validated attachment bytes and metadata ready for persistence."""

    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    data: bytes


def sanitize_filename(raw: str | None) -> tuple[str, str]:
    """Return a safe basename and its lowercase extension.

    The stored name is only a display/download label: bytes live in the
    database, so there is no filesystem path to traverse. Path components are
    still stripped, control characters removed, and the length bounded.
    """
    candidate = str(raw or "attachment").replace("\\", "/")
    name = PurePosixPath(candidate).name
    name = "".join(char for char in name if char.isprintable() and char not in "\r\n")
    name = name.strip().strip(".")
    if not name:
        raise ValidationFailed("The attachment needs a file name.")

    extension = PurePosixPath(name).suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_TYPES:
        raise ValidationFailed(
            f"Unsupported attachment type. Allowed types: {_ATTACHMENT_SUMMARY}."
        )

    stem = name[: -len(extension)] or "attachment"
    max_stem = _MAX_FILENAME_LENGTH - len(extension)
    name = f"{stem[:max_stem]}{extension}"
    return name, extension


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def _validate_signature(extension: str, data: bytes) -> None:
    if extension == ".pdf":
        valid = b"%PDF-" in data[:1024]
    elif extension == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension in {".jpg", ".jpeg"}:
        valid = data.startswith(b"\xff\xd8\xff")
    elif extension == ".webp":
        valid = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    elif extension in _ZIP_EXTENSIONS:
        valid = data.startswith(_ZIP_MAGIC)
    elif extension in _TEXT_EXTENSIONS:
        valid = _looks_like_text(data)
    else:  # Defensive: sanitize_filename already rejected unknown extensions.
        valid = False
    if not valid:
        raise ValidationFailed(
            "The attachment content does not match its file type."
        )


async def prepare_attachment(
    upload: UploadFile, *, max_bytes: int
) -> PreparedAttachment:
    """Read one bounded upload and validate its declared type."""
    filename, extension = sanitize_filename(upload.filename)
    data = bytearray()
    try:
        while True:
            chunk = await upload.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValidationFailed(
                    f"The attachment exceeds the maximum size of {max_bytes} bytes."
                )
    finally:
        await upload.close()

    if not data:
        raise ValidationFailed("The attachment is empty.")
    _validate_signature(extension, bytes(data))

    payload = bytes(data)
    return PreparedAttachment(
        filename=filename,
        content_type=ALLOWED_ATTACHMENT_TYPES[extension],
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        data=payload,
    )


def attachment_content_disposition(filename: str) -> str:
    """RFC 6266 attachment header with an ASCII fallback plus UTF-8 name."""
    fallback = (
        filename.encode("ascii", errors="ignore").decode("ascii").replace('"', "")
        or "attachment"
    )
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
