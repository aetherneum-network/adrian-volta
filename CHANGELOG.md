# Changelog

Nothing is deleted or rewritten in this repository: a change is a new entry, a new version of a
rule file, a new commit. Dates are UTC.

## v2.0.2-freeze - 2026-10-02

Documentation only, after the publication and the recorded blind run. No file that decides a
result changed: `lab/`, `rules/`, `corpus/`, `scenarios/`, `tests/`, `tools/`, `compose/`,
`eval/score.py` and `requirements.txt` are identical to `v2.0.1-freeze`, which stays where it is;
the blind run of `v2.0.1-freeze` measured the same code. `eval/history.json` and
`eval/results.json` are unchanged. In the workflow file only a comment changed. Written through
Claude Opus 5.5.

- **Publication and CI.** The branch was published on 2026-10-02 as pull request #2 of this
  repository and the workflow runs on GitHub-hosted runners. Run 37013132166 (push, commit
  `0a16a1e`, 2026-10-02) passed on `ubuntu-latest` (ubuntu-24.04, CPython 3.12.14) and
  `windows-latest` (Windows Server 2025, CPython 3.12.10): 307 tests OK, scenarios 10/10 PASS, scan
  clean, and on both systems the two rebuilds gave the digest of `reports/REBUILD.sha256`
  (`7454103ed44ca25212954c0ca1596a9f31455456489b8a68f5ef59409ea39a3b`, 10376 files). The
  statements written before that, that the workflow had never been executed and that nothing was
  pushed, now say so: the comment of `.github/workflows/ci.yml`, `CLAIMS.md` known limit 6 (the
  Linux digest was `[TO CONFIRM]`) and the last section of the README proof-pack section. The line
  "Linux: the CI workflow was written, never executed. Nothing was pushed." of the entry
  `v2.0.1-freeze` described that version and stays as written.
- **Blind run of `v2.0.1-freeze`.** The README table already carried its row (commit "Blind run:
  seed 20261012"); `CLAIMS.md` (section 1 and known limit 1) and the last section of the README
  still said that it had not been made. They now state what `eval/history.json` records: 2026-09-30,
  the evaluator (Claude Fable 5.1, not the builder), seed `20261012`, localised 347/347, verdict
  250/250, never-events 0 and 0.
- `MANIFEST.sha256`: written by `tools/manifest.py` in the next commit, which the tag
  `v2.0.2-freeze` points to.
- Still `[TO CONFIRM]`: action pins by commit SHA, runner image digests, the human signature on the
  content declaration.

## v2.0.1-freeze - 2026-09-30

Fix of finding **T17**. Source: the evaluator's blind run of 2026-09-30 on `v2.0.0-freeze` (seed
`20261011`, runner "evaluator (Claude Opus 5.5), not the builder"), recorded verbatim in
`eval/history.json` in its own commit before any fix. Frozen with the annotated tag
`v2.0.1-freeze`. The blind figures of `v2.0.0-freeze` stay as recorded; `v2.0.1-freeze` has not
been measured blind. Written by the synthetic alumnus (an AI agent) through Claude Opus 5.5.

### T17 - an OK that said nothing about the secondary repository

- **What the blind run saw.** Hand-written instance hand-0007: the index of the secondary
  repository cut halfway through (804 bytes). The lab answered verdict OK, exit code 0, "RUN OK",
  drill `ok`; the gold label is `repo_b_unreadable`, ALERT. That one instance is the missed fault
  (localised 342/343), the wrong verdict (249/250) and the `never_event_restore` of 1 in that run:
  the three figures are one defect.
- **Cause.** `lab/heartbeat.py` called the secondary readable when the `created` field of its
  newest manifest could be read (`Repo.created` in `lab/backup.py`); nothing read its index or its
  pack. `lab/run.py` drilled the primary only and reported that drill as the drill result, so an
  `ok` said nothing about the secondary. Rule A-035 described the fact in the same narrow way.
