# Threads Publisher — Assignment Ninja pilot

This repository is being prepared for a scheduled Threads publishing workflow, starting with Assignment Ninja.

## Current state

A manual, read-only Lark dry-run workflow has been added on the `codex/ninja-lark-dry-run` branch. It is not yet merged or run. It checks that the Lark app can obtain an app token, read the Ninja view's fields and records, and reports only counts/schema—never post copy or image URLs.

There is no scheduler or live publishing enabled.

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

## What is needed to run the dry-run

After merging the branch, configure these in the repository's Settings → Secrets and variables → Actions:

Secrets:
- `LARK_APP_ID`
- `LARK_APP_SECRET`
- `LARK_BASE_TOKEN`

Repository variables:
- `LARK_TABLE_ID` — Threads Content Planner table ID
- `LARK_VIEW_ID` — Ninja view ID

Then open Actions → “Ninja Lark dry-run” → Run workflow. It is manual and read-only.

The app has been added to the Base with view-only access, but the workflow has not yet verified that the app credentials can read it. The dry-run will reveal the real field names/types; use those to map approval, copy, media, schedule, and publishing-state fields before implementing any publishing or write-back.

## Remaining rollout gates

- Run and pass the Lark dry-run against the Ninja view.
- Map and validate the real field names/types.
- Confirm the Ninja Threads account authorization and required publishing credentials.
- Decide on write-back: the app currently has view-only Base access, so it cannot update publishing status or store post IDs/links.
- Test an approved post with an explicit, one-post live-publishing enablement.
- Turn on a recurring schedule only after the end-to-end test and duplicate protection pass.
