#!/usr/bin/env python3
"""Publish at most one due Ninja calendar item to Threads, then mark it Posted."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lark_ninja_cloudinary_media_test import (
    API_ROOT,
    CheckError,
    cloudinary_post,
    cloudinary_signature,
    compress_image_for_cloudinary,
    display_text,
    download_attachment,
    first_image_attachment,
    get_items,
    posting_datetime,
    request_json,
    required,
    safe_api_message,
    tenant_token,
)

THREADS_ROOT = "https://graph.threads.net/v1.0"
LOCAL_TZ_NAME = "Asia/Kuala_Lumpur"
STATE_PATH = Path("state/ninja-published.json")
MAX_POST_CHARS = 500


def threads_json(method: str, url: str, *, token: str, params: dict[str, str] | None = None,
                 operation: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    body = None
    if params is not None:
        body = urllib.parse.urlencode(params).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            error = {}
        code = error.get("code", "unknown")
        trace = error.get("fbtrace_id")
        trace_text = f"; trace ID {trace}" if isinstance(trace, str) and trace else ""
        # Avoid echoing an API message that might contain submitted post text.
        raise CheckError(f"{operation} failed (HTTP {exc.code}, Threads code {code}{trace_text}).") from None
    except urllib.error.URLError:
        raise CheckError(f"{operation} could not reach Threads.") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError(f"{operation}: Threads returned an unexpected JSON response.") from None
    if not isinstance(result, dict):
        raise CheckError(f"{operation}: Threads returned an unexpected response format.")
    if "error" in result:
        error = result.get("error")
        error = error if isinstance(error, dict) else {}
        code = error.get("code", "unknown")
        trace = error.get("fbtrace_id")
        trace_text = f"; trace ID {trace}" if isinstance(trace, str) and trace else ""
        raise CheckError(f"{operation} failed (Threads code {code}{trace_text}).")
    return result


def choose_record(records: list[dict], *, publish: bool, now: datetime) -> tuple[dict, dict | None, str]:
    candidates: list[tuple[datetime, dict, dict | None, str]] = []
    for record in records:
        fields = record.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        if display_text(fields.get("Status")).strip().casefold() != "scheduled":
            continue
        scheduled_at = posting_datetime(fields.get("Posting Date"))
        if scheduled_at is None:
            continue
        if publish:
            if scheduled_at.date() != now.date() or scheduled_at > now:
                continue
        elif scheduled_at.date() < now.date():
            continue
        text = display_text(fields.get("Copywriting")).strip()
        if not text:
            continue
        graphic = first_image_attachment(fields.get("Graphic"))
        candidates.append((scheduled_at, record, graphic, text))
    if not candidates:
        raise CheckError("No due Scheduled Ninja post with copywriting was found.")
    candidates.sort(key=lambda item: item[0])
    if publish and len(candidates) > 1:
        raise CheckError("More than one Ninja post is due today; refusing to choose one automatically.")
    _, record, graphic, text = candidates[0]
    return record, graphic, text


def read_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        raise CheckError("The saved publishing safety state is unreadable; stop for manual review.") from None
    if not isinstance(data, dict):
        raise CheckError("The saved publishing safety state has an unexpected format.")
    return data


def save_state(state: dict, message: str) -> None:
    if os.environ.get("GITHUB_REF_NAME") != "main":
        raise CheckError("Live publishing is allowed only from the main branch.")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for command in (
        ["git", "config", "user.name", "github-actions[bot]"],
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        ["git", "add", str(STATE_PATH)],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if diff.returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True, text=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True, capture_output=True, text=True)


def lark_update_status(*, app_token: str, table_id: str, record_id: str, status: str, token: str) -> None:
    url = (
        f"{API_ROOT}/bitable/v1/apps/{urllib.parse.quote(app_token, safe='')}"
        f"/tables/{urllib.parse.quote(table_id, safe='')}/records/{urllib.parse.quote(record_id, safe='')}"
    )
    request_json(
        "PUT", url, token=token, payload={"fields": {"Status": status}},
        operation=f"set Ninja record status to {status}",
    )


def prepare_image(*, token: str, base_token: str, table_id: str, record: dict,
                  attachment: dict, cloud_name: str, api_key: str, api_secret: str) -> str:
    record_id = record.get("record_id")
    file_token = attachment.get("file_token") or attachment.get("fileToken")
    if not isinstance(record_id, str) or not isinstance(file_token, str):
        raise CheckError("Selected Ninja record is missing its internal image identifiers.")
    image, mime = download_attachment(
        token=token, base_token=base_token, table_id=table_id,
        record_id=record_id, file_token=file_token,
    )
    image, mime, extension, compressed = compress_image_for_cloudinary(image, mime)
    if compressed:
        print(f"Compressed the scheduled image to {len(image)} bytes for delivery.")
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    public_id = f"ninja-{hashlib.sha256(record_id.encode()).hexdigest()[:18]}-{run_id}"
    timestamp = str(int(time.time()))
    upload_params = {
        "folder": "ninja-threads",
        "overwrite": "true",
        "public_id": public_id,
        "timestamp": timestamp,
    }
    upload_fields = {
        **upload_params,
        "api_key": api_key,
        "signature": cloudinary_signature(upload_params, api_secret),
    }
    uploaded = cloudinary_post(
        f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
        upload_fields,
        image,
        image_mime_type=mime,
        image_filename=f"ninja-graphic{extension}",
        operation="Cloudinary image upload",
    )
    image_url = uploaded.get("secure_url")
    if not isinstance(image_url, str) or not image_url.startswith("https://"):
        raise CheckError("Cloudinary did not return a public HTTPS image URL.")
    return image_url


def wait_for_container(container_id: str, token: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = threads_json(
            "GET",
            f"{THREADS_ROOT}/{urllib.parse.quote(container_id, safe='')}?fields=status,status_code",
            token=token,
            operation="check Threads media container",
        )
        status = str(result.get("status") or result.get("status_code") or "").upper()
        if status in {"FINISHED", "PUBLISHED"}:
            return
        if status in {"ERROR", "EXPIRED"}:
            raise CheckError("Threads could not process the image; the post was not published.")
        time.sleep(3)
    raise CheckError("Threads image processing timed out; no publish retry was attempted.")


def publish_one(*, record: dict, text: str, graphic: dict | None, threads_token: str,
                lark_token: str, base_token: str, table_id: str, cloud_name: str,
                cloud_api_key: str, cloud_api_secret: str, now: datetime) -> str:
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise CheckError("Selected Ninja record has no record ID.")
    state = read_state()
    if record_id in state:
        previous = state[record_id]
        prior_state = previous.get("state", "unknown") if isinstance(previous, dict) else "unknown"
        raise CheckError(
            f"This record already has a saved publishing attempt ({prior_state}); skipping to prevent a duplicate."
        )

    # Check Base edit permission before sending a post. The field remains Scheduled.
    lark_update_status(
        app_token=base_token, table_id=table_id, record_id=record_id,
        status="Scheduled", token=lark_token,
    )

    image_url = None
    if graphic:
        image_url = prepare_image(
            token=lark_token, base_token=base_token, table_id=table_id,
            record=record, attachment=graphic, cloud_name=cloud_name,
            api_key=cloud_api_key, api_secret=cloud_api_secret,
        )

    state[record_id] = {
        "state": "reserved",
        "reserved_at": now.isoformat(),
        "scheduled_for": now.date().isoformat(),
    }
    save_state(state, f"Reserve Ninja Threads record {record_id}")

    params = {"media_type": "IMAGE" if image_url else "TEXT", "text": text}
    if image_url:
        params["image_url"] = image_url
    container = threads_json(
        "POST", f"{THREADS_ROOT}/me/threads", token=threads_token,
        params=params, operation="create Threads post container",
    )
    container_id = container.get("id")
    if not isinstance(container_id, str) or not container_id:
        raise CheckError("Threads returned no media container ID; the record is reserved for review.")
    if image_url:
        wait_for_container(container_id, threads_token)

    published = threads_json(
        "POST", f"{THREADS_ROOT}/me/threads_publish", token=threads_token,
        params={"creation_id": container_id}, operation="publish Threads post",
    )
    post_id = published.get("id")
    if not isinstance(post_id, str) or not post_id:
        raise CheckError("Threads returned no post ID; the record is reserved for review.")

    # If the Lark write or final state save fails, the reservation blocks automatic retries.
    lark_update_status(
        app_token=base_token, table_id=table_id, record_id=record_id,
        status="Posted", token=lark_token,
    )
    state[record_id] = {
        "state": "published",
        "reserved_at": now.isoformat(),
        "published_at": datetime.now(ZoneInfo(LOCAL_TZ_NAME)).isoformat(),
        "threads_post_id": post_id,
    }
    save_state(state, f"Record Ninja Threads post {post_id}")
    return post_id


def main() -> int:
    try:
        now = datetime.now(ZoneInfo(LOCAL_TZ_NAME))
        mode = os.environ.get("PUBLISH_MODE", "dry-run").strip().lower()
        publish = mode == "publish"
        if mode not in {"dry-run", "publish"}:
            raise CheckError("PUBLISH_MODE must be dry-run or publish.")

        app_id = required("LARK_APP_ID")
        app_secret = required("LARK_APP_SECRET")
        base_token = required("LARK_BASE_TOKEN")
        table_id = required("LARK_TABLE_ID")
        view_id = required("LARK_VIEW_ID")
        threads_token = required("THREADS_ACCESS_TOKEN")
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
        cloud_api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
        cloud_api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

        lark_token = tenant_token(app_id, app_secret)
        records = get_items(
            "records", token=lark_token, base_token=base_token,
            table_id=table_id, view_id=view_id,
        )
        record, graphic, text = choose_record(records, publish=publish, now=now)
        record_id = record.get("record_id")
        if not isinstance(record_id, str):
            raise CheckError("Selected Ninja record has no record ID.")
        if len(text) > MAX_POST_CHARS:
            raise CheckError("Copywriting exceeds Threads' 500-character post limit.")

        account = threads_json(
            "GET", f"{THREADS_ROOT}/me?fields=id,username",
            token=threads_token, operation="verify Threads account access",
        )
        if not account.get("id"):
            raise CheckError("Threads did not confirm the connected account.")

        if not publish:
            print(
                f"Dry-run passed for one Scheduled Ninja record (ID {record_id}); "
                f"copy is {len(text)} characters; image {'present' if graphic else 'not present'}; "
                "no post or Lark write was made."
            )
            return 0

        if graphic and not all((cloud_name, cloud_api_key, cloud_api_secret)):
            raise CheckError("Cloudinary credentials are required for the selected image.")
        post_id = publish_one(
            record=record, text=text, graphic=graphic, threads_token=threads_token,
            lark_token=lark_token, base_token=base_token, table_id=table_id,
            cloud_name=cloud_name, cloud_api_key=cloud_api_key,
            cloud_api_secret=cloud_api_secret, now=now,
        )
        print(f"Published one Ninja Threads post (post ID {post_id}) and set its Lark status to Posted.")
        return 0
    except CheckError as exc:
        print(f"Ninja Threads workflow stopped safely: {safe_api_message(exc)}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print(
            "Ninja Threads workflow stopped safely: could not save the duplicate-prevention state; "
            "no publish was started.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
