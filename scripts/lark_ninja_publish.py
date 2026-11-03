#!/usr/bin/env python3
"""Publish at most one due calendar item for a selected brand to Threads."""
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
from datetime import datetime, timedelta
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
BRAND_NAME = os.environ.get("PUBLISH_BRAND", "Ninja").strip() or "Ninja"
BRAND_SLUG = {"Ninja": "ninja", "A4U": "a4u", "Pirate": "pirate"}.get(BRAND_NAME)
if BRAND_SLUG is None:
    raise ValueError("PUBLISH_BRAND must be Ninja, A4U, or Pirate.")
STATE_PATH = Path("state") / f"{BRAND_SLUG}-published.json"
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
        subcode = error.get("error_subcode")
        transient = error.get("is_transient")
        trace = error.get("fbtrace_id")
        details = [f"HTTP {exc.code}", f"Threads code {code}"]
        if subcode is not None:
            details.append(f"subcode {subcode}")
        if isinstance(transient, bool):
            details.append(f"transient {str(transient).lower()}")
        if isinstance(trace, str) and trace:
            details.append(f"trace ID {trace}")
        # Keep the diagnostic metadata but never echo a message that might include post text.
        raise CheckError(f"{operation} failed ({'; '.join(details)}).") from None
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


def no_due_diagnostics(records: list[dict], *, now: datetime) -> str:
    buckets: dict[str, dict[str, int]] = {}
    for offset in (-1, 0, 1):
        day = (now.date() + timedelta(days=offset)).isoformat()
        buckets[day] = {"rows": 0, "scheduled": 0, "with_copy": 0, "future_time": 0}
    unparseable = 0
    for record in records:
        fields = record.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        scheduled_at = posting_datetime(fields.get("Posting Date"))
        if scheduled_at is None:
            unparseable += 1
            continue
        day = scheduled_at.date().isoformat()
        if day not in buckets:
            continue
        bucket = buckets[day]
        bucket["rows"] += 1
        status = display_text(fields.get("Status")).strip().casefold()
        if status == "scheduled":
            bucket["scheduled"] += 1
        if display_text(fields.get("Copywriting")).strip():
            bucket["with_copy"] += 1
        if scheduled_at > now:
            bucket["future_time"] += 1
    summary = "; ".join(
        f"{day}: rows={values['rows']}, Scheduled={values['scheduled']}, "
        f"with_copy={values['with_copy']}, future_time={values['future_time']}"
        for day, values in buckets.items()
    )
    return (
        f"Checked {len(records)} Lark rows; parsed dates near today: {summary}; "
        f"unparseable dates={unparseable}."
    )


def choose_record(records: list[dict], *, publish: bool, now: datetime) -> tuple[dict, dict | None, str, str]:
    candidates: list[tuple[datetime, dict, dict | None, str, str]] = []
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
            # Posting Date is a date-only Lark field. Its API timestamp can resolve
            # later in the day, but the row is due on that calendar date.
            if scheduled_at.date() != now.date():
                continue
        elif scheduled_at.date() < now.date():
            continue
        text = display_text(fields.get("Copywriting")).strip()
        if not text:
            continue
        first_comment = display_text(fields.get("First/Second Comment")).strip()
        graphic = first_image_attachment(fields.get("Graphic"))
        candidates.append((scheduled_at, record, graphic, text, first_comment))
    if not candidates:
        detail = no_due_diagnostics(records, now=now)
        raise CheckError(f"No due Scheduled {BRAND_NAME} post with copywriting was found. {detail}")
    candidates.sort(key=lambda item: item[0])
    if publish and len(candidates) > 1:
        raise CheckError(f"More than one {BRAND_NAME} post is due today; refusing to choose one automatically.")
    _, record, graphic, text, first_comment = candidates[0]
    return record, graphic, text, first_comment


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
        operation=f"set {BRAND_NAME} record status to {status}",
    )


