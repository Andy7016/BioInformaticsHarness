from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


MAX_SEQUENCE_LENGTH = 256
MAX_PAIRS = 4096
SCOPE = (
    "Full-length Levenshtein distance over the declared candidate/reference pool only. "
    "It does not run BLAST or local alignment, does not convert distance to identity, "
    "and never reverse-complements automatically."
)


def base_result(payload: dict | None, status: str = "unknown") -> dict:
    layer = "provided_input"
    if isinstance(payload, dict):
        layer = str(payload.get("fixture_kind", "provided_input"))
    return {
        "schema": "domain-validation-nearest-reference-v1",
        "status": status,
        "actual_scope": SCOPE,
        "source_layer": layer,
        "scientific_acceptance": "not_assessed",
        "limits": {"max_sequence_length": MAX_SEQUENCE_LENGTH, "max_pairs": MAX_PAIRS},
        "errors": [],
        "results": [],
        "coverage": {},
    }


def sequence_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def validate_entry(entry: Any, alphabet: set[str], kind: str, index: int, max_length: int) -> tuple[dict | None, str | None]:
    if not isinstance(entry, dict):
        return None, f"{kind}[{index}] must be an object"
    entry_id = entry.get("id")
    sequence = entry.get("sequence")
    if not isinstance(entry_id, str) or not entry_id.strip() or not isinstance(sequence, str) or not sequence:
        return None, None
    if any(char not in alphabet for char in sequence):
        return None, f"{kind}[{index}] {entry_id} contains a letter outside the declared alphabet"
    if len(sequence) > max_length:
        return {"id": entry_id, "sequence": sequence, "over_budget": True}, None
    return {"id": entry_id, "sequence": sequence, "over_budget": False}, None


