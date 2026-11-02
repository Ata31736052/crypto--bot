# ApexSignal — Binance Spot Signal Scanner

This repository contains the ApexSignal scanner for Binance Spot markets. It publishes candidate signals to Telegram; it does not place exchange orders.

## Current project status

- Production configuration remains unchanged. Experimental signal/confirmation variants were not promoted to `main`.
- The available multi-timeframe comparison did not justify replacing the existing 1H confirmation with 30m or 15m+30m confirmation. Keep the production configuration stable; do not treat bounded historical replay results as a forecast of future performance.
- A read-only root-cause review records the verified production log observations, existing safeguards, experiment disposition, and limitations: [Root-cause review — 2026-11-02](docs/ROOT_CAUSE_REVIEW_2026-11-02.md).
- The state-persistence workflow was hardened: failures to commit/push changed `signals_state.json` are no longer silently ignored. This improves visibility of persistence failures; it does not change signal selection.

## Operational safeguards

The production scanner includes bounded API retries, fail-closed liquidity-universe validation, exclusion of the still-forming candle, fresh quote checks before Telegram delivery, entry-drift checks, bounded stop-distance and risk/reward validation, atomic local state writes, and workflow self-tests. These are implementation safeguards, not guarantees of signal quality or financial outcomes.

## Validation note

A successful regression workflow validates the tested code path only. A push-triggered regression run does not mean the scheduled production scan has executed. Check the actual production workflow run before describing a new production execution as verified.

## Repository boundary

This README and the linked review apply only to `Ata31736052/crypto--bot` (ApexSignal). They do not describe or modify the separate `crypto---bot` repository.
