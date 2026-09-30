# Blind protocol

*Proof pack v2.0. Written by the author before the tag `v2.0.0-freeze`. Everything below is run
by a **different hand** from the author - a session with the other model of the fleet
(`claude-fable-5-1`) or the Rector - **once**, after the tag.*

## Why

The generator of the faults and the rules that find them have the same author. The development,
holdout and stress figures therefore measure internal consistency, and they are saturated. The
blind run is the honest datum: a seed, a set of perturbations and ten hand-written instances that
the author has never generated or seen.

## What the author did and did not do

* Generated and inspected: the development suite, seed `20260930`.
* Generated and inspected: the stress suite, seed `20261002` (diagnostic, not a holdout).
* Ran once, aggregates only, corpus removed by the scorer, no instance inspected: the holdout
  suite, seed `20261001`.
* Seeds that appear in the tests and must also be avoided: `424242` (generated in memory by a
  determinism test, never audited) and `5` (argument checks only, nothing generated).
* Never ran `--suite blind`. Never generated any other seed.
* Writing styles never fed to the lab by the author, kept for this run: `single-quoted`,
  `doc-markers`, `bom`, `anchors`, `port-string`, `tz-alias`. The author only checked that the
  generator's output in these styles is valid YAML for a plain `yaml.safe_load`
  (`tests/test_yaml_styles.py`).

## Before the run

```bash
git status --porcelain                       # must print nothing
git describe --tags --exact-match            # must print v2.0.0-freeze
python -m pip install --require-hashes -r requirements.txt
python tools/manifest.py --check             # must end with "manifest: OK"
python -m unittest discover -s tests -t .    # must end with OK
```

If any of these fails, stop and record the failure instead of the run.

## Choices of the runner

1. **Seed** `<N>`: an integer that is none of `20260930`, `20261001`, `20261002`, `424242`, `5`
   and that was not tried before. The scorer refuses the three suite seeds and any seed already in
   `eval/history.json`.
2. **Styles** `<STYLES>`: a comma-separated list. At least three of the six reserved styles
   above; any of the other styles may be added (`flow`, `double-quoted`, `comments`, `key-order`,
   `indent-4`, `crlf`, `yml-ext`, `host-case`, `prefix-slash`, `entrypoints-list`).
3. **Faults per instance** `<K>`: `1` or `2`.
4. **Ten hand-written instances** in a folder `<DIR>` outside the repository, following
   `docs/FORMAT.md`: one sub-folder per instance and one `labels.jsonl` with a line per instance.
   At least three of them clean, and at least one fault of a kind the generator does not plant in
   that shape. The label is written from what was done to the instance, **before** the lab is run
   on it, and is not edited afterwards.

## The run

One command, once:

```bash
python eval/score.py --suite blind --seed <N> --runner "<who runs it>" --date <YYYY-MM-DD> \
    --styles <STYLES> --faults-per-instance <K> --handwritten <DIR>
```

It generates 240 instances from the seed, adds the ten hand-written ones, audits all of them,
prints the table, writes the `blind` entry of `eval/results.json` and appends the run - seed,
runner, date, commit, metrics - to `eval/history.json`. A second blind run is refused. The
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