- **Fix, code.** `lab/backup.py`, `inspect`: a complete read of a repository - the index parsed
  to its end, every block it lists found inside the pack and hashed to its address, every snapshot
  manifest read, every listed file rebuilt to its size and SHA-256; each problem carries its
  location. `lab/drill.py`: a `DrillResult` is ok only with that complete read of its repository;
  `ScopeResult` makes `drill.result` `ok` only when the primary drill held *and* the secondary was
  read completely and verified. `drill.primary_result` keeps the primary alone,
  `drill.repositories` says per repository what was read and verified, and `drill.unverified`
  lists every part that was not, with its location. The console prints one line per repository;
  the postmortem too. `lab/heartbeat.py`: `secondary_readable` is the complete read; its problems
  are listed with their location and no age is computed from an unread repository. `lab/run.py`:
  a structural guard (`RUN-050`) would name the unverified repository if a drill that is not ok
  ever ended without a finding.
- **Silent fallbacks removed on the way.** `lab/restore.py` replaced a pack that could not be read
  by an empty one and then reported missing blocks: now `pack_unreadable`. `lab/fsx.py`
  `walk_files` skipped a folder that could not be listed: now it raises; `Repo.snapshot_ids` turns
  that into "cannot be listed", never "no snapshot", and `lab/routes.py` into `route_unreadable`.
  Repository JSON with a repeated key was read as "last one wins": now it is unreadable. The
  primary is read completely too: an older manifest that cannot be read, or a corrupt block used
  only by an older snapshot, now fails the drill.
- **Fix, rules.** `rules/alerts.json` `2026.09.30-3`: A-035 states the complete read, new inline
  test A-035-c; the rationale of A-040 says absent or empty. `rules/backup_policy.json`
  `2026.09.30-2`: B-000 covers any part of the repository, new inline test B-000-b; the note says
  where the drill facts come from. No class, verdict or order changed.
- **Tests.** `tests/test_complete_read.py`: hand-0007 byte for byte (`tests/data/hand-0007`, the
  two packs stored with a `.txt` suffix so that the scanner reads them), eleven damages of the
  secondary (index cut in half, empty, with a repeated key, not UTF-8; pack cut short, missing;
  index entries beyond the pack; an entry missing that an older manifest needs; an older manifest
  cut short; a corrupt block; a snapshots path that is not a folder), a secondary missing
  altogether and one that is empty, three cases on the primary, and the fallbacks above.
  `tests/test_never_event.py`: a drill without the complete read is not ok. On the code of
  `v2.0.0-freeze` 18 of the 19 new tests fail - hand-0007 audits OK with exit 0, and ten of the
  eleven damages of the secondary audit OK.
- **Scorer gate.** `eval/score.py` refused any blind run once one was recorded in the repository,
  which would have refused the blind run of this tag. It now refuses a second blind run of the
  same commit.
- **Measured again, not blind:** the development suite (seed `20260930`) and the stress suite
  (seed `20261002`) on the fixed code, recorded in `eval/history.json`; the holdout suite was not
  run again. Scenario S04: only the rule-versions line of its expected postmortem changed
  (re-pinned). New rebuild digest in `reports/REBUILD.sha256`.

### Not done, not verified

- The blind run of `v2.0.1-freeze`: to be made once, by a different hand, with a new seed and new
  hand-written instances (`eval/BLIND_PROTOCOL.md`). Seed `20261011` and the ten hand-written
  instances of the first run are no longer blind.
- An unexpected exception in `python -m lab.run` ends with Python's exit code 1, the number of
  ALERT (no report is written and "RUN OK" is not printed). Not changed here: `[TO CONFIRM]`.

## v2.0.0-freeze - 2026-09-30

First proof pack. Frozen with the annotated tag `v2.0.0-freeze` before the blind run. Written by
the synthetic alumnus (an AI agent) through Claude Opus 5.5.

### Added

