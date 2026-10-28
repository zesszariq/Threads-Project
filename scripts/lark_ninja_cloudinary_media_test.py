#!/usr/bin/env python3
"""Upload one upcoming scheduled Ninja image to Cloudinary, verify it, then delete it.

This manual test never prints post copy or media URLs, publishes to Threads, or
updates a Lark record. It chooses the earliest upcoming Scheduled row in the
configured Ninja view that has an image attachment.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

API_ROOT = "https://open.larksuite.com/open-apis"
MAX_PAGES = 20
MAX_MEDIA_BYTES = 25 * 1024 * 1024
CLOUDINARY_MAX_BYTES = 9_500_000
LOCAL_ZONE = ZoneInfo("Asia/Kuala_Lumpur")


class CheckError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CheckError(f"Missing GitHub Actions secret/variable: {name}")
    return value


def safe_api_message(value: object) -> str:
    message = str(value or "no diagnostic message")
    message = re.sub(r"https?://\S+", "[URL]", message)
    message = re.sub(r"(?i)(token|secret|authorization)\s*[:=]\s*[^,; ]+", r"\1=[redacted]", message)
    message = re.sub(r"\b[A-Za-z0-9_-]{48,}\b", "[redacted]", message)
    return message[:180]


def request_json(method: str, url: str, *, token: str | None = None,
                 payload: dict | None = None, operation: str = "Lark request") -> dict:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error = {}
        if not isinstance(error, dict):
            error = {}
        api_code = error.get("code", "unknown")
        message = safe_api_message(error.get("msg", "no diagnostic message"))
        error_data = error.get("error") if isinstance(error.get("error"), dict) else {}
        request_id = error_data.get("log_id") or error.get("log_id")
        suffix = f"; request ID {request_id}" if isinstance(request_id, str) and request_id else ""
        raise CheckError(
            f"{operation} failed (HTTP {exc.code}, Lark code {api_code}{suffix}): {message}"
        ) from None
    except urllib.error.URLError:
        raise CheckError(f"{operation} could not reach Lark.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError(f"{operation}: Lark returned an unexpected JSON response.") from None
    if not isinstance(result, dict):
        raise CheckError(f"{operation}: Lark returned an unexpected response format.")
    if result.get("code", 0) != 0:
        code = result.get("code", "unknown")
        message = safe_api_message(result.get("msg", "request failed"))
        error_data = result.get("error") if isinstance(result.get("error"), dict) else {}
        request_id = error_data.get("log_id") or result.get("log_id")
        suffix = f"; request ID {request_id}" if isinstance(request_id, str) and request_id else ""
        raise CheckError(f"{operation} failed (Lark code {code}{suffix}): {message}")
    return result


def tenant_token(app_id: str, app_secret: str) -> str:
    result = request_json(
        "POST",
        f"{API_ROOT}/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
        operation="create app access token",
    )
    token = (result.get("data") or {}).get("tenant_access_token") or result.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise CheckError("Lark did not return an app access token.")
    return token


def get_items(path: str, *, token: str, base_token: str, table_id: str,
              view_id: str | None = None) -> list[dict]:
    items: list[dict] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, str] = {"page_size": "100"}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id
        url = (
            f"{API_ROOT}/bitable/v1/apps/{urllib.parse.quote(base_token, safe='')}"
            f"/tables/{urllib.parse.quote(table_id, safe='')}/{path}?"
            f"{urllib.parse.urlencode(params)}"
        )
        result = request_json("GET", url, token=token, operation="read Base records")
        data = result.get("data") or {}
        batch = data.get("items") or []
        if not isinstance(batch, list):
            raise CheckError(f"Lark returned an unexpected {path} result.")
        items.extend(item for item in batch if isinstance(item, dict))
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token")
        if not page_token:
            raise CheckError("Lark indicated another page but returned no page token.")
    raise CheckError(f"Stopped after {MAX_PAGES} pages to keep the test bounded.")


def display_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    if isinstance(value, list):
        return " ".join(display_text(item) for item in value)
    return ""


def posting_datetime(value: object) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("timestamp", value.get("value"))
    try:
        if isinstance(value, (int, float)):
            # Lark Base date values are Unix milliseconds.
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).astimezone(LOCAL_ZONE)
        if isinstance(value, str):
            normalized = value.strip().replace("/", "-")
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(normalized, fmt)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=LOCAL_ZONE)
                    return parsed.astimezone(LOCAL_ZONE)
                except ValueError:
                    continue
    except (OverflowError, OSError, ValueError):
        return None
    return None


def is_image(item: dict) -> bool:
    mime = str(item.get("type") or item.get("mime_type") or item.get("mimeType") or "").lower()
    name = str(item.get("name") or item.get("file_name") or "").lower()
    return mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


def first_image_attachment(value: object) -> dict | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and is_image(item):
            file_token = item.get("file_token") or item.get("fileToken")
            if isinstance(file_token, str) and file_token:
                return item
    return None


def select_record(records: list[dict]) -> tuple[dict, dict]:
    today = datetime.now(LOCAL_ZONE).date()
    candidates: list[tuple[datetime, dict, dict]] = []
    for record in records:
        fields = record.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        if display_text(fields.get("Status")).strip().casefold() != "scheduled":
            continue
        scheduled_at = posting_datetime(fields.get("Posting Date"))
        if scheduled_at is None or scheduled_at.date() < today:
            continue
        graphic = first_image_attachment(fields.get("Graphic"))
        if graphic is None:
            continue
        candidates.append((scheduled_at, record, graphic))
    if not candidates:
        raise CheckError("No upcoming Ninja record is both Scheduled and has an image in Graphic.")
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], candidates[0][2]


def download_attachment(*, token: str, base_token: str, table_id: str,
                        record_id: str, file_token: str) -> tuple[bytes, str]:
    metadata_url = (
        f"{API_ROOT}/base/v3/bases/{urllib.parse.quote(base_token, safe='')}"
        f"/tables/{urllib.parse.quote(table_id, safe='')}/get_attachments"
    )
    metadata = request_json("POST", metadata_url, token=token,
                            payload={"record_id_list": [record_id]},
                            operation="read Base attachment metadata")
    attachment_root = (metadata.get("data") or {}).get("attachments") or {}
    record_data = attachment_root.get(record_id) or {}
    selected: dict | None = None
    if isinstance(record_data, dict):
        for field_items in record_data.values():
            if not isinstance(field_items, list):
                continue
            for item in field_items:
                if isinstance(item, dict) and item.get("file_token") == file_token:
                    selected = item
                    break
            if selected:
                break
    if not selected:
        raise CheckError("Lark returned no attachment metadata for the selected image.")
    extra = selected.get("extra_info")
    query = {"extra": extra} if isinstance(extra, str) and extra else {}
    media_url = (
        f"{API_ROOT}/drive/v1/medias/{urllib.parse.quote(file_token, safe='')}/download"
    )
    if query:
        media_url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(media_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            content_type = response.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
            content = response.read(MAX_MEDIA_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CheckError(f"Lark media download returned HTTP {exc.code}. Check docs:document.media:download scope.") from None
    except urllib.error.URLError:
        raise CheckError("Could not download the Lark attachment.") from None
    if len(content) > MAX_MEDIA_BYTES:
        raise CheckError("The selected image exceeds the 25 MiB smoke-test limit.")
    if not content or not content_type.startswith("image/"):
        raise CheckError("The selected Lark attachment did not download as an image.")
    return content, content_type



def compress_image_for_cloudinary(content: bytes, mime_type: str) -> tuple[bytes, str, str, bool]:
    if len(content) <= CLOUDINARY_MAX_BYTES:
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime_type)
        if not extension:
            raise CheckError(f"Cloudinary test does not support oversized {mime_type} images.")
        return content, mime_type, extension, False

    try:
        from PIL import Image, ImageOps
        source = Image.open(io.BytesIO(content))
        source.load()
        image_format = (source.format or "").upper()
        source = ImageOps.exif_transpose(source)
    except Exception:
        raise CheckError("The oversized Lark image could not be decoded for compression.") from None
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        raise CheckError(f"Cannot safely compress oversized {mime_type} media for this image test.")

    def resized_copy(max_edge: int):
        result = source.copy()
        if max(result.size) > max_edge:
            ratio = max_edge / max(result.size)
            size = (max(1, round(result.width * ratio)), max(1, round(result.height * ratio)))
            result = result.resize(size, Image.Resampling.LANCZOS)
        return result

    if image_format == "PNG":
        for max_edge in (max(source.size), 3200, 2800, 2400, 2000):
            candidate = resized_copy(max_edge)
            output = io.BytesIO()
            candidate.save(output, format="PNG", optimize=True)
            data = output.getvalue()
            if len(data) <= CLOUDINARY_MAX_BYTES:
                return data, "image/png", ".png", True
        # Palette reduction preserves transparency and usually shrinks large flat-color graphics.
        candidate = resized_copy(2000)
        if candidate.mode not in {"1", "L", "P"}:
            candidate = candidate.convert("RGBA" if "A" in candidate.getbands() else "RGB")
        try:
            palette = candidate.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            output = io.BytesIO()
            palette.save(output, format="PNG", optimize=True)
            data = output.getvalue()
            if len(data) <= CLOUDINARY_MAX_BYTES:
                return data, "image/png", ".png", True
        except Exception:
            pass
        raise CheckError("Image compression could not bring this PNG below Cloudinary's 10 MB limit.")

    output_mime = "image/webp" if image_format == "WEBP" else "image/jpeg"
    output_ext = ".webp" if image_format == "WEBP" else ".jpg"
    for max_edge in (max(source.size), 3200, 2800, 2400, 2000, 1600):
        candidate = resized_copy(max_edge)
        if output_mime == "image/jpeg":
            if candidate.mode != "RGB":
                if "A" in candidate.getbands():
                    background = Image.new("RGB", candidate.size, "white")
                    alpha = candidate.getchannel("A")
                    background.paste(candidate.convert("RGB"), mask=alpha)
                    candidate = background
                else:
                    candidate = candidate.convert("RGB")
        for quality in (90, 85, 80, 75, 70, 65):
            output = io.BytesIO()
            if output_mime == "image/jpeg":
                candidate.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            else:
                candidate.save(output, format="WEBP", quality=quality, method=6)
            data = output.getvalue()
            if len(data) <= CLOUDINARY_MAX_BYTES:
                return data, output_mime, output_ext, True
    raise CheckError("Image compression could not bring this image below Cloudinary's 10 MB limit.")

def cloudinary_signature(params: dict[str, str], api_secret: str) -> str:
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha1(f"{canonical}{api_secret}".encode("utf-8")).hexdigest()


def cloudinary_post(url: str, fields: dict[str, str], image: bytes | None = None,
                   image_mime_type: str = "application/octet-stream",
                   image_filename: str = "ninja-graphic-test",
                   operation: str = "Cloudinary request") -> dict:
    boundary = "----NinjaCloudinaryMediaTestBoundary"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    if image is not None:
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{image_filename}"\r\n'.encode(),
            f"Content-Type: {image_mime_type}\r\n\r\n".encode(),
            image,
            b"\r\n",
        ])
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url, data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_body = {}
        error_data = error_body.get("error") if isinstance(error_body, dict) else {}
        if not isinstance(error_data, dict):
            error_data = {}
        message = safe_api_message(error_data.get("message", "no diagnostic message"))
        raise CheckError(f"{operation} failed (HTTP {exc.code}): {message}") from None
    except urllib.error.URLError:
        raise CheckError(f"{operation} could not reach Cloudinary.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError("Cloudinary returned an unexpected response.") from None
    if not isinstance(result, dict):
        raise CheckError("Cloudinary returned an unexpected response format.")
    return result


def main() -> int:
    uploaded_public_id: str | None = None
    resource_type = "image"
    cloud_name = api_key = api_secret = ""
    primary_error: str | None = None
    try:
        app_id = required("LARK_APP_ID")
        app_secret = required("LARK_APP_SECRET")
        base_token = required("LARK_BASE_TOKEN")
        table_id = required("LARK_TABLE_ID")
        view_id = required("LARK_VIEW_ID")
        cloud_name = required("CLOUDINARY_CLOUD_NAME")
        api_key = required("CLOUDINARY_API_KEY")
        api_secret = required("CLOUDINARY_API_SECRET")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cloud_name):
            raise CheckError("CLOUDINARY_CLOUD_NAME has an unexpected format.")

        lark_token = tenant_token(app_id, app_secret)
        records = get_items("records", token=lark_token, base_token=base_token,
                            table_id=table_id, view_id=view_id)
        record, attachment = select_record(records)
        record_id = record.get("record_id")
        file_token = attachment.get("file_token") or attachment.get("fileToken")
        if not isinstance(record_id, str) or not isinstance(file_token, str):
            raise CheckError("Selected Lark record is missing its internal attachment identifiers.")

        media, mime_type = download_attachment(
            token=lark_token, base_token=base_token, table_id=table_id,
            record_id=record_id, file_token=file_token,
        )
        media, upload_mime_type, extension, compressed = compress_image_for_cloudinary(media, mime_type)
        if compressed:
            print(f"Compressed the selected image to {len(media)} bytes before uploading to Cloudinary.")
        run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        public_id = f"ninja-media-smoke-{run_id}-{attempt}"
        folder = "ninja-media-smoke"
        timestamp = str(int(time.time()))
        upload_params = {"folder": folder, "public_id": public_id, "timestamp": timestamp}
        upload_fields = {
            **upload_params,
            "api_key": api_key,
            "signature": cloudinary_signature(upload_params, api_secret),
        }
        uploaded = cloudinary_post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/{resource_type}/upload",
            upload_fields,
            media,
            image_mime_type=upload_mime_type,
            image_filename=f"ninja-graphic-test{extension}",
            operation="Cloudinary image upload",
        )
        uploaded_public_id = uploaded.get("public_id")
        secure_url = uploaded.get("secure_url")
        if not isinstance(uploaded_public_id, str) or not uploaded_public_id:
            raise CheckError("Cloudinary upload response did not include an asset identifier.")
        if not isinstance(secure_url, str) or not secure_url.startswith("https://"):
            raise CheckError("Cloudinary did not return an HTTPS delivery URL.")

        fetch = urllib.request.Request(secure_url, headers={"Accept": "image/*"}, method="GET")
        try:
            with urllib.request.urlopen(fetch, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status != 200 or not content_type.startswith("image/"):
                    raise CheckError("Cloudinary's public URL did not return an image.")
                response.read(1024)
        except urllib.error.HTTPError as exc:
            raise CheckError(f"Cloudinary public URL returned HTTP {exc.code}.") from None
        except urllib.error.URLError:
            raise CheckError("Could not fetch Cloudinary's public image URL.") from None

        print("One upcoming Scheduled Ninja image was fetched from Lark and served from a public Cloudinary URL.")
    except CheckError as exc:
        primary_error = str(exc)

    cleanup_error: str | None = None
    if uploaded_public_id and cloud_name and api_key and api_secret:
        timestamp = str(int(time.time()))
        destroy_params = {"invalidate": "true", "public_id": uploaded_public_id,
                          "timestamp": timestamp}
        destroy_fields = {
            **destroy_params,
            "api_key": api_key,
            "signature": cloudinary_signature(destroy_params, api_secret),
        }
        try:
            deleted = cloudinary_post(
                f"https://api.cloudinary.com/v1_1/{cloud_name}/{resource_type}/destroy",
                destroy_fields,
                operation="temporary Cloudinary image cleanup",
            )
            if deleted.get("result") not in {"ok", "not found"}:
                cleanup_error = "Cloudinary did not confirm deletion of the temporary public test image."
            else:
                print("Temporary Cloudinary copy of the Lark image was deleted.")
        except CheckError as exc:
            cleanup_error = f"Temporary public test image cleanup failed: {exc}"

    if primary_error:
        print(f"Media URL test failed: {primary_error}", file=sys.stderr)
        if cleanup_error:
            print(cleanup_error, file=sys.stderr)
        return 1
    if cleanup_error:
        print(cleanup_error, file=sys.stderr)
        return 1
    print("Threads publishing and Lark write-back were not attempted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
