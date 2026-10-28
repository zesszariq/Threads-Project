#!/usr/bin/env python3
"""Upload, publicly fetch, and delete a tiny synthetic Cloudinary test image."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


# A 1x1 synthetic PNG; no Lark or marketing media is used.
TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGysAAAAASUVORK5CYII="
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing GitHub Actions secret/variable: {name}")
    return value


def sign(params: dict[str, str], api_secret: str) -> str:
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha1(f"{canonical}{api_secret}".encode("utf-8")).hexdigest()


def multipart_post(url: str, fields: dict[str, str], file_bytes: bytes | None = None) -> dict:
    boundary = "----CodexCloudinarySmokeBoundary"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    if file_bytes is not None:
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="codex-smoke.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            file_bytes,
            b"\r\n",
        ])
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Cloudinary returned HTTP {exc.code}.") from None
    except urllib.error.URLError:
        raise RuntimeError("Could not reach Cloudinary's API.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Cloudinary returned an unexpected response.") from None
    if not isinstance(payload, dict):
        raise RuntimeError("Cloudinary returned an unexpected response format.")
    return payload


def main() -> int:
    uploaded_public_id: str | None = None
    cloud_name = ""
    api_key = ""
    api_secret = ""
    primary_error: str | None = None

    try:
        cloud_name = required("CLOUDINARY_CLOUD_NAME")
        api_key = required("CLOUDINARY_API_KEY")
        api_secret = required("CLOUDINARY_API_SECRET")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cloud_name):
            raise RuntimeError("CLOUDINARY_CLOUD_NAME has an unexpected format.")

        run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        public_id = f"codex-smoke-{run_id}-{attempt}"
        folder = "codex-smoke"
        timestamp = str(int(time.time()))
        upload_params = {"folder": folder, "public_id": public_id, "timestamp": timestamp}
        upload_fields = {
            **upload_params,
            "api_key": api_key,
            "signature": sign(upload_params, api_secret),
        }
        result = multipart_post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            upload_fields,
            TEST_PNG,
        )
        uploaded_public_id = result.get("public_id")
        secure_url = result.get("secure_url")
        if not isinstance(uploaded_public_id, str) or not uploaded_public_id:
            raise RuntimeError("Cloudinary upload response did not include an asset identifier.")
        if not isinstance(secure_url, str) or not secure_url.startswith("https://"):
            raise RuntimeError("Cloudinary did not return an HTTPS delivery URL.")

        request = urllib.request.Request(secure_url, headers={"Accept": "image/*"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status != 200 or not content_type.startswith("image/"):
                    raise RuntimeError("The public delivery URL did not return an image.")
                response.read(1024)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"The public delivery URL returned HTTP {exc.code}.") from None
        except urllib.error.URLError:
            raise RuntimeError("Could not fetch the public delivery URL.") from None

        print("Temporary Cloudinary upload succeeded; public HTTPS image URL is fetchable.")
    except RuntimeError as exc:
        primary_error = str(exc)

    cleanup_error: str | None = None
    if uploaded_public_id and api_key and api_secret and cloud_name:
        timestamp = str(int(time.time()))
        destroy_params = {
            "invalidate": "true",
            "public_id": uploaded_public_id,
            "timestamp": timestamp,
        }
        destroy_fields = {
            **destroy_params,
            "api_key": api_key,
            "signature": sign(destroy_params, api_secret),
        }
        try:
            destroyed = multipart_post(
                f"https://api.cloudinary.com/v1_1/{cloud_name}/image/destroy",
                destroy_fields,
            )
            if destroyed.get("result") not in {"ok", "not found"}:
                cleanup_error = "Cloudinary did not confirm deletion of the temporary test image."
            else:
                print("Temporary test image was deleted.")
        except RuntimeError as exc:
            cleanup_error = f"Temporary test image cleanup failed: {exc}"

    if primary_error:
        print(f"Cloudinary upload test failed: {primary_error}", file=sys.stderr)
        if cleanup_error:
            print(cleanup_error, file=sys.stderr)
        return 1
    if cleanup_error:
        print(cleanup_error, file=sys.stderr)
        return 1

    print("No Lark media, Threads post, or Lark record was used or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
