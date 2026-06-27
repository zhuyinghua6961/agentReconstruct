#!/usr/bin/env python3
"""Run LFP-Bench draft against FastQA /api/fast/ask and score basic metrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("pip install httpx", file=sys.stderr)
    raise

REFUSE_WORDS = ("不足", "未报道", "未找到", "无法", "没有", "未提供", "insufficient", "not reported")


def norm_doi(d: str) -> str:
    return d.replace("/", "_").strip()


def doi_hit(refs: list, gold_dois: list[str]) -> bool:
    blob = json.dumps(refs, ensure_ascii=False)
    return any(norm_doi(d) in blob for d in gold_dois)


def numeric_ok(answer: str, check: str) -> bool | None:
    if not check.startswith("numeric:"):
        return None
    nums = [n for n in check.split(":", 1)[1].split(",") if n]
    return all(n in answer for n in nums)


def refuse_ok(answer: str) -> bool:
    return any(w in answer for w in REFUSE_WORDS)


def run_one(client: httpx.Client, url: str, question: str) -> dict:
    payload = {
        "question": question,
        "requested_mode": "fast",
        "route": "kb_qa",
        "kb_enabled": True,
        "use_generation_driven": True,
    }
    r = client.post(url, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=str(Path(__file__).parent / "lfp_bench_v1_draft.jsonl"))
    ap.add_argument("--url", default="http://127.0.0.1:8008/api/fast/ask")
    ap.add_argument("--split", default="", help="dev | test_i | test_ii")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.bench).read_text().splitlines() if l.strip()]
    if args.split:
        rows = [r for r in rows if r.get("split") == args.split]
    if args.limit:
        rows = rows[: args.limit]

    results = []
    with httpx.Client() as client:
        for i, q in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {q['id']} ...", flush=True)
            try:
                resp = run_one(client, args.url, q["question"])
            except Exception as e:
                results.append({**q, "error": str(e)})
                continue
            ans = resp.get("content") or resp.get("answer") or ""
            refs = resp.get("references") or []
            rec = doi_hit(refs, q.get("gold_doi") or [])
            row = {
                **q,
                "answer": ans[:2000],
                "references": refs,
                "recall_hit": rec,
            }
            if q.get("answerable", True):
                row["numeric_ok"] = numeric_ok(ans, q.get("check", ""))
            else:
                row["refuse_ok"] = refuse_ok(ans)
            results.append(row)

    n = len(results)
    ans_rows = [r for r in results if r.get("answerable", True) and "error" not in r]
    unans_rows = [r for r in results if not r.get("answerable", True) and "error" not in r]
    recall = sum(1 for r in ans_rows if r.get("recall_hit")) / max(len(ans_rows), 1)
    refuse = sum(1 for r in unans_rows if r.get("refuse_ok")) / max(len(unans_rows), 1)
    print(f"\nRecall@refs: {recall:.1%} ({len(ans_rows)} answerable)")
    print(f"Refuse rate: {refuse:.1%} ({len(unans_rows)} unanswerable)")

    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
