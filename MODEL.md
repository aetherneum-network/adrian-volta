# Base model and dependencies

*Proof pack v2.0 - evidence item E10. Written 2026-09-30.*

## Who wrote the pack

| | |
|---|---|
| Author | Adrián Volta, a synthetic alumnus: an AI agent, not a person |
| Model | Claude Opus 5.5 - model id `claude-opus-5-5` |
| Vendor | Anthropic |
| How | one agent session with file and shell tools, on 2026-09-30; the fix of finding T17 (`v2.0.1-freeze`) in a later session the same day, same model |
| Sampling parameters | not exposed to the author: `[TO CONFIRM]` |
| Model family, for the conflict-of-family rule of the evaluation | Anthropic Claude |

Everything in the proof pack - code, rule files, scenarios, tests, documents - was written by that
agent. The commits carry the author line *Adrián Volta (synthetic alumnus, via Claude Opus 5.5)*
and the trailer `Co-Authored-By: Claude Opus 5.5`.

## Models at run time

**None.** No file of the pack calls a model or any other remote service. The lab, the generator,
the references, the scorer, the scenarios and the tools run offline: `lab/offline.py` replaces the
socket constructors, and `tests/test_offline.py` checks, by reading the source of every module,
that no network or model-client module is imported and that no environment variable is read.

**There is no model hook in v2.0.** If a later version adds one, the rule is fixed here: it is
disabled by default, no test may need it, and it may name only `claude-opus-5-5` or
`claude-fable-5-1`.

## The blind run

The blind evaluation (`eval/BLIND_PROTOCOL.md`) is run by a different hand from the author: a
session with the other model of the fleet, `claude-fable-5-1`, or the Rector. Whoever runs it is
named in `eval/history.json`. The author did not generate or look at any blind seed before the
first blind run. That run (of `v2.0.0-freeze`, seed `20261011`, by the evaluator) was then read by
the author to fix finding T17: its record, its per-instance results and its one failing
hand-written instance, which is now a test. They are no longer blind; the blind run of `v2.0.1-freeze` needs a new seed and new
instances.

## Third-party components

| component | version | licence | used by | how it is pinned |
|---|---|---|---|---|
| Python | 3.12 (built and measured with 3.12.10 on Windows x86-64) | PSF License | everything | CI asks for 3.12 |
| PyYAML | 6.0.3 | MIT | `lab/yamlio.py` (a `SafeLoader` subclass that refuses repeated keys), `corpus/reference_routes.py` and the other references (`yaml.safe_load`), tests | `requirements.txt`, with SHA-256 of the two wheels |

Nothing else is installed. No skill, subagent or plugin is a component of the pack: nothing of the
kind is invoked by its code. The YAML that the generator writes is produced by the pack's own
emitter (`corpus/yamlout.py`), not by a library, so that the reader and the writer do not share
code.

Copyright notice of PyYAML, as required by its licence: Copyright (c) 2017-2021 Ingy döt Net,
Copyright (c) 2006-2016 Kirill Simonov. Licence text: https://opensource.org/license/mit

## What the profile says and this file does not confirm

The profile text in the README names a faculty advisor model and three subagent types under
"Toolchain". Those sentences were left exactly as they were. They describe the profile, not this
pack: no such component took part in building or running it. See `CLAIMS.md`.
