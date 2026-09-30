# Blind protocol

*Proof pack v2.0. Written by the author before the tag `v2.0.0-freeze`; updated by the author
before the tag `v2.0.1-freeze` (section "Since the first blind run"). Everything below is run by a
**different hand** from the author - a session with the other model of the fleet
(`claude-fable-5-1`) or the Rector - **once per frozen tag**, after the tag. The tag to measure
now is `v2.0.1-freeze`.*

## Why

The generator of the faults and the rules that find them have the same author. The development,
holdout and stress figures therefore measure internal consistency, and they are saturated. The
blind run is the honest datum: a seed, a set of perturbations and ten hand-written instances that
the author has never generated or seen.

## Since the first blind run

The blind run of `v2.0.0-freeze` was made on 2026-09-30 by the evaluator (Claude Opus 5.5, not the
builder), seed `20261011`, six reserved styles, two faults per instance, ten hand-written
instances. It is recorded verbatim in `eval/history.json`: localised 342/343, verdict 249/250,
false alarms 0/75, spurious 0, `never_event_restore` **1**, `never_event_admin_public` 0. All
three misses are one instance, hand-0007: the index of the secondary repository cut in half,
audited OK (finding T17). A never-event above zero is a failed pack: `v2.0.0-freeze` failed.

`v2.0.1-freeze` changes code and rules, not only documents (`CHANGELOG.md`):

* `lab/backup.py`, `lab/drill.py`, `lab/heartbeat.py`, `lab/run.py`, `lab/restore.py`,
  `lab/fsx.py`, `lab/routes.py`, `lab/postmortem.py`: both repositories are read completely
  (index, every block of the pack, every manifest); the drill result is `ok` only when every
  backup part in scope was read and verified; the report says per repository what was verified;
  silent fallbacks (an unreadable pack read as empty, a folder that cannot be listed read as
  empty, a repeated JSON key read as "last one wins") are gone.
* `rules/alerts.json` `2026.09.30-3` (A-035, A-040) and `rules/backup_policy.json`
  `2026.09.30-2` (B-000): definitions and inline tests; no class, verdict or order changed.
* `eval/score.py`: a blind run is refused when one is already recorded *for the same commit*
  (before: for the repository).
* Tests: `tests/test_complete_read.py`, with hand-0007 copied under `tests/data/hand-0007`.

What this means for the next run: seed `20261011` is used, and the ten hand-written instances of
the first run were read by the author (hand-0007 is a test): none of them may be reused. The
author read the evaluator's record and per-instance results of that run, for the fix only.

## What the author did and did not do

* Generated and inspected: the development suite, seed `20260930`.
* Generated and inspected: the stress suite, seed `20261002` (diagnostic, not a holdout).
* Ran once, aggregates only, corpus removed by the scorer, no instance inspected: the holdout
  suite, seed `20261001`.
* Seeds that appear in the tests and must also be avoided: `424242` (generated in memory by a
  determinism test, never audited) and `5` (argument checks only, nothing generated).
* Never ran `--suite blind`. Never generated any other seed.
* After the first blind run: generated and inspected the development suite and the stress suite
  again on the fixed code (same seeds). The holdout suite was not run again.
* Writing styles never fed to the lab by the author, kept for this run: `single-quoted`,
  `doc-markers`, `bom`, `anchors`, `port-string`, `tz-alias`. The author only checked that the
  generator's output in these styles is valid YAML for a plain `yaml.safe_load`
  (`tests/test_yaml_styles.py`).

## Before the run

```bash
git status --porcelain                       # must print nothing
git describe --tags --exact-match            # must print v2.0.1-freeze
python -m pip install --require-hashes -r requirements.txt
python tools/manifest.py --check             # must end with "manifest: OK"
python -m unittest discover -s tests -t .    # must end with OK
```

If any of these fails, stop and record the failure instead of the run.

## Choices of the runner

1. **Seed** `<N>`: an integer that is none of `20260930`, `20261001`, `20261002`, `20261011`,
   `424242`, `5` and that was not tried before. The scorer refuses the three suite seeds and any
   seed already in `eval/history.json`.
2. **Styles** `<STYLES>`: a comma-separated list. At least three of the six reserved styles
   above; any of the other styles may be added (`flow`, `double-quoted`, `comments`, `key-order`,
   `indent-4`, `crlf`, `yml-ext`, `host-case`, `prefix-slash`, `entrypoints-list`).
3. **Faults per instance** `<K>`: `1` or `2`.
4. **Ten hand-written instances** in a folder `<DIR>` outside the repository, following
   `docs/FORMAT.md`: one sub-folder per instance and one `labels.jsonl` with a line per instance.
   At least three of them clean, and at least one fault of a kind the generator does not plant in
   that shape. New ones: not the ten of the first blind run. The label is written from what was done to the instance, **before** the lab is run
   on it, and is not edited afterwards.

## The run

One command, once:

```bash
python eval/score.py --suite blind --seed <N> --runner "<who runs it>" --date <YYYY-MM-DD> \
    --styles <STYLES> --faults-per-instance <K> --handwritten <DIR>
```

It generates 240 instances from the seed, adds the ten hand-written ones, audits all of them,
prints the table, writes the `blind` entry of `eval/results.json` and appends the run - seed,
runner, date, commit, metrics - to `eval/history.json`. A second blind run of the same commit is
refused. The
per-instance details stay in `build/eval/blind/details.jsonl`, which is not tracked.

## After the run

1. The figures are reported **as they are**: recall per class (detected, localised), verdict
   accuracy, false alarms on clean instances, spurious findings, exactness of the restore lists,
   and the two never-event counters, which must be zero. A never-event above zero is a failed
   pack, whatever the other figures say.
2. One line is added under "Blind run" in the proof-pack section of the README, with seed, date,
   runner and the figures. The profile text below that section is not touched.
3. Any correction made after looking at the blind results goes in a separate line, marked
   **no longer blind**, with the rule file and version that changed. The blind figures are never
   overwritten.
4. The two changed files are committed, then the manifest is written on top:

```bash
git add eval/history.json eval/results.json README.md
git commit -m "Blind run: seed <N>, run by <who runs it>"
python tools/manifest.py --write
git add MANIFEST.sha256
git commit -m "Manifest after the blind run"
```

Nothing is rebased, reset or amended: the freeze tag stays where it is.
