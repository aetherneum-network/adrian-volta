# Claims, evidence and limits

*Proof pack v2.0 - evidence item E9. Written 2026-09-30 by the synthetic alumnus (an AI agent),
through Claude Opus 5.5.*

This file takes the statements of the public profile - the README from the title "Adrián Volta"
down, which this pack left exactly as it was - and gives each one of three outcomes:

* **demonstrated by** a scenario or a test of this pack, within the limits written next to it;
* **not demonstrated: out of v2.0** - the pack contains no evidence for it;
* **awaiting legal review — not touched** - the wording is under review elsewhere; this pack
  neither edits it nor builds evidence on it.

Everything that is demonstrated is demonstrated on a **model**: a lab in pure Python that reads
invented topologies and its own file formats. The figures are internal consistency on synthetic
data (`SYNTHETIC.md`). Nothing below is evidence about a real system.

## 1. The six capabilities of the pack

| | capability, as the lab shows it | scenarios | tests | rule files |
|---|---|---|---|---|
| A1 | A service is exposed by adding one route file. A router that points to a missing backend, and a forgotten copy that shadows a live route, block the table. | S01, S02 | `tests/test_routes_lint.py`, `tests/test_yaml_styles.py` | `rules/route_lint.json` |
| A2 | An admin service never becomes reachable from a public entrypoint, by router or by network: the table is refused and gets no accepted digest. | S03 (negative) | `tests/test_routes_lint.py`, `tests/test_references.py` | `rules/route_lint.json` |
| A3 | Backup to two repositories with restore drills. A restore is successful only when every restored file has the SHA-256 of the source manifest, no file is missing and none is extra. A drill is ok only when both repositories were read completely and verified - every index entry, every block of the pack, every snapshot manifest - and the report says per repository what was verified (since `v2.0.1-freeze`, finding T17). A stale or unreadable secondary is an alert. Retention never supersedes the only snapshot with a verified drill. | S05, S06, S07, S10 (negative) | `tests/test_never_event.py`, `tests/test_complete_read.py`, `tests/test_roundtrip.py`, `tests/test_backup_store.py`, `tests/test_fsx_jsonio.py` | `rules/backup_policy.json`, `rules/alerts.json` |
| A4 | Health is what the route answers, not what the process says. A route that fails while the declared healthcheck stays healthy is a finding; three restarts in ten minutes are a loop. | S04 | `tests/test_sim_probe.py` | `rules/health.json` |
| A5 | The nightly chain runs in order and in UTC. A backup taken before the export, a missed window, a schedule in a local time zone and timestamps without a zone are reported; nothing is assumed about an instant that is not written. | S08, S09 | `tests/test_heartbeat.py` | `rules/alerts.json`, `rules/backup_policy.json` |
| A6 | A postmortem is written from the findings: timeline, cause, what detected it, no names and no blame. The profile does not state this one; the plan of the pack added it. | S04 | `tests/test_postmortem.py` | `rules/severity.json` |

Across all six: `tests/test_rules.py` runs the inline tests of the 38 rules, `tests/test_cli.py`
checks the exit codes and the words "RUN OK", `tests/test_offline.py` checks that nothing opens a
socket, `tests/test_scenarios.py` checks the ten scenarios and their negative controls,
`tests/test_determinism.py` checks the double rebuild, and `eval/score.py` measures the twelve
fault classes against gold labels (`eval/results.json`, `eval/history.json`).

**The never-event.** A restore declared successful while even one file's hash differs from the
source manifest. Attempts: `tests/test_never_event.py` (damage to the pack, to the index, to the
snapshot listing, a snapshot forged to be consistent with itself, a source changed after the
backup, stale files in the scratch folder, an edited registry, 120 seeded mutations),
`tests/test_complete_read.py` (the instance of the blind run below, byte for byte, and the
damages of the same family on either repository), S06, S10,
and the counter `never_event_restore` of the scorer, which is judged by
`corpus/reference_hashes.py` and not by the lab. Measured: 0 on the development, holdout and
stress suites (2026-09-30). **1 in the blind run of `v2.0.0-freeze`** (2026-09-30, seed
`20261011`, run by the evaluator): one hand-written instance whose secondary index was cut in
half was audited OK with drill `ok` (finding T17). Fixed in `v2.0.1-freeze` (`CHANGELOG.md`); the
fix has not been measured blind. The development and stress suites give 0 again on the fixed code;
the holdout suite was not run again. The second counter, `never_event_admin_public`, is judged by
`corpus/reference_reach.py`: 0 on the same suites.

## 2. The statements of the profile, one by one

Quotations keep the words of the profile; dashes and separators are simplified.

