"""Postmortem of a FAILED audit, rendered from the report - the same report gives the same text.

Blameless by construction: the document cites facts and identifiers (file, item, rule, UTC
instants, hashes), never people. It is the postmortem of a *synthetic* incident in the lab;
it is not the postmortem of any real outage.
"""
from __future__ import annotations


def _what_failed(f: dict) -> str:
    d = f["detail"]
    cls = f["class"]
    if cls in ("health_probes_process", "service_down"):
        codes = ", ".join(str(s) for s in d["statuses"])
        return (f"route `{d['router']}` ({d['entrypoint']} `{d['host']}{d['path_prefix']}`, file `{d['route_file']}`) "
                f"answered {codes} for {d['failing_beats']} consecutive beats")
    if cls == "restart_loop":
        return f"service `{f['item']}` restarted {len(d['restarts'])} times inside the probe window"
    if cls in ("backup_truncated", "block_corrupt", "restore_mismatch", "repo_unreadable", "no_snapshot", "source_unreadable"):
        files = ", ".join(f"`{p}`" for p in d.get("mismatched_files", [])) or "none listed"
        return f"restore of snapshot `{f['item']}` from `{f['file']}` did not reproduce the source (files: {files})"
    if cls == "job_order_inverted":
        return f"the nightly chain ran the backup before the export (`{f['file']}`, {f['item']})"
    return f"{cls} at `{f['file']}` ({f['item']})"


def _what_said_fine(f: dict) -> str | None:
    d = f["detail"]
    if f["class"] == "health_probes_process":
        target = "the process" if d["health_kind"] == "process" else f"`{d['health_path']}` directly on the backend"
        return (f"healthcheck of kind `{d['health_kind']}` for service `{f['item']}` looked at {target} "
                f"and reported healthy at each failing beat")
    if f["class"] in ("backup_truncated", "block_corrupt") and d.get("registry_claimed_ok"):
        return f"an earlier registry entry recorded snapshot `{f['item']}` as restored successfully"
    return None


def render(report: dict) -> str:
    failed = [f for f in report["findings"] if f["verdict"] == "FAILED"]
    lines = [
        f"# Postmortem (synthetic) - {report['instance']}",
        "",
        f"*Data as of {report['as_of']} (UTC). Rendered by `lab/postmortem.py` from the audit report. "
        "Synthetic incident in a synthetic lab: companies, hosts and services are fictitious. "
        "Facts and identifiers only.*",
        "",
        "## Summary",
        "",
        f"- Verdict: **{report['verdict']}** ({len(failed)} failing finding{'s' if len(failed) != 1 else ''}, "
        f"{len(report['findings'])} in total).",
    ]
    for f in failed:
        lines.append(f"- What failed: {_what_failed(f)}.")
        fine = _what_said_fine(f)
        if fine:
            lines.append(f"- What said it was fine: {fine}.")
        lines.append(f"- Decided by: rule `{f['rule']}` -> class `{f['class']}` (severity rule `{f['severity_rule']}`).")
    lines += ["", "## Timeline (UTC)", "", "| time | source | fact |", "|---|---|---|"]
    rows = []
    for f in report["findings"]:
        for t, source, fact in f["detail"].get("timeline", []):
            rows.append((t, source, fact))
    rows.append((report["as_of"], "audit", f"audit run, verdict {report['verdict']}"))
    for t, source, fact in sorted(set(rows)):
        lines.append(f"| {t} | {source} | {fact} |")
    lines += ["", "## Evidence", ""]
    for f in report["findings"]:
        also = f" (also: {', '.join('`' + a + '`' for a in f['also'])})" if f["also"] else ""
        lines.append(f"- `{f['class']}` [{f['verdict']}] at `{f['file']}`{also}, item `{f['item']}`, rule `{f['rule']}`.")
    lines.append(f"- Rule files: " + ", ".join(f"`{k}` {v}" for k, v in sorted(report["rules"].items())) + ".")
    if report.get("routes", {}).get("provisional_sha256"):
        lines.append(f"- Route table as loaded: {report['routes']['count']} entries, SHA-256 `{report['routes']['provisional_sha256']}`.")
    drill = report.get("drill")
    if drill:
        lines.append(f"- Restore drill: repository `{drill['repo']}`, snapshot `{drill['snapshot']}`, result `{drill['result']}`, "
                     f"{drill['restored_count']} of {drill['source_count']} files restored, "
                     f"source manifest SHA-256 `{drill['source_manifest_sha256']}`.")
    fallback = report.get("fallback")
    if fallback:
        lines.append(f"- Fallback declared: repository `{fallback['repo']}`, snapshot `{fallback['snapshot']}`, result `{fallback['result']}`.")
    lines += ["", "## What the checks did not see", ""]
    blind = [_what_said_fine(f) for f in failed]
    blind = [b for b in blind if b]
    lines += [f"- {b[0].upper() + b[1:]}." for b in blind] or ["- Nothing reported as healthy contradicted the findings."]
    lines += ["", "## Follow-ups", "",
              "- Owner and date for each corrective action: [TO CONFIRM].",
              "- Re-run the audit after the correction; the verdict must change by a changed fact, not by an edited report.",
              ""]
    return "\n".join(lines)
