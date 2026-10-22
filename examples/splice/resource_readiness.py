#!/usr/bin/env python3
"""Consume raw UCI Splice inputs for resource-readiness only; no model execution."""

from __future__ import annotations

import argparse
import collections
import hashlib
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import zipfile

PLAN_SCHEMA = "research-support.resource-gap-plan.v1"
ACQ_SCHEMA = "research-support.resource-acquisition.v1"
OUTCOME_SCHEMA = "research-support.splice-resource-readiness.v1"
CON_SCHEMA = "research-support.resource-consumption.v1"
EXPECTED_CONTEXT = {"study_id": "splice", "iteration_id": "resource-readiness-01",
                    "task_id": "qualify-biological-evaluation-units"}
MAX_META_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 10 * 1024 * 1024
ENTITY_WORDS = re.compile(
    r"accession|gene[ _-]?(?:id|symbol|name)|family|assembly|chromosome|strand|locus|coordinate",
    re.I,
)


class InputError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def relpath(root: Path, raw: object, label: str, must_exist: bool) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise InputError(f"{label} must be a non-empty root-relative path")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise InputError(f"{label} must stay root-relative: {raw!r}")
    path = (root / candidate).resolve(strict=must_exist)
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise InputError(f"{label} resolves outside --root: {raw!r}") from exc
    if must_exist and not path.is_file():
        raise InputError(f"{label} is not a file: {raw!r}")
    return path, rel


def read_json(root: Path, raw_path: str, label: str) -> tuple[dict, bytes]:
    path, _ = relpath(root, raw_path, label, True)
    if path.stat().st_size > MAX_META_BYTES:
        raise InputError(f"{label} exceeds the {MAX_META_BYTES}-byte limit")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"{label} is not UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"{label} must contain a JSON object")
    return value, raw


def context_ids(context: dict) -> dict[str, str]:
    if context.get("schema") != "research-support.task-context.v1":
        raise InputError(f"unsupported task-context schema: {context.get('schema')!r}")
    scopes = [context] + [context[key] for key in ("spec", "identity", "current", "scope")
                           if isinstance(context.get(key), dict)]
    aliases = {"study_id": ("study_id", "study"),
               "iteration_id": ("iteration_id", "iteration"), "task_id": ("task_id",)}
    result = {}
    for key, names in aliases.items():
        values = {scope[name].strip() for scope in scopes for name in names
                  if isinstance(scope.get(name), str) and scope[name].strip()}
        if len(values) != 1:
            raise InputError(f"context has no unambiguous {key}: {sorted(values)}")
        result[key] = values.pop()
    return result


def validate_inputs(plan: dict, plan_raw: bytes, acq: dict, context: dict) -> tuple[dict, str]:
    if plan.get("schema") != PLAN_SCHEMA:
        raise InputError(f"unsupported plan schema: {plan.get('schema')!r}")
    if acq.get("schema") != ACQ_SCHEMA:
        raise InputError(f"unsupported acquisition schema: {acq.get('schema')!r}")
    task_ids = context_ids(context)
    if task_ids != EXPECTED_CONTEXT:
        raise InputError(f"unsupported task-context identity: {task_ids!r}")
    for label, obj in (("plan", plan), ("acquisition", acq)):
        found = {key: obj.get(key) for key in ("study_id", "iteration_id")}
        expected = {key: task_ids[key] for key in ("study_id", "iteration_id")}
        if found != expected:
            raise InputError(f"{label} identity {found!r} does not match task context {expected!r}")
    gap_id = plan.get("gap_id")
    if not isinstance(gap_id, str) or not gap_id.strip():
        raise InputError("plan.gap_id must be a non-empty string")
    ids = {"study_id": plan["study_id"], "iteration_id": plan["iteration_id"],
           "gap_id": gap_id.strip()}
    acq_gap_id = acq.get("gap_id")
    if acq_gap_id != ids["gap_id"]:
        raise InputError(f"acquisition gap_id {acq_gap_id!r} does not match plan gap_id {ids['gap_id']!r}")
    plan_hash = sha(plan_raw)
    if str(acq.get("plan_sha256", "")).lower() != plan_hash:
        raise InputError("acquisition plan_sha256 does not match the supplied plan bytes")
    required = ("target_claim", "gap", "affected_use", "value", "budget_limits",
                "resource_ids", "requirements", "allowed_decisions")
    missing = [key for key in required if key not in plan]
    if missing:
        raise InputError(f"plan missing required fields: {missing}")
    if not isinstance(plan["resource_ids"], list) or not {"uci_archive", "uci_page"}.issubset(plan["resource_ids"]):
        raise InputError("plan.resource_ids must include uci_archive and uci_page")
    if not isinstance(acq.get("resources"), list) or "attempts" not in acq or "observed_usage" not in acq:
        raise InputError("acquisition requires resources, observed_usage, and attempts")
    return ids, plan_hash


