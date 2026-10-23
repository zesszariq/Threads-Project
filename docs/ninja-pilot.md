# Assignment Ninja publishing loop

## Loop definition

- **Check cadence:** Check the Lark calendar on a modest recurring cadence once credentials, field mapping, and publishing authorization are verified. The calendar's scheduled time is authoritative; a scheduler check is not a promise of exact-second delivery.
- **Acts when:** A record belongs to Assignment Ninja, is approved, is due in Asia/Kuala_Lumpur, has valid required content, and has not already been published or reserved.
- **Purpose:** Publish approved Ninja calendar content to the correct Threads account and keep the calendar's publishing state accurate.
- **Loop body:**
  1. Read the configured Ninja view only.
  2. Validate approval, destination account, copy, media, and schedule using the real Base field mapping.
  3. In dry-run, report only safe record identifiers and validation outcomes; never publish.
  4. In live mode, reserve one record, publish it, then write the Threads post ID/link and final status back to that same record.
- **Self-check:** Confirm the record's brand/account matches the configured Ninja allowlist; status is approved; scheduled time is due; media is available; and no prior published ID or in-flight reservation exists.
- **State / idempotency:** Use the Lark record ID as the dedupe key. Record a publishing/in-flight state before sending. Store the returned Threads post ID and URL after success. Never blindly retry an ambiguous result.
- **Stop / bail-out:** Publishing is off by default. Stop if credentials, field mapping, approval, media, account identity, or Lark write-back cannot be verified. Stop on API/auth errors or uncertain publication outcomes and require human review. Disable the workflow or the live-publishing flag for the kill switch.
- **Output:** The current manual dry-run verifies app access and prints record count plus field names/types only. It does not print post contents or image URLs, and does not attempt publishing or write-back. Future live runs should report post ID/link and the status written to Lark without logging full post text.

## Current read-only test

The `Ninja Lark dry-run` workflow is manual-only. It obtains an app token, reads the configured table schema and records in the configured Ninja view, and reports safe metadata. It has no Threads API call and no Lark write operation. See the repository README for the required GitHub Actions secrets and variables.

## Logical fields to map after dry-run

Use the real field names and types returned by the dry-run; do not guess them.

- brand/account allowlist (Assignment Ninja)
- post copy
- optional image/media reference
- scheduled date and time
- approval state
- publishing state
- Threads post ID and URL
- safe failure/review note

## Rollout gates

1. Merge and run the manual read-only dry-run; confirm the actual Ninja table/view and field schema.
2. Configure credentials in GitHub Actions secrets; never commit them or paste them into chat.
3. Map and validate the actual record fields and image URL access.
4. Confirm the Ninja Threads authorization and image handling.
5. Decide whether the app should receive edit access for Lark write-back; it is currently view-only.
6. Enable live publishing only for one approved test record, with a one-post-per-run cap.
7. Turn on the recurring schedule only after the test and duplicate protection pass.
