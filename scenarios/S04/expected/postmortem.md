# Postmortem (synthetic) - S04-valdora-status-404

*Data as of 2026-09-14T07:00:00Z (UTC). Rendered by `lab/postmortem.py` from the audit report. Synthetic incident in a synthetic lab: companies, hosts and services are fictitious. Facts and identifiers only.*

## Summary

- Verdict: **FAILED** (1 failing finding, 1 in total).
- What failed: route `status-main` (public `status.valdora.example/`, file `routes/status.yaml`) answered 404 for 19 consecutive beats.
- What said it was fine: healthcheck of kind `process` for service `status` looked at the process and reported healthy at each failing beat.
- Decided by: rule `H-020` -> class `health_probes_process` (severity rule `S-030`).

## Timeline (UTC)

| time | source | fact |
|---|---|---|
| 2026-09-14T06:42:00Z | healthcheck | process check of service status reported healthy |
| 2026-09-14T06:42:00Z | probe | public status.valdora.example/ answered 404 (first failing beat) |
| 2026-09-14T06:43:00Z | probe | second consecutive failing beat (404): declared by rule H-020 |
| 2026-09-14T07:00:00Z | audit | audit run, verdict FAILED |
| 2026-09-14T07:00:00Z | probe | last failing beat of the run (19 consecutive) |

## Evidence

- `health_probes_process` [FAILED] at `topology.yaml`, item `status`, rule `H-020`.
- Rule files: `alerts` 2026.09.30-2, `backup_policy` 2026.09.30-1, `health` 2026.09.30-2, `route_lint` 2026.09.30-1, `severity` 2026.09.30-2.
- Route table as loaded: 4 entries, SHA-256 `45572f11d6437a6d4bcb5d44adf13c8580771dc68ea369560658a80ef029d67a`.

## What the checks did not see

- Healthcheck of kind `process` for service `status` looked at the process and reported healthy at each failing beat.

## Follow-ups

- Owner and date for each corrective action: [TO CONFIRM].
- Re-run the audit after the correction; the verdict must change by a changed fact, not by an edited report.
