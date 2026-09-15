"""Serve call recordings from S3-compatible object storage (MinIO on the VPS, R2/S3 in the cloud).

The worker's egress writes `recordings/<tenant>/<date>/<call>/<leg>.ogg`; this module fetches those
objects with a SigV4-signed GET so the dashboard can stream/download them through the API (which
enforces tenant auth). Signing is done by hand to avoid pulling boto into the API image.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_get(
    url: str,
    *,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """AWS SigV4 headers for an S3 GET of `url` (path-style, unsigned payload)."""
    parts = urlsplit(url)
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    day = ts[:8]
    headers = {
        "host": parts.netloc,
        "x-amz-content-sha256": _EMPTY_SHA256,
        "x-amz-date": ts,
        **{k.lower(): v for k, v in (extra_headers or {}).items()},
    }
    signed = ";".join(sorted(headers))
    canonical = "\n".join(
        [
            "GET",
            quote(parts.path or "/", safe="/"),
            parts.query,
            "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers)),
            signed,
            _EMPTY_SHA256,
        ]
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", ts, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    k = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    )
    return headers


CONTENT_TYPES = {
    ".ogg": "audio/ogg",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


class RecordingStorage:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._ak, self._sk = access_key, secret_key
        self._region = "us-east-1" if region == "auto" else region
        self._client = client or httpx.AsyncClient(timeout=60)

    def url(self, key: str) -> str:
        return f"{self._endpoint}/{self._bucket}/{quote(key, safe='/')}"

    async def fetch(self, key: str, byte_range: str | None = None) -> httpx.Response:
        """GET the object; `byte_range` is forwarded so `<audio>` seeking works."""
        url = self.url(key)
        extra = {"range": byte_range} if byte_range else None
        headers = sign_get(
            url, access_key=self._ak, secret_key=self._sk, region=self._region, extra_headers=extra
        )
        return await self._client.get(url, headers=headers)


def content_type_for(key: str) -> str:
    for ext, ct in CONTENT_TYPES.items():
        if key.endswith(ext):
            return ct
    return "application/octet-stream"
