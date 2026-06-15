# Batch triage verification checklist

Use this when the watcher feeds a pre-deduped JSON batch.

1. **Count sanity check**
   - Compare the number of parsed posting objects to the wake-gate `new_count`.
   - If they differ, stop and inspect the raw batch before triaging more items.

2. **Canonical IDs**
   - Prefer `canonical_id` when present.
   - For URL inputs, strip query params before dedupe/history writes.

3. **History writes**
   - Append every posting to `postings_seen.json` after triage, including skips.
   - Keep the `reason` field specific to the failed filter.

4. **Queue writes**
   - Only `apply now` and `wait` rows go to the dashboard's `New Postings` section.
   - Skip rows stay in history only.

5. **Batch failure mode to watch**
   - If the batch count is short by a small number, the missing items are often adjacent entries dropped while reconstructing the raw list.
   - Reconcile the raw batch first; do not assume the wake-gate is wrong.