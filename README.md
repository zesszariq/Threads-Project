# Threads Publisher — Assignment Ninja pilot

This repository will hold the first scheduled Threads publishing workflow, starting with Assignment Ninja.

## Current state

Bootstrap only. No scheduler or live publishing is enabled yet. The initial setup must remain dry-run until the Lark source fields and the Ninja Threads authorization are verified.

## Intended flow

1. Read only Ninja calendar records marked approved and due to publish.
2. Validate the copy, media, account allowlist, and scheduled time.
3. Publish through Meta's official Threads API only after production is deliberately enabled.
4. Write the result (published status, post ID/link, or a safe error state) back to the matching Lark record.

## Guardrails

- Default to dry-run; publishing remains disabled until an explicit production configuration is added.
- Never publish a draft or a record for another brand/account.
- Limit each run to one post and prevent duplicate publication by tracking the Lark record ID and publication state.
- If a request may have succeeded but the result is uncertain, stop for review rather than retrying and risking a duplicate.
- Keep credentials in GitHub Actions secrets, never in source files, commits, or logs.
- Provide a kill switch that disables publishing immediately.

## Remaining setup

- Lark admin approval for the Ninja calendar, with the minimum read/write scopes needed for the post fields and result fields.
- The exact Ninja Base/table/view and its real field names; map these from the approved Base, not guessed names.
- Authorization of the Ninja Threads account for the Threads publishing API.
- A confirmed posting schedule in Asia/Kuala_Lumpur and one approved post for an end-to-end test.

Until those are confirmed, this repository is documentation only and cannot publish.