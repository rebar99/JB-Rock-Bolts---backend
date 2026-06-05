import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _client():
    from app.config import settings
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def upload_file(file_bytes: bytes, filename: str, content_type: str = "application/octet-stream") -> str:
    """Upload bytes to R2 and return the public URL."""
    from app.config import settings
    _client().put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=filename,
        Body=file_bytes,
        ContentType=content_type,
    )
    return f"{settings.R2_PUBLIC_URL.rstrip('/')}/{filename}"


def delete_file(filename: str) -> None:
    """Delete a file from R2 by key. Logs a warning on failure instead of raising."""
    from app.config import settings
    try:
        _client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=filename)
    except ClientError as e:
        logger.warning(f"Could not delete R2 object '{filename}': {e}")


def save_upload(file_bytes: bytes, filename: str, content_type: str = "application/octet-stream") -> str:
    """
    Unified upload helper.
    - R2 when credentials are configured (production).
    - Local filesystem fallback for development.
    """
    from app.config import settings
    if settings.r2_enabled:
        return upload_file(file_bytes, filename, content_type)

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, filename)
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    return f"/uploads/{filename}"
