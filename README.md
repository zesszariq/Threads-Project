# Threads Publisher — Assignment Ninja pilot

This repository contains a manual media smoke test and a guarded weekday Threads publisher for the Assignment Ninja pilot.

## Weekday publishing

The Ninja Threads weekday publisher workflow is scheduled for **5:00 PM Malaysia time, Monday to Friday** (09:00 UTC). It looks for one Ninja record with Status set to Scheduled, Posting Date due today, and non-empty Copywriting. A scheduled run publishes no more than one post. It stops without posting if no item is due or if multiple items are due.

Image posts are downloaded from Lark, compressed if needed, uploaded to Cloudinary, and sent to Threads using the resulting public HTTPS URL. After Threads confirms publication, the same Lark record is changed to Posted.

## Manual runs

Open **Actions → Ninja Threads weekday publisher → Run workflow**.

- **dry-run** checks Lark access, verifies the Threads token and expected account, and validates one upcoming record. It does not publish or edit Lark.
- **publish** sends one due post immediately. This is a live action and is intended for the one-post test.

The scheduled workflow uses live publishing automatically at 09:00 UTC on weekdays.

## Required GitHub configuration

Repository secrets:

- LARK_APP_ID
- LARK_APP_SECRET
- LARK_BASE_TOKEN
- THREADS_ACCESS_TOKEN (or existing secret name THREADS_TOKEN)
- CLOUDINARY_API_SECRET

Repository variables:

- LARK_TABLE_ID
- LARK_VIEW_ID
- THREADS_EXPECTED_USERNAME (Threads handle without @, matching the authorized token)
- CLOUDINARY_CLOUD_NAME
- CLOUDINARY_API_KEY

The Lark app requires base:record:read, base:record:update, and docs:document.media:download. Permission changes must be included in an approved, released app version, and the app must have edit access to the Base. The Threads token requires threads_basic and threads_content_publish.

The workflow requires GitHub Actions permission to write repository contents. Before calling Threads' publish endpoint, it commits a durable reservation for the selected Lark record to state/ninja-published.json. If a publish outcome is uncertain or a later write fails, future runs skip that record rather than risking a duplicate. Such a record requires manual review.

Cloudinary copies used by published posts remain stored so image URLs stay available.

## Media smoke test

The Ninja Cloudinary media URL test is manual-only. It reads one scheduled graphic, compresses it if required, verifies its public Cloudinary URL, then deletes that temporary Cloudinary copy. It never publishes or writes to Lark.
