#!/usr/bin/env python3
"""Read-only Cloudinary credential check; never prints media or credential values."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing GitHub Actions secret/variable: {name}")
    return value


def main() -> int:
    try:
        cloud_name = required("CLOUDINARY_CLOUD_NAME")
        api_key = required("CLOUDINARY_API_KEY")
        api_secret = required("CLOUDINARY_API_SECRET")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cloud_name):
            raise RuntimeError("CLOUDINARY_CLOUD_NAME has an unexpected format.")

        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/resources/image?max_results=1"
        credentials = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Basic {credentials}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f"Cloudinary returned HTTP {response.status}.")
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Cloudinary returned HTTP {exc.code}; check the credential names and values."
            ) from None
        except urllib.error.URLError:
            raise RuntimeError("Could not reach Cloudinary's API.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Cloudinary returned an unexpected response.") from None

        if not isinstance(payload, dict) or not isinstance(payload.get("resources"), list):
            raise RuntimeError("Cloudinary returned an unexpected response format.")

        print("Cloudinary API credentials accepted (read-only check).")
        print("No upload, deletion, Threads publishing, or Lark write-back was attempted.")
        return 0
    except RuntimeError as exc:
        print(f"Cloudinary check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
