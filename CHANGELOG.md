# Changelog

Nothing is deleted or rewritten in this repository: a change is a new entry, a new version of a
rule file, a new commit. Dates are UTC.

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
