# Independent hourly scheduler: safety design

## Scope

This document covers execution reliability only. It does not change signal selection, indicators, risk, entry/exit rules, or Telegram message content.

## Current state

- The production workflow already has an hourly GitHub Actions schedule.
- GitHub schedule delivery has been delayed or absent in observed runs.
- The workflow supports a production `workflow_dispatch` input.
- Signal-state deduplication in `main.py` prevents repeat alerts per symbol for a time window; it is not a run-level idempotency lock.
- Scheduled production and manually dispatched production previously used different Actions concurrency groups.

## Change in this branch

Scheduled scans and manually dispatched production scans now share the `apexsignal-production` concurrency group. Audit and CI jobs remain isolated. `cancel-in-progress: false` is retained.

This reduces overlap between those two production entry paths. It does not guarantee exactly-once execution and does not replace a durable scheduler-side run ledger.

## Proposed independent scheduler

Use a Cloudflare Worker Cron Trigger as a watchdog/dispatcher only. It must not fetch market data, calculate signals, or send Telegram messages itself.

For each UTC hourly slot, the Worker should:

1. Derive a stable slot key, e.g. `apexsignal:production:YYYY-MM-DDTHH`.
2. Acquire a durable, atomic per-slot claim (prefer a Durable Object for serialized state; KV alone is not an atomic lock).
3. Query GitHub Actions for recent runs of the exact workflow and default branch.
4. If a production run for the slot is queued/in progress, record/retain the claim and do not dispatch.
5. If a successful production run already covers the slot, mark the slot complete and do not dispatch.
6. Otherwise dispatch the existing workflow with explicit `mode=production` and `ref=main`.
7. Poll/observe the resulting run and persist its run ID, status, timestamps, and outcome.
8. On ambiguous API/network outcomes, reconcile GitHub run history before retrying; never blindly dispatch again.

## Credentials and least privilege

- Store the GitHub credential only as a Cloudflare Worker secret.
- Use a fine-grained GitHub App installation token or fine-grained PAT restricted to this repository, with Actions: write for workflow dispatch and Actions: read for run reconciliation.
- Do not store Telegram credentials in Cloudflare. Telegram secrets remain only in GitHub Actions production job secrets.
- Never expose tokens in logs, URLs, artifacts, or repository files.

## Failure and duplicate protection

- A slot claim must be atomic and have an explicit lease/expiry policy.
- A timed-out dispatch request is ambiguous: check GitHub run history before retrying.
- A stale running job must be investigated before another production dispatch; do not assume it is safe to overlap.
- Keep GitHub Actions concurrency as a second line of defense.
- Keep the existing per-symbol signal dedupe as a separate third line of defense.
- Alert/record missed slots rather than creating a burst of catch-up scans.

## Rollout gates

1. Merge the shared production concurrency lock only after review and passing regression.
2. Deploy the Worker initially in observe-only mode; verify cron delivery and run reconciliation without dispatching production.
3. Exercise dispatch logic against a non-production/test workflow or a guarded dry-run mode.
4. Enable production dispatch only after duplicate, timeout, stale-run, and credential tests pass.
5. Monitor at least several hourly slots and retain an audit trail.

## Explicit non-goals

- No changes to strategy or signal thresholds.
- No direct Telegram access from the Worker.
- No automatic catch-up burst after missed hours.
- No claim of exactly-once delivery: external API dispatch and network failures require reconciliation and idempotent slot state.