- `lab/`: route table and lint, trace simulator and route-level probe, content-addressed backup,
  restore drill, append-only drill registry, retention, nightly windows, postmortem, and the audit
  command `python -m lab.run`.
- `rules/`: five ordered rule files, 38 rules, each with inline tests.
- `corpus/`: seeded generator (three fictitious companies, twelve fault classes), gold labels and
  manifest of the development suite, and three references that share no code with the lab.
- `scenarios/`: S01-S10 with `scenario.json`, `input/`, `expected/`, `check.py`, `run.md`, and
  `scenarios/run_all.py`. S03 and S10 are negative.
- `tests/`: the offline unittest suite, with the attempts at the never-event.
- `eval/`: scorer, results, history of every run, blind protocol.
- `tools/`: double rebuild, manifest, content scanner, generated orchestrator profile.
- `SYNTHETIC.md`, `CLAIMS.md`, `MODEL.md`, `docs/FORMAT.md`, `requirements.txt` with hashes, an
  offline CI workflow, and a proof-pack section at the top of the README. The profile text of the
  README is byte-identical to what it was.

### Differences from the approved plan

- **Exit code 1 for ALERT.** The plan lists `0` OK, `2` BLOCKED, `3` FAILED. An alert (stale or
  unreadable secondary, missed window, mixed time zones) is not a success, so it got its own code
  instead of `0`. "RUN OK" is printed for verdict OK only.
- **`requirements.txt` is the lockfile.** The plan names `requirements.lock`; the file asked for
  in the work order is `requirements.txt` with hashes. There is one file, with that name.
- **Tag.** Only `v2.0.0-freeze` exists. The plan's `v2.0.0` is for after the blind run and is not
  created here.
- **Documents before the blind run.** The plan writes README, `CLAIMS.md` and `MODEL.md` after
  the blind run. They are written before the tag, with the blind line marked "not run yet"; the
  runner adds one line.
- **"Three production deploys".** The plan recommends removing the sentence from the profile. It
  was not removed: the profile text was not to be edited in this work. `CLAIMS.md` lists it as not
  demonstrated.
- **No containers**, as the plan decided: `compose/` is an illustration generated from the S01
  topology and never executed.

### Corrections made while building (also in `eval/history.json`)

- **C-1, rules/health.json `2026.09.30-2`.** First stress run: 335/336 detected, one spurious
  `service_down`. A restart in the middle of a long silent failure hid it. Rule H-020 now counts
  the failing beats at which the declared healthcheck said healthy. Not blind.
- **C-2, rules/alerts.json and rules/severity.json `2026.09.30-2`.** A secondary repository whose
  newest snapshot could not be read produced no finding, so a run could end OK. New rule A-035,
  class `repo_b_unreadable`, verdict ALERT. Found by writing tests, not by a suite.
- **C-3, tests.** The message of commit 030711c names a renamed file among the never-event
  attempts; the case was added in the next test commit. The pack already refused it.
- **C-4, eval/score.py.** The scorer stopped with an error on an instance with a partial scope and
  on a hand-written fault class. Labels are now validated first; references are asked by scope; a
  part no reference can read is not vouched for. Made after the single holdout run; the lab and the
  rules did not change and the development suite scores byte-identical.

The first versions of `rules/health.json`, `rules/alerts.json` and `rules/severity.json`
(`2026.09.30-1`) were superseded before the first commit and are not in the history; what they
measured is in `eval/history.json`.

### Not done, not verified

- The blind run (`eval/BLIND_PROTOCOL.md`): to be made once by a different hand.
- Linux: the CI workflow was written, never executed. Nothing was pushed.
- Action pins by commit SHA, runner image digest, human signature on the content declaration:
  `[TO CONFIRM]`.
- The source-archive hash of PyYAML is not in the lockfile (its local build failed; only the two
  wheel hashes were verified).
- The intermediate commits were cut from the finished tree and were not tested one by one.