| # | statement in the profile | outcome |
|---|---|---|
| 1 | Thesis: "file-provider reverse-proxy" - a new service is exposed by editing a YAML file instead of container labels (thesis summary, biography, skills, contributions) | demonstrated by S01, S02 (A1), on a model of a file-provider route table; no proxy product is run |
| 2 | "dual-plane public/admin routing", "public + admin segregated" | demonstrated by S03 (A2) |
| 3 | "dual-repo backup", "running on a nightly cadence", "on nightly cadence" | demonstrated by S05, S06, S07 (A3) and S08, S09 (A5), on the lab's own repository format and a virtual clock |
| 4 | "Backup discipline - dual-repo, scheduled restore drills" | demonstrated by S05, S06, S10 (A3) |
| 5 | "multi-network topology, healthchecks, restart policies" | demonstrated by S04 (A4) and `tests/test_sim_probe.py`, as a model: networks, healthcheck kinds and restart policies are fields of an invented topology |
| 6 | "Adrián is not happy until every container in the topology reports healthy" | the capability behind it is A4 (S04); the sentence itself is voice |
| 7 | "Docker + container orchestration" | not demonstrated: out of v2.0. No container is started; `compose/S01-valdora.compose.yaml` is generated and never executed |
| 8 | "at scale", "production-scale container topology", "production container topology" | not demonstrated: out of v2.0. The largest instance of the pack (S02) has fourteen services and forty-one routers, all invented |
| 9 | "threshold-based key custody", "Secrets management - threshold-based key custody, dynamic secrets" | not demonstrated: out of v2.0 |
| 10 | "TOTP forward-auth", "Forward-auth - TOTP 2FA at the proxy layer" | not demonstrated: out of v2.0 |
| 11 | "VPN admin plane - peer config generation, segregated routing" | not demonstrated: out of v2.0 |
| 12 | "Observability - Prometheus / Grafana / Loki / Promtail, retention policies" | not demonstrated: out of v2.0. The lab has its own probe and heartbeat; none of these products is used |
| 13 | "DNS-01 wildcard certificates - automated renewal" | not demonstrated: out of v2.0 |
| 14 | "the profile cites three production deploys as live evidence" (thesis summary and contributions) | not demonstrated: out of v2.0. This repository contains no evidence of any deploy. The plan of the pack recommends removing the sentence; it is still there because the profile text was not to be edited in this work |
| 15 | "saves the founder hours per deploy" | not demonstrated: out of v2.0. Nothing was timed |
| 16 | "keeps the substrate of the entire portfolio standing", "Primary Placement: The substrate - cross-portfolio infrastructure" | not demonstrated: out of v2.0 |
| 17 | "operates via specialist subagent invocations ... Each invocation is recorded in the git history of the placement repository; the trail is auditable end-to-end" | not demonstrated: out of v2.0. No such component took part in this pack and no such trail is in this repository (`MODEL.md`) |
| 18 | "Faculty Advisor: Claude Sonnet 4.6" (header and diploma) | not demonstrated: out of v2.0. The pack was written through Claude Opus 5.5 and calls no model (`MODEL.md`) |
| 19 | "Master of the Æther - Topological Resilience", "Phase 0 - profile-attested - re-defense scheduled", the diploma block and its signatures | status and title, not capabilities: outside the pack |
| 20 | "Sleeps poorly ...", "Has memorized the entire YAML tree ...", "His best work is what didn't break." | voice, not capabilities: outside the pack |
| 21 | The thesis title as written in "Notable Contributions" (first item) and inside the diploma block | awaiting legal review — not touched |
| 22 | The catalogue line (number of alumni, subagents and skills) and the links to the university pages | outside the pack |

Count, over the 18 statements that assert a capability or a fact about the work (1-18): 5
demonstrated on the model (1-5), 1 partly (6), 12 not demonstrated (7-18). The standard asks that
a statement without evidence leaves the profile: that is a decision for the owners of the profile
text and is not taken here.

## Known limits

1. **Same author.** The generator of the faults and the rules that find them were written by
   the same agent. Development and holdout figures are saturated and measure internal consistency
   on synthetic data. The blind run of `v2.0.0-freeze` (`eval/BLIND_PROTOCOL.md`) found one
   defect, T17: localised 342/343, verdict 249/250, `never_event_restore` 1, all three from one
   instance. The blind run of `v2.0.1-freeze`, which carries the fix, has not been made.
2. **No independent reference for four classes.** Missed window, mixed time zones, retention, and
   the policy-order variant of the inverted chain are checked against the fault plan only
   (`docs/FORMAT.md`, section 4).
3. **A model, not the products.** The route table, the healthcheck, the restart policy, the
   repository and the nightly chain are the lab's own, simplified, formats. Agreement with how any
   real reverse proxy, container engine or backup tool behaves was not measured.
4. **The repository is plain.** Fixed 1024-byte blocks addressed by SHA-256; no encryption, no
   compression, no concurrency, no remote transport. "Secondary" is a second folder.
5. **The clock is virtual.** Nothing is scheduled for real: the audit is one chained command that
   replays a trace up to `as_of` and declares what it could not evaluate.
6. **One platform measured.** Windows x86-64, Python 3.12.10. The CI workflow targets Linux and
   Windows and has never been executed; the rebuild digest on Linux is `[TO CONFIRM]`.
7. **Lockfile.** `requirements.txt` carries the hashes of two wheels (CPython 3.12, Windows and
   Linux x86-64). Other platforms need their own. Action pins by commit SHA and the image digest
   are `[TO CONFIRM]`.
8. **Content scan.** `tools/scan.py` looks for a few recognisable patterns. The human signature on
   the declaration is `[TO CONFIRM]`.
9. **YAML.** Six writing styles were kept for the blind run and never fed to the lab by the
   author. The reader refuses what it does not understand (repeated keys, unknown keys), which can
   make it stricter than the tools it models.
10. **History.** The commits were cut from the finished tree in the order of the plan; the
    intermediate commits were not tested one by one. The tests were run on the final tree.
