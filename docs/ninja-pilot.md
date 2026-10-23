# Assignment Ninja publishing loop

## Loop definition

- **Check cadence:** Check the Lark calendar on a modest recurring cadence once credentials and field mapping are configured. The calendar's scheduled time is authoritative; a scheduler check is not a promise of exact-second delivery.
- **Acts when:** A record belongs to Assignment Ninja, is approved, is due in Asia/Kuala_Lumpur, has valid required content, and has not already been published or reserved.
- **Purpose:** Publish approved Ninja calendar content to the correct Threads account and keep the calendar's publishing state accurate.
- **Loop body:**
  1. Read due Ninja records only.
  2. Validate approval, destination account, copy, media, and schedule.
  3. In dry-run, report record IDs and validation results without publishing.
  4. In live mode, reserve one record, publish it, then write the Threads post ID/link and final status back to that same record.
- **Self-check:** Confirm the record's brand/account matches the configured Ninja allowlist; status is approved; scheduled time is due; media is available; and no prior published ID or in-flight reservation exists.
- **State / idempotency:** Use the Lark record ID as the dedupe key. Record a publishing/in-flight state before sending. Store the returned Threads post ID and URL after success. Never blindly retry an ambiguous result.
- **Stop / bail-out:** Publishing is off by default. Stop if credentials, field mapping, approval, media, account identity, or Lark write-back cannot be verified. Stop on API/auth errors or uncertain publication outcomes and require human review. Disable the workflow or the live-publishing flag for the kill switch.
- **Output:** Dry-run summary or, after successful publication, the post ID/link and status written to Lark. Logs should contain record IDs and outcomes, not credentials or full post text.

## Logical fields to map after Lark access is approved

The actual field names and types must be read from the real Base table before implementation.

- brand/account allowlist (Assignment Ninja)
- post copy
- optional image/media reference
- scheduled date and time
- approval state
- publishing state
- Threads post ID and URL
- safe failure/review note

## Rollout gates

1. Read the approved table structure and map the real fields.
2. Configure credentials in GitHub Actions secrets; never commit them or paste them into chat.
3. Run dry-run checks against one approved Ninja record.
4. Confirm the Ninja Threads authorization and image handling.
5. Enable live publishing for one approved test record, with a one-post-per-run cap.
6. Turn on the recurring schedule only after the test and Lark result write-back both pass.