def compute(payload: Any) -> tuple[dict, int]:
    result = base_result(payload if isinstance(payload, dict) else None)
    if not isinstance(payload, dict):
        result["errors"].append("input must be a JSON object")
        return result, 2

    required = ("alphabet", "direction_policy", "reference_pool", "candidates", "references")
    missing = [key for key in required if key not in payload]
    if missing:
        result["errors"].append({"kind": "missing_required_input", "fields": missing})
        return result, 2

    alphabet_name = payload["alphabet"]
    alphabets = {"DNA": set("ACGT"), "RNA": set("ACGU")}
    if alphabet_name not in alphabets:
        result["errors"].append("alphabet must be DNA or RNA")
        return result, 1
    direction_policy = payload["direction_policy"]
    if direction_policy != "forward_only":
        result["errors"].append(
            {
                "kind": "unsupported_direction_policy",
                "received": direction_policy,
                "required": "forward_only",
            }
        )
        return result, 2
    pool = payload["reference_pool"]
    if not isinstance(pool, dict) or pool.get("kind") not in ("natural", "template"):
        result["errors"].append("reference_pool.kind must be natural or template")
        return result, 1
    if not isinstance(pool.get("identity"), str) or not pool["identity"].strip():
        result["errors"].append("reference_pool.identity must identify the declared pool")
        return result, 2
    candidates = payload["candidates"]
    references = payload["references"]
    if not isinstance(candidates, list) or not isinstance(references, list):
        result["errors"].append("candidates and references must be lists")
        return result, 1
    if not candidates:
        result["errors"].append({"kind": "missing_candidates"})
        return result, 2
    if not references:
        result["coverage"] = {
            "candidate_count": len(candidates),
            "reference_count": 0,
            "queried_candidate_count": 0,
            "unsearched_candidate_count": len(candidates),
            "reference_pool_empty": True,
        }
        result["errors"].append({"kind": "empty_reference_pool", "message": "no novelty claim is emitted"})
        return result, 2

    normalized_candidates = []
    normalized_references = []
    unknown_entries = []
    seen_candidate_ids: set[str] = set()
    seen_reference_ids: set[str] = set()
    for index, entry in enumerate(candidates):
        item, error = validate_entry(entry, alphabets[alphabet_name], "candidates", index, MAX_SEQUENCE_LENGTH)
        if error:
            result["errors"].append(error)
            continue
        if item is None:
            unknown_entries.append({"kind": "missing_candidate_input", "index": index})
            continue
        if item["id"] in seen_candidate_ids:
            result["errors"].append(f"duplicate candidate id: {item['id']}")
        seen_candidate_ids.add(item["id"])
        normalized_candidates.append(item)
    for index, entry in enumerate(references):
        item, error = validate_entry(entry, alphabets[alphabet_name], "references", index, MAX_SEQUENCE_LENGTH)
        if error:
            result["errors"].append(error)
            continue
        if item is None:
            unknown_entries.append({"kind": "missing_reference_input", "index": index})
            continue
        if item["id"] in seen_reference_ids:
            result["errors"].append(f"duplicate reference id: {item['id']}")
        seen_reference_ids.add(item["id"])
        normalized_references.append(item)

    if result["errors"] and any(not isinstance(error, dict) for error in result["errors"]):
        return result, 1
    if not normalized_candidates or not normalized_references:
        result["coverage"] = {
            "candidate_count": len(candidates),
            "reference_count": len(references),
            "queried_candidate_count": 0,
            "unsearched_candidate_count": len(candidates),
            "missing_entry_count": len(unknown_entries),
        }
        result["errors"].extend(unknown_entries)
        return result, 2

    pair_count = len(normalized_candidates) * len(normalized_references)
    result["limits"]["pair_count"] = pair_count
    if pair_count > MAX_PAIRS:
        result["coverage"] = {
            "candidate_count": len(candidates),
            "reference_count": len(references),
            "queried_candidate_count": 0,
            "unsearched_candidate_count": len(candidates),
            "pair_budget_exceeded": True,
        }
        result["errors"].append({"kind": "pair_budget_exceeded", "pair_count": pair_count})
        return result, 2
    over_budget = [item["id"] for item in normalized_candidates + normalized_references if item["over_budget"]]
    if over_budget:
        result["coverage"] = {
            "candidate_count": len(candidates),
            "reference_count": len(references),
            "queried_candidate_count": 0,
            "unsearched_candidate_count": len(candidates),
            "over_budget_ids": over_budget,
        }
        result["errors"].append({"kind": "sequence_length_budget_exceeded", "ids": over_budget})
        return result, 2

    for query in normalized_candidates:
        pair_rows = []
        for reference in normalized_references:
            distance = sequence_distance(query["sequence"], reference["sequence"])
            pair_rows.append(
                {
                    "reference_id": reference["id"],
                    "query_length": len(query["sequence"]),
                    "reference_length": len(reference["sequence"]),
                    "distance": distance,
                    "normalized_edit_fraction": distance / max(len(query["sequence"]), len(reference["sequence"])),
                }
            )
        minimum = min(row["distance"] for row in pair_rows)
        nearest = [row for row in pair_rows if row["distance"] == minimum]
        result["results"].append(
            {
                "candidate_id": query["id"],
                "query_length": len(query["sequence"]),
                "nearest_reference_ids": [row["reference_id"] for row in nearest],
                "minimum_distance": minimum,
                "nearest_pairs": nearest,
                "comparison_label": "template_distance" if pool["kind"] == "template" else "natural_pool_distance",
            }
        )

    result["reference_pool"] = {
        "kind": pool["kind"],
        "identity": pool["identity"],
        "direction_policy": direction_policy,
        "alphabet": alphabet_name,
    }
    result["coverage"] = {
        "candidate_count": len(candidates),
        "reference_count": len(references),
        "queried_candidate_count": len(result["results"]),
        "unsearched_candidate_count": len(candidates) - len(result["results"]),
        "missing_entry_count": len(unknown_entries),
    }
    result["errors"].extend(unknown_entries)
    result["status"] = "unknown" if unknown_entries else "pass"
    return result, 2 if unknown_entries else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded full-length nearest-reference arithmetic")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result = base_result(None)
        result["errors"].append({"kind": "input_unreadable", "detail": str(exc)})
        code = 2
    else:
        result, code = compute(payload)
    if code == 1:
        result["status"] = "fail"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
