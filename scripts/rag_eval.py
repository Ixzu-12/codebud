#!/usr/bin/env python3
"""
rag_eval.py -- Measure how well project search finds the right files.

Runs every question in scripts/rag_eval.json against the project index
(built beforehand with `codebud index`) and reports, per ranking variant:

  recall@k  share of questions with a correct file among the top k results
  MRR       mean of 1/rank of the first correct file (0 when none is found)
  ms/query  mean search time

for all questions and separately for "concept" questions (plain words) and
"identifier" questions (naming a symbol). Questions with no correct file in
the top k are listed with what came back instead, unless --quiet.

Usage (from the repository root, Ollama running, index built):

    codebud index
    python scripts/rag_eval.py                       # all variants, k = 5
    python scripts/rag_eval.py --variants hybrid -k 3
    python scripts/rag_eval.py --json                # machine-readable

Requirements: the dev environment (make install-dev), numpy, and the
embedding model the index was built with.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from codebud.rag import search  # noqa: E402
from codebud.rag.indexer import project_root  # noqa: E402

QUESTIONS = Path(__file__).with_name("rag_eval.json")

# name -> keyword arguments for search.search()
VARIANTS = {
    "vector": {"lists": ("vector",), "test_weight": 1.0},
    "keyword": {"lists": ("keyword",), "test_weight": 1.0},
    "keyword+symbol": {"lists": ("keyword", "symbol"), "test_weight": 1.0},
    "hybrid": {"test_weight": 1.0},
    "hybrid tests*0.5": {"test_weight": 0.5},
    "hybrid tests*0.25": {"test_weight": 0.25},
    "hybrid (default)": {},
}


def evaluate(root: Path, questions: list[dict], options: dict, k: int) -> dict:
    """Run every question; return per-question ranks and timings."""
    rows = []
    for item in questions:
        started = time.monotonic()
        hits = search.search(root, item["q"], top_k=k, **options)
        elapsed = time.monotonic() - started
        paths = [h.chunk.path for h in hits]
        rank = next((i + 1 for i, p in enumerate(paths) if p in item["files"]), None)
        rows.append({**item, "rank": rank, "got": paths, "seconds": elapsed})
    return {"rows": rows}


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "recall": 0.0, "mrr": 0.0, "ms": 0.0}
    found = [r for r in rows if r["rank"]]
    return {
        "n": len(rows),
        "recall": len(found) / len(rows),
        "mrr": sum(1 / r["rank"] for r in found) / len(rows),
        "ms": 1000 * sum(r["seconds"] for r in rows) / len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-k", type=int, default=5, help="results per question (default 5)")
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(VARIANTS),
        default=list(VARIANTS),
        help="ranking variants to compare (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--quiet", action="store_true", help="do not list missed questions")
    args = parser.parse_args()

    root = project_root()
    questions = json.loads(QUESTIONS.read_text())["questions"]
    # Warm-up: load the vector matrix and the embedding model once.
    search.search(root, "warm up", top_k=1)

    results = {}
    for name in args.variants:
        rows = evaluate(root, questions, VARIANTS[name], args.k)["rows"]
        results[name] = {
            "all": summarise(rows),
            "concept": summarise([r for r in rows if r["kind"] == "concept"]),
            "identifier": summarise([r for r in rows if r["kind"] == "identifier"]),
            "missed": [{"q": r["q"], "got": r["got"]} for r in rows if not r["rank"]],
        }

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print(f"{len(questions)} questions, k = {args.k}, project {root}\n")
    print(f"{'variant':<20}{'recall@k':>10}{'MRR':>7}{'concept':>10}{'ident.':>8}{'ms/q':>8}")
    for name, r in results.items():
        a = r["all"]
        print(
            f"{name:<20}{a['recall']:>10.2f}{a['mrr']:>7.2f}"
            f"{r['concept']['recall']:>10.2f}{r['identifier']['recall']:>8.2f}{a['ms']:>8.0f}"
        )
    if not args.quiet:
        for name, r in results.items():
            if r["missed"]:
                print(f"\nmissed by {name}:")
                for m in r["missed"]:
                    print(f"  {m['q']}\n    got: {', '.join(m['got'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
