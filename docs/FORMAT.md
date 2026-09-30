# Instance and label format

*Proof pack v2.0. Everything described here is synthetic: invented topologies of three fictitious
companies under `.example` domains. This page is what a person needs to write an instance by hand
(the blind protocol asks for ten of them) and to read what the lab and the references compute.*

## 1. An instance is a folder

```
<instance>/
  instance.json            required
  topology.yaml            required when the scope has "routing" or "health"
  routes/**/*.yaml|*.yml   one file per service; sub-folders are read too
  trace.jsonl              required when the scope has "health" or "backup"
  policy/backup.yaml       required when the scope has "backup"
  source/                  the tree that the backup protects
  repo_a/  repo_b/         primary and secondary repository
  drills.jsonl             registry of past restore drills (may be absent)
```

All text files are UTF-8 with LF line endings. Relative paths use forward slashes. The lab never
writes inside an instance.

### `instance.json`

```json
{"instance_id": "hand-0001", "as_of": "2026-09-14T07:00:00Z", "scope": ["routing", "health", "backup"]}
```

`as_of` is the instant the audit is about. It must be explicit UTC (`YYYY-MM-DDTHH:MM:SSZ`): the
wall clock is never read, and a value without `Z` makes the instance unreadable (verdict `FAILED`).
`scope` lists which parts exist; without it all three are expected.

### `topology.yaml`

```yaml
company: "Cartiera Valdora S.p.A."
domain: valdora.example
entrypoints:
  public: {plane: public, network: edge-public}
  admin:  {plane: admin,  network: edge-admin}
networks:
  edge-public: {plane: public}
  edge-admin:  {plane: admin}
  app-1:       {plane: internal}
services:
  status:
    plane: public            # public | admin
    port: 9000
    networks: [edge-public, app-1]
    restart: {policy: always}            # always | on-failure | unless-stopped
    health: {kind: process}              # process | http (http needs "path")
```

Keys starting with `x-` are ignored everywhere; any other unknown key makes the file unreadable. A
repeated key is an error, not "last one wins".

### `routes/<service>.yaml`

```yaml
service: status
routers:
  - name: status-main
    entrypoint: public            # or "entrypoints: [public, admin]"
    host: status.valdora.example
    path_prefix: /
    backend: status-backend
    priority: 0                   # optional, default 0
    enabled: true                 # optional, default true
backends:
  status-backend: {target: "status:9000"}
```

Definitions used by the lab **and** by the references:

* a host is compared lower-case, without a trailing dot; a prefix without trailing slashes (except `/`);
* a prefix matches a path when it is `/`, or equals the path, or is followed by `/` in the path;
* among the matching entries of one entrypoint and host the winner is the highest `priority`, then
  the longest prefix, then the file read first (files are read in sorted order of their relative path);
* two **enabled** routers with the same entrypoint, host and prefix are a duplicate, whatever their
  priority: one of them is shadowed (`route_duplicate_shadow`, both files are named);
* a router whose backend is missing, or whose target service is not in the topology, is
  `route_missing_service`; a target port different from the service port is `route_wrong_port`;
* a router on a public entrypoint whose target has `plane: admin`, or an admin service attached to a
  network of the public plane, is `admin_on_public`;
* a disabled router is skipped; a file that is not a route file (`*.txt`, `*.yaml.disabled`) is not read.

### `trace.jsonl`

One JSON object per line. Simulation events carry `t` in explicit UTC:

```json
{"kind": "proc",    "t": "2026-09-14T00:00:00Z", "service": "status", "state": "up"}
{"kind": "serve",   "t": "2026-09-14T00:00:00Z", "service": "status", "map": {"/": 200, "/healthz": 200}}
{"kind": "restart", "t": "2026-09-14T06:40:10Z", "service": "status", "down_s": 20}
{"kind": "host",    "t": "2026-10-24T20:00:00Z", "state": "off"}
{"kind": "job", "run": "2026-09-14", "step": "backup_a", "start": "2026-09-14T02:31:10Z", "end": "2026-09-14T02:31:40Z", "status": "ok"}
```

A service with no `proc` statement is not up. A `serve` map replaces the previous one; a path that
is not in the map answers 404; a stopped process answers 503; an unknown target 502. Job timestamps
are *classified* before use: `utc`, `offset` (explicit numeric offset, converted), `naive` (no
zone: the instant is unknown and is not guessed) or `invalid`.

The probe runs 30 beats of 60 seconds ending at `as_of`. At each beat it sends one request per
route, through the route table, and separately asks what the *declared* healthcheck says. Rules
(`rules/health.json`): three restarts inside ten minutes are a `restart_loop`; two consecutive beats
in which a route fails while the declared healthcheck says healthy are `health_probes_process`
(located at `topology.yaml`, item = the service); two consecutive failing beats otherwise are
`service_down`.

### `policy/backup.yaml`

```yaml
schedule: {at: "02:30", tz: UTC}      # quote the time: unquoted 12:30 is the number 750 in YAML 1.1
chain: [export, backup_a, backup_b, verify, prune]
source: source
export_path: source/export/orders-export.csv
repos:
  a: {path: repo_a}
  b: {path: repo_b, max_lag_hours: 26}
retention: {keep_last: 7}
```

`tz` must be one of `UTC`, `Etc/UTC`, `Z`. Anything else is reported as `tz_mixed` and the nightly
windows are not evaluated. A window is covered when a run starts between `at` and `at` + 90 minutes.

### A repository

```
repo_a/pack.bin                      blocks, appended
repo_a/index.json                    {"format": 1, "block_size": 1024, "blocks": {"<sha256>": [offset, length]}}
repo_a/snapshots/<YYYYMMDDTHHMMSSZ>.json
repo_a/superseded/<id>.json          manifests moved by retention (never deleted)
```

