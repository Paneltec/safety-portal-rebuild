"""Write docs/PARITY_REPORT.md from the two ledger files and the "Not verified" list in PROGRESS.md.

    python3 docs/ledger/parity_report.py "<pytest result line>" "<walkthrough result line>"

The two arguments are the test results at the time of writing, copied from the runs. Nothing in the
report is typed by hand apart from the fixed explanations in this file.
"""
import csv
import datetime as dt
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
LABELS = {"IMPLEMENTED_TESTED": "Built and tested", "EXPLICITLY_UNAVAILABLE": "Deliberately unavailable (answers with a plain refusal)", "HISTORICAL_COMPATIBILITY": "Old address kept working",
          "HISTORICAL": "Removed in the original; not rebuilt", "NOT_STARTED": "Not built", "PARTIAL": "Partly built"}


def main() -> None:
    pytest_line = sys.argv[1] if len(sys.argv) > 1 else "not supplied"
    e2e_line = sys.argv[2] if len(sys.argv) > 2 else "not supplied"
    with (HERE / "endpoint_coverage.csv").open(encoding="utf-8-sig", newline="") as fh:
        endpoints = list(csv.reader(fh))[1:]
    with (HERE / "feature_coverage.csv").open(encoding="utf-8-sig", newline="") as fh:
        features = list(csv.reader(fh))[1:]
    by_status = Counter(r[7] for r in endpoints)
    baseline = [r for r in endpoints if r[2] != "(new)" and r[3] != ""]
    new = [r for r in endpoints if r not in baseline]
    by_module: dict[str, Counter] = defaultdict(Counter)
    for r in endpoints:
        by_module[r[2] or "(new)"][r[7]] += 1
    progress = (DOCS / "PROGRESS.md").read_text(encoding="utf-8")
    not_verified = progress.split("## Not verified", 1)[1].split("\n## ", 1)[0].strip() if "## Not verified" in progress else ""

    out = ["# Parity report", "",
           f"Generated {dt.date.today().isoformat()} by `docs/ledger/parity_report.py` from `endpoint_coverage.csv` and `feature_coverage.csv`. "
           "It compares this rebuild with the written reconstruction pack. The original software was never seen, run or copied.", "",
           "## In one paragraph", "",
           f"The pack lists {len(features)} required capabilities. {sum(1 for f in features if f[3] == 'IMPLEMENTED_TESTED')} is recorded as fully built, "
           f"{sum(1 for f in features if f[3] == 'PARTIAL')} as partly built (the working core is there, with named gaps), and "
           f"{sum(1 for f in features if f[3] == 'NOT_STARTED')} as not built. Of the {len(endpoints)} server operations in the ledger, {by_status['IMPLEMENTED_TESTED']} are built with tests, "
           f"{by_status['EXPLICITLY_UNAVAILABLE']} deliberately answer with a refusal, {by_status['HISTORICAL'] + by_status['HISTORICAL_COMPATIBILITY']} are historical, and "
           f"{by_status['NOT_STARTED']} are not built. \"Partly built\" is the honest label for most areas: this is a working system with real gaps, not a finished copy.", "",
           "## Test results when this report was written", "",
           f"- Automated tests (server, against PostgreSQL): {pytest_line}",
           f"- Browser walkthrough (`e2e/run.sh`): {e2e_line}",
           "- These are the results of the runs named above. They do not cover anything in the \"Not verified\" list below.", "",
           "## Server operations by state", "", "| State | Operations |", "|---|---|",
           *[f"| {LABELS.get(k, k)} | {v} |" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])], "",
           f"{len(new)} of these operations are new in this rebuild (they have no counterpart in the pack), usually because several original operations were replaced by one.", "",
           "## Required capabilities", "", "| Id | Area | State | What is built, and what is not |", "|---|---|---|---|",
           *[f"| {f[0]} | {f[1]} | {LABELS.get(f[3], f[3])} | {(f[5] or f[2]).replace('|', '/')} |" for f in features], "",
           "## Server operations by area of the original", "", "Areas with nothing built are listed too, so the gaps are visible.", "",
           "| Area (original module) | Built and tested | Refused on purpose | Historical | Not built |", "|---|---|---|---|---|",
           *[f"| {module} | {c['IMPLEMENTED_TESTED']} | {c['EXPLICITLY_UNAVAILABLE']} | {c['HISTORICAL'] + c['HISTORICAL_COMPATIBILITY']} | {c['NOT_STARTED']} |"
             for module, c in sorted(by_module.items(), key=lambda kv: (-kv[1]['NOT_STARTED'], kv[0]))], "",
           "## Not verified", "", "Built, but not proven in the conditions it will be used in:", "", not_verified, "",
           "## Things this rebuild will not do, by design", "",
           "- Send a payment, lodge STP, submit super, or email or text a payslip. The pay area is a test workbench and says so on every screen.",
           "- Send any email or text while the server's safe mode lock is on (the default).",
           "- Restore a backup over the running portal, reset or bulk-delete test data, or change the activity log.",
           "- Show a simulated connection as a live one.",
           "- Offer an Android or iPhone app download.", "",
           "## Where to look next", "",
           "- `docs/PROGRESS.md`: what each stage delivered and what is left over.",
           "- `docs/ADR-001-architecture.md`: every decision and deviation, numbered.",
           "- `docs/ledger/endpoint_coverage.csv`: one row per server operation with its test evidence.",
           "- `docs/PERFORMANCE.md`: timings on a synthetic dataset.",
           "- `docs/BACKUP_RESTORE.md`, `docs/ADMIN_GUIDE.md`, `docs/WORKER_GUIDE.md`.", ""]
    (DOCS / "PARITY_REPORT.md").write_text("\n".join(out), encoding="utf-8")
    print(f"wrote PARITY_REPORT.md: {len(features)} capabilities, {len(endpoints)} operations")


if __name__ == "__main__":
    main()
