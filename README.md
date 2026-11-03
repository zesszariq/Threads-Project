# Threads weekday publisher

This repository runs a guarded weekday Threads publisher for Ninja, A4U, and Pirate from one GitHub Actions workflow.

## Weekday publishing

The workflow checks at **10:00, 11:00, 12:00, and 1:00 PM Malaysia time, Monday to Friday** (02:00, 03:00, 04:00, and 05:00 UTC). Each run checks Ninja, A4U, and Pirate sequentially. For each brand it looks for one record in that brand's Lark view with Status set to `Scheduled`, Posting Date due today, and non-empty Copywriting. If a brand has no due item, it safely skips that brand. It refuses to choose automatically if multiple posts are due for one brand.

Each brand uses its own Threads access token, username, Lark view, and duplicate-prevention state file. The Lark app credentials, Base token, table ID, and Cloudinary account are shared. Image attachments are downloaded from Lark, compressed if needed, uploaded to Cloudinary, and sent to Threads using the resulting public HTTPS URL. After Threads confirms the post and any first comment, the matching Lark record is changed to Posted.

## Manual runs

Open **Actions → Threads weekday publisher → Run workflow**.

- Choose a brand and **dry-run** to verify access and validate an upcoming record. It does not publish or edit Lark.
- Choose a brand and **publish** to send one due post immediately for that brand.
- Scheduled runs publish automatically for all three brands.

GitHub Actions runs in the cloud, so the user's laptop does not need to be open. Scheduled job start times can be delayed by GitHub load.

## Required GitHub configuration

Shared repository secrets:

- `LARK_APP_ID`
- `LARK_APP_SECRET`
- `LARK_BASE_TOKEN`
- `CLOUDINARY_API_SECRET`
- Ninja: `THREADS_ACCESS_TOKEN` (or existing `THREADS_TOKEN`)
- A4U: `A4U_THREADS_ACCESS_TOKEN`
- Pirate: `PIRATE_THREADS_ACCESS_TOKEN`

Shared repository variables:

- `LARK_TABLE_ID`
- Ninja: `LARK_VIEW_ID`, `THREADS_EXPECTED_USERNAME`
- A4U: `A4U_LARK_VIEW_ID`, `A4U_THREADS_USERNAME`
- Pirate: `PIRATE_LARK_VIEW_ID`, `PIRATE_THREADS_USERNAME`
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`

The current Threads calendar fields used by the publisher are `Copywriting`, `First/Second Comment`, `Graphic`, `Posting Date`, and `Status`. The Lark app requires Base read/update and attachment-download access, and must have edit access to the Base. The Threads tokens require `threads_basic` and `threads_content_publish`.

Before calling Threads' publish endpoint, the workflow commits a durable reservation for the selected Lark record to a brand-specific file under `state/`. If a publish outcome is uncertain or a later write fails, future runs skip that record rather than risk a duplicate; that record then requires manual review.

Cloudinary copies used by published posts remain stored so image URLs stay available. Video attachments are not handled by this image-publishing flow.