```json
{"format": 1, "id": "20260912T023110Z", "created": "2026-09-12T02:31:10Z", "source_file_count": 2, "total_bytes": 1536,
 "files": [{"path": "dati/Relazione qualità.txt", "size": 1536, "sha256": "<sha256 of the file>",
            "blocks": ["<sha256 of block 1>", "<sha256 of block 2>"]}]}
```

The easiest way to write a repository by hand is to let the pack do it on a source tree
(`lab.backup.backup(source_dir, repo_dir, created)`), then damage it with a hex editor.

### `drills.jsonl`

Append-only registry: each line carries `prev` (the `hash` of the line before, or 64 zeros) and
`hash` (SHA-256 of the line without `hash`, in canonical compact JSON). A registry whose chain does
not verify vouches for nothing (`registry_tampered`).

## 2. Verdicts

| verdict | exit code | classes |
|---|---|---|
| `OK` | 0 | no finding |
| `ALERT` | 1 | `repo_b_stale`, `repo_b_unreadable`, `window_missed`, `tz_mixed` |
| `BLOCKED` | 2 | `route_missing_service`, `route_duplicate_shadow`, `admin_on_public`, `route_wrong_port`, `route_unreadable`, `topology_unreadable`, `policy_unreadable`, `retention_deletes_only_valid`, `retention_unreadable` |
| `FAILED` | 3 | `health_probes_process`, `restart_loop`, `service_down`, `backup_truncated`, `block_corrupt`, `job_order_inverted`, `restore_mismatch`, `repo_unreadable`, `no_snapshot`, `source_unreadable`, `registry_tampered`, `trace_unreadable`, `instance_unreadable`, and any class no rule knows |

The verdict of a run is the most severe one among its findings (`rules/severity.json`).

## 3. Gold labels (`labels.jsonl`, one instance per line)

```json
{"instance": "hand-0001", "clean": false, "verdict": "FAILED", "decoys": [],
 "faults": [{"class": "block_corrupt", "variant": "hand", "verdict": "FAILED",
             "file": "repo_a", "also": [], "items": ["20260921T013539Z"],
             "mismatched_files": ["documenti/qualità/05 Contatti fornitori.txt"]}]}
```

| class | `file` | `also` | `items` | `mismatched_files` |
|---|---|---|---|---|
| `route_missing_service` | the route file | `[]` | `[router name, ...]` (every router of the file that points to the missing service; one match is enough) | `[]` |
| `route_duplicate_shadow` | one of the two route files | `[the other one]` | `null` (either router) | `[]` |
| `admin_on_public` | the route file, or `topology.yaml` for a network | `[]` | `[router name]` or `[service]` | `[]` |
| `health_probes_process` | `topology.yaml` | `[]` | `[service]` | `[]` |
| `restart_loop` | `trace.jsonl` | `[]` | `[service]` | `[]` |
| `repo_b_stale` | the path of repository b | `[]` | `[newest snapshot id of b]`, or `[null]` when b has no snapshot | `[]` |
| `backup_truncated`, `block_corrupt` | the path of repository a | `[]` | `[newest snapshot id of a]` | every file that differs from the source after a restore, sorted |
| `job_order_inverted` | `policy/backup.yaml` (item `chain`) or `trace.jsonl` (item = run id) | `[]` | `["chain"]` or `[run id]` | the files that differ (usually the export) |
| `window_missed` | `trace.jsonl` | `[]` | `[date of the first missed window, YYYY-MM-DD]` | `[]` |
| `retention_deletes_only_valid` | `policy/backup.yaml` | `[]` | `["retention"]` | `[]` |
| `tz_mixed` | `policy/backup.yaml` (item `schedule`) or `trace.jsonl` (item `job-timestamps`) | `[]` | as on the left | `[]` |

A clean instance has `"clean": true, "verdict": "OK", "faults": []`. The label is written from what
the author of the instance *did* to it, before the lab is run on it - never from the lab's output.

Every key shown above is required, in the label and in each fault; `variant` is free text (`hand`
for a hand-written instance) and `decoys` may be left out. A hand-written fault may name a class
that the generator does not plant: use the class names of section 2. The scorer checks the labels
before it generates or audits anything and refuses a file it cannot use; it never completes or
repairs a label. Instance names must not start with `blind-`, which is the prefix of the generated
instances of a blind run.

A fault is **detected** when a finding of its class exists, and **localised** when class, file(s)
and item all match. A finding that matches no fault is **spurious**.

## 4. What the three references check, and what they do not

| reference | checks, without any code of the lab | does not cover |
|---|---|---|
| `corpus/reference_routes.py` | every rule of every route file, by brute force over the universe of requests: ghosts, duplicates, admin targets on public entrypoints | - |
| `corpus/reference_reach.py` | who can reach whom on the world matrix; a replay of the trace for silent failures and restart loops | - |
| `corpus/reference_hashes.py` | a naive restore of the newest snapshot of each repository compared with a direct SHA-256 of the source tree; age of the secondary | - |

`window_missed`, `tz_mixed`, `retention_deletes_only_valid` and the policy-order variant of
`job_order_inverted` have **no independent reference**: their gold is the fault plan only. This is
stated next to the numbers in the README.

An instance whose `scope` leaves a part out is judged by the references for the parts it has, and
is not counted among the instances fully covered by the references. When a part is in scope but
its reference cannot read it, nobody vouches for that part: if the lab nevertheless called a
restore successful, or accepted the route table, the scorer counts a never-event.
