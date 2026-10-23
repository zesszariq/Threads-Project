#!/usr/bin/env python3
"""Read-only connectivity/schema check for the Assignment Ninja Threads view.

This script never publishes, mutates Lark, or prints record contents/media URLs.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://open.larksuite.com/open-apis"
TIMEOUT_SECONDS = 30
MAX_PAGES = 20


class CheckError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CheckError(f"Missing GitHub Actions secret/variable: {name}")
    return value


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(payload).encode("utf-8")
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not print the URL: it contains the Base token path segment.
        raise CheckError(f"Lark API returned HTTP {exc.code}. Check app scope/access and Base configuration.") from None
    except urllib.error.URLError as exc:
        raise CheckError(f"Could not reach Lark API: {exc.reason}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CheckError("Lark returned a response that was not valid JSON.") from None

    if not isinstance(result, dict):
        raise CheckError("Lark returned an unexpected response format.")
    if result.get("code", 0) != 0:
        code = result.get("code", "unknown")
        # Lark's message is useful for permissions/debugging; omit request URLs and tokens.
        message = str(result.get("msg", "API request failed"))[:240]
        raise CheckError(f"Lark API error {code}: {message}")
    return result


def get_tenant_token(app_id: str, app_secret: str) -> str:
    result = request_json(
        "POST",
        f"{API_ROOT}/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
    )
    token = result.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise CheckError("Lark did not return an app access token.")
    return token


def get_all_items(path: str, *, access_token: str, base_token: str, table_id: str,
                  view_id: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None

    for _ in range(MAX_PAGES):
        params: dict[str, str] = {"page_size": "100"}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id

        query = urllib.parse.urlencode(params)
        url = (
            f"{API_ROOT}/bitable/v1/apps/{urllib.parse.quote(base_token, safe='')}"
            f"/tables/{urllib.parse.quote(table_id, safe='')}/{path}?{query}"
        )
        result = request_json("GET", url, access_token=access_token)
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

    raise CheckError(f"Stopped after {MAX_PAGES} pages to keep the dry-run bounded.")


def main() -> int:
    try:
        app_id = required_env("LARK_APP_ID")
        app_secret = required_env("LARK_APP_SECRET")
        base_token = required_env("LARK_BASE_TOKEN")
        table_id = required_env("LARK_TABLE_ID")
        view_id = required_env("LARK_VIEW_ID")

        token = get_tenant_token(app_id, app_secret)
        fields = get_all_items(
            "fields", access_token=token, base_token=base_token, table_id=table_id
        )
        records = get_all_items(
            "records", access_token=token, base_token=base_token,
            table_id=table_id, view_id=view_id
        )

        schema = sorted(
            (
                {
                    "name": str(field.get("field_name", "")),
                    "type": field.get("type"),
                }
                for field in fields
            ),
            key=lambda field: field["name"].casefold(),
        )

        print("Lark dry-run connection: OK")
        print(f"Ninja view records visible: {len(records)}")
        print(f"Table fields visible: {len(schema)}")
        print("Field names and types (no cell contents):")
        for field in schema:
            print(f"- {field['name']} (type {field['type']})")
        print("Publishing: NOT attempted")
        print("Lark write-back: NOT attempted")
        return 0
    except CheckError as exc:
        print(f"Dry-run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