def byte_budget_limit(budget: object) -> int:
    if not isinstance(budget, dict):
        raise InputError("plan.budget_limits must be an object")
    declared = [value for key, value in budget.items()
                if "byte" in key.lower() and ("max" in key.lower() or "limit" in key.lower())
                and isinstance(value, int) and not isinstance(value, bool) and value > 0]
    return min([MAX_SOURCE_BYTES] + declared)


def read_resource(root: Path, item: dict, resource_id: str) -> tuple[bytes, dict]:
    source = item.get("source")
    if not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k].strip()
                                           for k in ("url", "version", "license", "retrieved_at")):
        raise InputError(f"{resource_id} needs source.url/version/license/retrieved_at")
    path, rel = relpath(root, item.get("path"), f"{resource_id}.path", True)
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES or item.get("bytes") != size:
        raise InputError(f"{resource_id} byte count/limit does not match acquisition metadata")
    data = path.read_bytes()
    digest = sha(data)
    if str(item.get("sha256", "")).lower() != digest:
        raise InputError(f"{resource_id} SHA-256 does not match acquisition metadata")
    url = urlsplit(source["url"])
    if url.scheme != "https" or (url.hostname or "").lower() != "archive.ics.uci.edu":
        raise InputError(f"{resource_id} URL is not on the official UCI host")
    if resource_id == "uci_page" and not url.path.startswith("/dataset/69/"):
        raise InputError("uci_page URL is not dataset 69")
    if resource_id == "uci_archive" and not (
        url.path.startswith("/static/public/69/") and url.path.lower().endswith(".zip")
    ):
        raise InputError("uci_archive URL is not the dataset 69 ZIP")
    return data, {"resource_id": resource_id, "path": rel, "bytes": size, "sha256": digest,
                  "source": {k: source[k] for k in ("url", "version", "license", "retrieved_at")}}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text, self.title, self.tables = [], [], []
        self.skip = 0
        self.in_title = False
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self.skip += 1
        elif not self.skip:
            if tag == "title":
                self.in_title = True
            elif tag == "table" and self.table is None:
                self.table = []
            elif tag == "tr" and self.table is not None:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif not self.skip:
            if tag == "title":
                self.in_title = False
            elif tag in ("td", "th") and self.cell is not None:
                self.row.append(" ".join(self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.table.append(self.row)
                self.row = None
            elif tag == "table" and self.table is not None:
                self.tables.append(self.table)
                self.table = None

    def handle_data(self, data):
        if self.skip:
            return
        if data.strip():
            self.text.append(data)
        if self.in_title:
            self.title.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def visible(self):
        return re.sub(r"\s+", " ", html.unescape(" ".join(self.text))).strip()


def page_fields(parser: PageParser) -> list[dict]:
    fields = []
    for table in parser.tables:
        for row in table:
            if len(row) >= 2 and (
                row[0].lower() in ("class", "instancename") or re.fullmatch(r"Base\d+", row[0])
            ):
                keys = ("name", "role", "type", "description", "units", "missing_values")
                fields.append({k: row[i] if i < len(row) else "" for i, k in enumerate(keys)})
    return fields


def page_aliases(text: str, path: str) -> list[dict]:
    observations = []
    for label in ("IE", "EI"):
        match = re.search(rf"\b{label}\s+borders?\b[^.?!]{{0,180}}?\b(donors?|acceptors?)\b", text, re.I)
        if match:
            observations.append({
                "source_resource_id": "uci_page", "path": path,
                "location": "Dataset Information > Additional Information > Problem Description",
                "visible_text_offset": [match.start(), match.end()],
                "class_label": label, "alias": match.group(1).lower().rstrip("s"),
                "text": text[max(0, match.start() - 40):min(len(text), match.end() + 40)],
            })
    return observations


def archive_aliases(names: str, path: str) -> list[dict]:
    lines, found, seen = names.splitlines(), [], set()
    for number, line in enumerate(lines, 1):
        m = re.search(r"\(\s*(ie|ei)\s*\)[^\r\n]{0,140}?\b(donors?|acceptors?)\b", line, re.I)
        if m:
            label, alias = m.group(1).upper(), m.group(2).lower().rstrip("s")
            key = (label, alias, number, number)
            seen.add(key)
            found.append({"source_resource_id": "uci_archive", "path": path,
                          "member": "splice.names", "line_start": number, "line_end": number,
                          "class_label": label, "alias": alias, "text": line})
    for start in range(len(lines)):
        block = "\n".join(lines[start:start + 3])
        if not re.search(r"\b(?:IE|EI)\s+borders?\b", block, re.I):
            continue
        for label in ("IE", "EI"):
            m = re.search(rf"\b{label}\s+borders?\b[\s\S]{{0,180}}?\b(donors?|acceptors?)\b", block, re.I)
            if m:
                first = start + 1 + block[:m.start()].count("\n")
                last = start + 1 + block[:m.end()].count("\n")
                alias = m.group(1).lower().rstrip("s")
                key = (label, alias, first, last)
                if key not in seen:
                    seen.add(key)
                    found.append({"source_resource_id": "uci_archive", "path": path,
                                  "member": "splice.names", "line_start": first, "line_end": last,
                                  "class_label": label, "alias": alias,
                                  "text": "\n".join(lines[first - 1:last])})
    return sorted(found, key=lambda x: (x["line_start"], x["class_label"], x["alias"]))


def archive_schema_locations(names: str) -> list[dict]:
    needles = ("Number of Attributes:", "-- class", "-- instance name",
               "-- 60 sequential DNA nucleotide positions", "The instance name.")
    lines, hits = names.splitlines(), []
    for needle in needles:
        for number, line in enumerate(lines, 1):
            if needle.lower() in line.lower():
                hits.append({"line": number, "text": line})
                break
    return hits


def parse_archive(raw: bytes, path: str) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 20 * 1024 * 1024:
                raise InputError("ZIP expanded size exceeds 20 MiB")
            bad = archive.testzip()
            if bad:
                raise InputError(f"ZIP CRC check failed at {bad!r}")
            members = [{"member": i.filename, "bytes": i.file_size, "crc32": f"{i.CRC:08x}"}
                       for i in archive.infolist()]
            if archive.namelist().count("splice.data") != 1 or archive.namelist().count("splice.names") != 1:
                raise InputError("ZIP must contain exactly one splice.data and one splice.names")
            data_raw, names_raw = archive.read("splice.data"), archive.read("splice.names")
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise InputError(f"uci_archive is not a readable ZIP: {exc}") from exc
    try:
        data, names = data_raw.decode("ascii"), names_raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(f"unsupported encoding in splice.data or splice.names: {exc}") from exc

    records, malformed = [], []
    raw_field_counts = collections.Counter()
    for line_no, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        raw_field_counts[len(line.split(","))] += 1
        fields = [x.strip() for x in line.split(",", 2)]
        if len(fields) != 3 or not all(fields) or not re.fullmatch(r"[A-Za-z]+", fields[2]):
            malformed.append(line_no)
            continue
        records.append({"line": line_no, "class": fields[0], "name": fields[1], "seq": fields[2]})
    if malformed or not records:
        raise InputError(f"splice.data is empty or has malformed rows at lines {malformed[:8]}")

    classes, lengths, symbols = collections.Counter(), collections.Counter(), collections.Counter()
    by_name, prefixes, shapes, examples, terminal = collections.defaultdict(list), set(), collections.Counter(), {}, collections.Counter()
    for row in records:
        classes[row["class"]] += 1
        lengths[len(row["seq"])] += 1
        symbols.update(row["seq"].upper())
        by_name[row["name"]].append(row)
        prefixes.add(row["name"].split("-", 1)[0])
        m = re.fullmatch(r"([A-Za-z0-9]+)-([A-Za-z]+)-([0-9]+)", row["name"])
        shape = "alphanumeric-prefix / alphabetic-token / integer-token" if m else "other"
        shapes[shape] += 1
        examples.setdefault(shape, [])
        if len(examples[shape]) < 6:
            examples[shape].append(row["name"])
        if m:
            terminal[(row["class"], m.group(2))] += 1
    duplicates = [rows for rows in by_name.values() if len(rows) > 1]
    same_duplicate_content = all(
        len({(r["class"], r["seq"]) for r in rows}) == 1 for rows in duplicates
    )
    schema_hits = archive_schema_locations(names)
    return {
        "archive_members": members,
        "splice_data": {"bytes": len(data_raw), "sha256": sha(data_raw)},
        "splice_names": {"bytes": len(names_raw), "sha256": sha(names_raw)},
        "record_count": len(records),
        "class_counts": dict(sorted(classes.items())),
        "instance_name_occurrences": len(records),
        "unique_instance_name_count": len(by_name),
        "duplicate_name_group_count": len(duplicates),
        "duplicate_name_excess_rows": len(records) - len(by_name),
        "all_duplicate_groups_same_class_and_sequence": same_duplicate_content,
        "sequence_length_counts": dict(sorted(lengths.items())),
        "sequence_symbol_counts": dict(sorted(symbols.items())),
        "raw_comma_field_count_distribution": dict(sorted(raw_field_counts.items())),
        "record_id_shape_counts": dict(shapes),
        "record_id_shape_examples": examples,
        "mechanical_prefix_distinct_count": len(prefixes),
        "mechanical_prefix_definition": "substring before the first hyphen; string statistic only, not a gene/family assignment",
        "terminal_token_counts_by_class": {
            f"{label}:{token}": n for (label, token), n in sorted(terminal.items())
        },
        "archive_schema_locations": schema_hits,
        "genbank_64_1_source_lines": [
            {"line": n, "text": line} for n, line in enumerate(names.splitlines(), 1)
            if "genbank 64.1" in line.lower()
        ],
        "archive_alias_observations": archive_aliases(names, path),
        "_names_text": names,
    }


def parse_page(raw: bytes, path: str) -> dict:
    try:
        parser = PageParser()
        parser.feed(raw.decode("utf-8-sig"))
        parser.close()
    except Exception as exc:
        raise InputError(f"cannot parse uci_page HTML: {exc}") from exc
    title, text = re.sub(r"\s+", " ", " ".join(parser.title)).strip(), parser.visible()
    if "molecular biology" not in title.lower() or "splice-junction" not in title.lower():
        raise InputError(f"page title does not identify UCI Splice dataset 69: {title!r}")
    fields = page_fields(parser)
    count = re.search(r"#\s*Instances\s*([0-9][0-9,]*)", text, re.I)
    features = re.search(r"#\s*Features\s*([0-9][0-9,]*)", text, re.I)
    license_match = re.search(r"Creative Commons Attribution 4\.0 International.*?CC BY 4\.0", text, re.I)
    position = re.search(r"starting at position.{0,100}?ending at position.{0,30}\+30", text, re.I)
    ambiguity = re.search(r"D:\s*A or G or T.{0,100}?R:\s*A or G", text, re.I)
    return {
        "title": title,
        "declared_instance_count": int(count.group(1).replace(",", "")) if count else None,
        "declared_feature_count": int(features.group(1).replace(",", "")) if features else None,
        "declared_fields": fields,
        "declared_field_names": [x["name"] for x in fields],
        "entity_related_fields": [x for x in fields if ENTITY_WORDS.search(" ".join(x.values()))],
        "license_text": license_match.group(0) if license_match else None,
        "position_wording": position.group(0) if position else None,
        "ambiguity_code_wording": ambiguity.group(0) if ambiguity else None,
        "page_alias_observations": page_aliases(text, path),
    }


def qualifications(archive: dict, page: dict, archive_path: str, page_path: str,
                   page_count_matches: bool) -> dict:
    names = archive.pop("_names_text")
    mapping_lines = [
        {"line": n, "text": line} for n, line in enumerate(names.splitlines(), 1)
        if re.search(r"\b(accession|stable gene|gene identifier|family identifier|gene family id|maps? to)\b", line, re.I)
    ]
    entity_fields = page["entity_related_fields"]
    stable_fields = [x for x in entity_fields if re.search(
        r"accession|gene[ _-]?id|family[ _-]?id", x["name"], re.I
    )]
    if not page_count_matches:
        linkage, linkage_reason = "unknown", "The UCI page instance count and ZIP record count disagree."
    elif stable_fields and mapping_lines:
        linkage, linkage_reason = "met", "A declared stable entity field and explicit source mapping evidence were observed."
    elif not entity_fields and not mapping_lines:
        linkage, linkage_reason = "unmet", (
            "The declared page fields contain no accession/gene/family or locus key, and the checked "
            "page/archive provide no authoritative instance-to-entity mapping."
        )
    else:
        linkage, linkage_reason = "unknown", (
            "Some entity-related wording exists, but a stable entity key and authoritative mapping are not both established."
        )

    observations = archive["archive_alias_observations"] + page["page_alias_observations"]
    alias_sets = {label: sorted({x["alias"] for x in observations if x["class_label"] == label})
                  for label in ("IE", "EI")}
    conflicts = {label: values for label, values in alias_sets.items() if len(values) > 1}
    if conflicts or not any(alias_sets.values()):
        label_status = "unknown"
        label_reason = (
            "The archive contains conflicting donor/acceptor wording; the current page agrees with one passage "
            "but does not resolve the archive's internal conflict. Keep EI/IE/N as archive labels."
            if conflicts else "Sources do not define aliases consistently enough; keep EI/IE/N as archive labels."
        )
    else:
        label_status, label_reason = "met", "All observed source aliases agree; retain the raw archive labels."

    return {
        "biological_unit_linkage": {
            "status": linkage,
            "evidence": [
                {"resource_id": "uci_page", "path": page_path, "location": "Variables Table",
                 "declared_field_names": page["declared_field_names"],
                 "entity_related_fields": entity_fields},
                {"resource_id": "uci_archive", "path": archive_path, "member": "splice.names",
                 "schema_locations": archive["archive_schema_locations"],
                 "genbank_64_1_source_lines": archive["genbank_64_1_source_lines"],
                 "explicit_mapping_lines": mapping_lines,
                 "duplicate_instance_name_groups": archive["duplicate_name_group_count"]},
            ],
            "reason": linkage_reason,
        },
        "label_semantics_consistency": {
            "status": label_status,
            "evidence": {"aliases_by_label": alias_sets, "conflicts": conflicts,
                         "archive": archive["archive_alias_observations"],
                         "page": page["page_alias_observations"]},
            "reason": label_reason,
        },
    }


def consumption_qualification_rows(requirements: list, quals: dict, outcome_rel: str) -> list[dict]:
    if not isinstance(requirements, list) or not requirements:
        raise InputError("plan.requirements must be a non-empty list")
    rows, seen = [], set()
    for requirement in requirements:
        requirement_id = requirement.get("id") if isinstance(requirement, dict) else None
        if not isinstance(requirement_id, str) or not requirement_id.strip() or requirement_id in seen:
            raise InputError("plan.requirements need unique non-empty string IDs")
        seen.add(requirement_id)
        result = quals.get(requirement_id)
        if not isinstance(result, dict) or result.get("status") not in ("met", "unmet", "unknown"):
            raise InputError(f"qualification missing a valid status: {requirement_id!r}")
        if not isinstance(result.get("reason"), str) or not result["reason"].strip():
            raise InputError(f"qualification missing a reason: {requirement_id!r}")
        pointer_id = requirement_id.replace("~", "~0").replace("/", "~1")
        rows.append({
            "requirement_id": requirement_id,
            "status": result["status"],
            "reason": result["reason"],
            "evidence_locator": f"{outcome_rel}#/qualifications/{pointer_id}",
        })
    if set(quals) != seen:
        raise InputError("qualification IDs do not exactly match plan requirement IDs")
    return rows


def main_run(args) -> dict:
    root = Path(args.root).resolve(strict=True)
    if not root.is_dir():
        raise InputError("--root must be an existing directory")
    plan, plan_raw = read_json(root, args.plan, "--plan")
    acq, _ = read_json(root, args.acquisition, "--acquisition")
    context, context_raw = read_json(root, args.context, "--context")
    ids, plan_hash = validate_inputs(plan, plan_raw, acq, context)
    context_hash = sha(context_raw)

    resources = {}
    for item in acq["resources"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in resources:
            raise InputError("acquisition resources need unique string IDs")
        resources[item["id"]] = item
    raw, reads = {}, {}
    byte_limit = byte_budget_limit(plan["budget_limits"])
    for resource_id in ("uci_archive", "uci_page"):
        if resource_id not in resources:
            raise InputError(f"acquisition is missing {resource_id}")
        raw[resource_id], reads[resource_id] = read_resource(root, resources[resource_id], resource_id)
    total_source_bytes = sum(len(data) for data in raw.values())
    if total_source_bytes > byte_limit:
        raise InputError(f"combined UCI source bytes {total_source_bytes} exceed plan limit {byte_limit}")

    archive_path, page_path = reads["uci_archive"]["path"], reads["uci_page"]["path"]
    archive = parse_archive(raw["uci_archive"], archive_path)
    page = parse_page(raw["uci_page"], page_path)
    page_count_matches = page["declared_instance_count"] is not None and (
        page["declared_instance_count"] == archive["record_count"]
    )
    quals = qualifications(archive, page, archive_path, page_path, page_count_matches)
    archive.pop("_names_text", None)
    if page["declared_instance_count"] is None:
        page_count_note = "page count absent; comparison unknown"
    elif page_count_matches:
        page_count_note = "page count matches observed archive rows"
    else:
        page_count_note = "page count differs from observed archive rows"

    outcome = {
        "schema": OUTCOME_SCHEMA, **ids, "plan_sha256": plan_hash, "context_sha256": context_hash,
        "evaluation_role": "readiness",
        "plan": {k: plan[k] for k in (
            "target_claim", "gap", "affected_use", "value", "budget_limits",
            "requirements", "allowed_decisions"
        )},
        "acquisition_observed_usage": acq["observed_usage"],
        "acquisition_attempts": acq["attempts"],
        "resource_reads": [reads[k] for k in ("uci_archive", "uci_page")],
        "observations": {
            "uci_page": page, "uci_archive": archive,
            "page_archive_count_match": page_count_matches, "page_archive_count_note": page_count_note,
            "checked_relation_sources": ["uci_archive", "uci_page"],
        },
        "qualifications": quals,
        "gene_family_use": {
            "ready_for_unseen_gene_or_family_evaluation": quals["biological_unit_linkage"]["status"] == "met",
            "current_supported_use": "Historical 60-nt window classification; sequence/name groups remain proxies.",
            "prefix_use": "mechanical string statistic only; never use it as gene/family identity",
        },
        "lawful_next_options": [
            "Use the UCI dataset only under the license recorded in acquisition metadata and preserve attribution.",
            "For gene/family holdout, obtain a licensed/versioned source with authoritative stable entity links plus assembly, locus coordinates, and strand; preserve one-to-many mappings and unknowns.",
        ] if page["license_text"] else [
            "Verify reuse terms before distributing the source or derivatives.",
            "For gene/family holdout, obtain a licensed/versioned source with authoritative stable entity links plus assembly, locus coordinates, and strand; preserve one-to-many mappings and unknowns.",
        ],
        "scope_boundary": {"model_training_or_evaluation": False, "new_model_scores": False,
                           "validated_gene_mapping_created": False, "prefix_inference": False},
    }

    output_path, output_rel = relpath(root, args.out, "--out", False)
    con_path, con_rel = relpath(root, args.consumption, "--consumption", False)
    receipt_path, receipt_rel = relpath(root, args.run_receipt, "--run-receipt", False)
    input_paths = {(root / args.plan).resolve(), (root / args.acquisition).resolve(),
                   (root / args.context).resolve()}
    input_paths.update((root / item["path"]).resolve() for item in reads.values())
    if output_path == con_path or output_path in input_paths or con_path in input_paths:
        raise InputError("outputs must be distinct from each other and all inputs")
    if receipt_path in input_paths or receipt_path in (output_path, con_path):
        raise InputError("--run-receipt must be a separate future path")
    for label, path in (("--out", output_path), ("--consumption", con_path)):
        if path.exists():
            raise InputError(f"{label} already exists; refusing to overwrite: {path}")
        if not path.parent.is_dir():
            raise InputError(f"{label} parent directory must already exist: {path.parent}")
    if receipt_path.exists():
        raise InputError("--run-receipt must name a future path that does not yet exist")

    outcome_raw = dump(outcome)
    con = {
        "schema": CON_SCHEMA, **ids, "plan_sha256": plan_hash, "context_sha256": context_hash,
        "resource_reads": [{k: reads[r][k] for k in ("resource_id", "path", "bytes", "sha256")}
                           for r in ("uci_archive", "uci_page")],
        "run_receipt": receipt_rel,
        "qualifications": consumption_qualification_rows(plan["requirements"], quals, output_rel),
        "evaluation_role": "readiness",
        "outputs": [{"path": output_rel, "bytes": len(outcome_raw), "sha256": sha(outcome_raw)}],
    }
    con_raw = dump(con)
    created = []
    try:
        for path, data in ((output_path, outcome_raw), (con_path, con_raw)):
            with path.open("xb") as handle:
                created.append(path)
                handle.write(data)
    except OSError:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return {"outcome": {"path": output_rel, "bytes": len(outcome_raw), "sha256": sha(outcome_raw)},
            "consumption": {"path": con_rel, "bytes": len(con_raw)},
            "record_count": archive["record_count"],
            "qualifications": {key: value["status"] for key, value in quals.items()}}


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag, help_text in (
        ("--root", "workspace root; all other paths are relative to it"),
        ("--plan", "resource-gap plan JSON"),
        ("--acquisition", "resource-acquisition JSON"),
        ("--context", "child task-context JSON"),
        ("--out", "new readiness outcome JSON"),
        ("--consumption", "new consumption receipt JSON"),
        ("--run-receipt", "future runner-generated receipt path; not written here"),
    ):
        parser.add_argument(flag, required=True, help=help_text)
    args = parser.parse_args()
    try:
        result = main_run(args)
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: filesystem operation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())