def prepare_image(*, token: str, base_token: str, table_id: str, record: dict,
                  attachment: dict, cloud_name: str, api_key: str, api_secret: str) -> str:
    record_id = record.get("record_id")
    file_token = attachment.get("file_token") or attachment.get("fileToken")
    if not isinstance(record_id, str) or not isinstance(file_token, str):
        raise CheckError(f"Selected {BRAND_NAME} record is missing its internal image identifiers.")
    image, mime = download_attachment(
        token=token, base_token=base_token, table_id=table_id,
        record_id=record_id, file_token=file_token,
    )
    image, mime, extension, compressed = compress_image_for_cloudinary(image, mime)
    if compressed:
        print(f"Compressed the scheduled image to {len(image)} bytes for delivery.")
    run_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
    public_id = f"{BRAND_SLUG}-{hashlib.sha256(record_id.encode()).hexdigest()[:18]}-{run_id}"
    timestamp = str(int(time.time()))
    upload_params = {
        "folder": f"{BRAND_SLUG}-threads",
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
        image_filename=f"{BRAND_SLUG}-graphic{extension}",
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
            f"{THREADS_ROOT}/{urllib.parse.quote(container_id, safe='')}?fields=status",
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


def publish_one(*, record: dict, text: str, first_comment: str, graphic: dict | None,
                threads_token: str, lark_token: str, base_token: str, table_id: str,
                cloud_name: str, cloud_api_key: str, cloud_api_secret: str, now: datetime) -> str:
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise CheckError(f"Selected {BRAND_NAME} record has no record ID.")
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
    save_state(state, f"Reserve {BRAND_NAME} Threads record {record_id}")

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
        try:
            wait_for_container(container_id, threads_token)
        except CheckError:
            # Status polling happens before threads_publish; this container was not posted.
            state = read_state()
            state.pop(record_id, None)
            save_state(state, f"Release pre-publish {BRAND_NAME} record {record_id}")
            raise

    publish_url = (
        f"{THREADS_ROOT}/me/threads_publish"
        f"?creation_id={urllib.parse.quote(container_id, safe='')}"
    )
    try:
        published = threads_json(
            "POST", publish_url, token=threads_token,
            operation="publish Threads post",
        )
    except CheckError as exc:
        # A definite 400/code 24 rejection means Meta did not publish this container.
        # Release the reservation so the next scheduled slot can create a fresh container.
        if "publish Threads post failed (HTTP 400, Threads code 24" in str(exc):
            state = read_state()
            state.pop(record_id, None)
            save_state(state, f"Release rejected {BRAND_NAME} Threads record {record_id}")
        raise
    post_id = published.get("id")
    if not isinstance(post_id, str) or not post_id:
        raise CheckError("Threads returned no post ID; the record is reserved for review.")

    # Persist the root ID first so a partial reply failure can never duplicate the post.
    state[record_id] = {
        "state": "root_published",
        "reserved_at": now.isoformat(),
        "published_at": datetime.now(ZoneInfo(LOCAL_TZ_NAME)).isoformat(),
        "threads_post_id": post_id,
    }
    save_state(state, f"Record {BRAND_NAME} Threads root post {post_id}")

    first_comment_id = None
    if first_comment:
        # Store the reply container before publishing. A later retry will stop safely
        # instead of risking a duplicate reply if the publish response was ambiguous.
        comment_container = threads_json(
            "POST",
            f"{THREADS_ROOT}/me/threads",
            token=threads_token,
            params={
                "media_type": "TEXT",
                "text": first_comment,
                "reply_to_id": post_id,
            },
            operation="create first-comment reply",
        )
        comment_container_id = comment_container.get("id")
        if not isinstance(comment_container_id, str) or not comment_container_id:
            raise CheckError("Threads returned no first-comment container ID; review before retrying.")
        state[record_id]["state"] = "first_comment_pending"
        state[record_id]["first_comment_container_id"] = comment_container_id
        save_state(state, f"Reserve {BRAND_NAME} Threads first comment for {post_id}")
        published_comment = threads_json(
            "POST",
            f"{THREADS_ROOT}/me/threads_publish"
            f"?creation_id={urllib.parse.quote(comment_container_id, safe='')}",
            token=threads_token,
            operation="publish first-comment reply",
        )
        first_comment_id = published_comment.get("id")
        if not isinstance(first_comment_id, str) or not first_comment_id:
            raise CheckError("Threads returned no first-comment ID; review before retrying.")
        state[record_id]["first_comment_id"] = first_comment_id
        state[record_id]["state"] = "first_comment_published"
        save_state(state, f"Record {BRAND_NAME} Threads first comment for {post_id}")

    # Mark Posted only after the root and its first comment are complete.
    lark_update_status(
        app_token=base_token, table_id=table_id, record_id=record_id,
        status="Posted", token=lark_token,
    )
    state[record_id] = {
        **state[record_id],
        "state": "published",
        "reserved_at": now.isoformat(),
        "published_at": datetime.now(ZoneInfo(LOCAL_TZ_NAME)).isoformat(),
        "threads_post_id": post_id,
        **({"first_comment_id": first_comment_id} if first_comment_id else {}),
    }
    save_state(state, f"Record {BRAND_NAME} Threads post {post_id}")
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
        expected_username = required("THREADS_EXPECTED_USERNAME").strip().lstrip("@").casefold()
        if publish and os.environ.get("GITHUB_REF_NAME") != "main":
            raise CheckError("Live publishing is allowed only from the main branch.")
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
        cloud_api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
        cloud_api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

        lark_token = tenant_token(app_id, app_secret)
        records = get_items(
            "records", token=lark_token, base_token=base_token,
            table_id=table_id, view_id=view_id,
        )
        try:
            record, graphic, text, first_comment = choose_record(records, publish=publish, now=now)
        except CheckError as exc:
            if str(exc).startswith(f"No due Scheduled {BRAND_NAME} post with copywriting was found."):
                print(str(exc))
                return 0
            raise
        record_id = record.get("record_id")
        if not isinstance(record_id, str):
            raise CheckError(f"Selected {BRAND_NAME} record has no record ID.")
        if len(text) > MAX_POST_CHARS:
            raise CheckError("Copywriting exceeds Threads' 500-character post limit.")
        if len(first_comment) > MAX_POST_CHARS:
            raise CheckError("First/Second Comment exceeds Threads' 500-character reply limit.")

        account = threads_json(
            "GET", f"{THREADS_ROOT}/me?fields=id,username",
            token=threads_token, operation="verify Threads account access",
        )
        if not account.get("id"):
            raise CheckError("Threads did not confirm the connected account.")
        actual_username = str(account.get("username") or "").strip().lstrip("@").casefold()
        if not actual_username or actual_username != expected_username:
            raise CheckError("The connected Threads account does not match THREADS_EXPECTED_USERNAME.")

        if not publish:
            print(
                f"Dry-run passed for one Scheduled {BRAND_NAME} record (ID {record_id}); "
                f"copy is {len(text)} characters; image {'present' if graphic else 'not present'}; "
                "no post or Lark write was made."
            )
            return 0

        if graphic and not all((cloud_name, cloud_api_key, cloud_api_secret)):
            raise CheckError("Cloudinary credentials are required for the selected image.")
        post_id = publish_one(
            record=record, text=text, first_comment=first_comment, graphic=graphic,
            threads_token=threads_token,
            lark_token=lark_token, base_token=base_token, table_id=table_id,
            cloud_name=cloud_name, cloud_api_key=cloud_api_key,
            cloud_api_secret=cloud_api_secret, now=now,
        )
        comment_result = " with its first comment" if first_comment else ""
        print(
            f"Published one {BRAND_NAME} Threads post (post ID {post_id}){comment_result} "
            "and set its Lark status to Posted."
        )
        return 0
    except CheckError as exc:
        print(f"{BRAND_NAME} Threads workflow stopped safely: {safe_api_message(exc)}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print(
            f"{BRAND_NAME} Threads workflow stopped safely: could not save duplicate-prevention state; "
            "check Threads and Lark before retrying to prevent duplicates.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
