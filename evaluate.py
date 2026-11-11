# /// script
# requires-python = ">=3.10"
# dependencies = ["PyJWT[crypto]==2.15.1", "filelock==4.2.10"]
# ///
"""Run the five curated retrieval and grounded-answer checks."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from retrieval import FilingIndex, passage_reference

ROOT = Path(__file__).resolve().parent
QUESTIONS_PATH = ROOT / "evaluation" / "questions.json"


def amounts_millions(text):
    amounts = set()
    for match in re.finditer(r"\b(\d[\d,]*(?:\.\d+)?)\s*(million|billion|trillion)?\b", text, re.I):
        number = Decimal(match[1].replace(",", ""))
        unit = (match[2] or "million").lower()
        amounts.add(number * {"million": 1, "billion": 1000, "trillion": 1_000_000}[unit])
    return amounts


def page_groups_present(groups, pages):
    return all(set(group).intersection(pages) for group in groups)


def check_answer(case, result):
    answer = result.get("analysis", {})
    text = answer.get("answer", "")
    cited_pages = {item.get("pdf_page") for item in answer.get("evidence", [])}
    return {
        "no_error": "error" not in result,
        "expected_status": answer.get("status") == case["expected_status"],
        "expected_amounts": set(map(Decimal, case["expected_amounts_millions"])).issubset(amounts_millions(text)),
        "expected_patterns": all(re.search(pattern, text, re.I) for pattern in case["expected_patterns"]),
        "cited_expected_pages": page_groups_present(case["required_page_groups"], cited_pages),
        "missing_information_named": case["expected_status"] != "insufficient_evidence" or bool(answer.get("missing_information")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--chatgpt", action="store_true", help="Run live answer checks through your ChatGPT connection")
    mode.add_argument("--retrieval-only", action="store_true", help="Run offline retrieval checks")
    parser.add_argument("--model", help="Optional model slug for live ChatGPT checks")
    parser.add_argument("--output", type=Path, help="Save the evaluation report here")
    args = parser.parse_args()
    cases = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    index = FilingIndex()
    records = []
    for case in cases:
        passages = index.retrieve(case["question"])
        pages = {passage["pdf_page"] for passage in passages}
        retrieval_pass = page_groups_present(case["required_page_groups"], pages) if case["required_page_groups"] else None
        record = {
            "id": case["id"], "question": case["question"],
            "retrieval_pass": retrieval_pass,
            "retrieved_passages": [passage_reference(passage, index.corpus["source"]) for passage in passages],
        }
        if args.chatgpt:
            command = [sys.executable, str(ROOT / "analyze.py"), "--chatgpt", case["question"]]
            if args.model:
                command.extend(["--model", args.model])
            try:
                completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=180)
                if completed.returncode != 0:
                    raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
                result = json.loads(completed.stdout)
                record["checks"] = check_answer(case, result)
                record["result"] = result
                record["passed"] = retrieval_pass is not False and all(record["checks"].values())
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                record["error"] = str(error)
                record["passed"] = False
        else:
            record["passed"] = retrieval_pass
        records.append(record)
        status = "SKIP (no known supporting passage)" if record["passed"] is None else ("PASS" if record["passed"] else "FAIL")
        print(f"{case['id']}: {status}", flush=True)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_chatgpt" if args.chatgpt else "offline_retrieval",
        "source": index.corpus["source"],
        "questions_sha256": hashlib.sha256(QUESTIONS_PATH.read_bytes()).hexdigest(),
        "passed": sum(record["passed"] is True for record in records),
        "total": sum(record["passed"] is not None for record in records),
        "skipped": sum(record["passed"] is None for record in records),
        "scope": "Five curated checks for expected amounts, status, and cited pages. Manual review is required for broader credit conclusions.",
        "cases": records,
    }
    output = args.output or ROOT / "evaluation" / ("night2-results.json" if args.chatgpt else "night2-retrieval.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['passed']}/{report['total']} passed. Report: {output}")
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    raise SystemExit(main())
