#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the human-documentation checker and inventory refresher."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PUBLIC_ROOT / "tools" / "documentation.py"
SPEC = importlib.util.spec_from_file_location("harness_documentation", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
documentation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(documentation)


def identity(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class DocumentationFixture:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True)
        self.policy = {
            "schema": "harness.documentation-policy/v1",
            "human": {
                "include": ["**/*.md"],
                "exclude": ["machine/**", "LICENSES/**"],
                "forbidden_boilerplate": ["does not guarantee"],
                "forbidden_verification_terms": ["SHA-256", "verify_checkpoint.py", "verify_release.py"],
                "forbidden_link_prefixes": [
                    "https://github.com/vvcchh0/BioInformaticsHarness-research-archive",
                    "https://chatgpt.com/c/",
                    "codex://",
                    "file://",
                ],
                "forbid_archive_pointer_targets": True,
                "forbid_visible_hex_identifiers": True,
            },
            "machine": {"source_index_required": ["id", "sources", "design", "borrowing", "implementation", "acceptance"]},
            "legal": {"preserve_verbatim": ["LICENSES/**"]},
            "release": {
                "frozen_tag": "v0.1.0-alpha.2",
                "documentation_revision": "fixture-revision",
            },
            "new_file_licenses": {
                "machine/document-notes/**": "CC-BY-4.0",
                "machine/history/**": "CC-BY-4.0",
                "machine/source-design-index.jsonl": "CC-BY-4.0",
                "machine/documentation-policy.json": "Apache-2.0",
                "machine/schemas/**": "Apache-2.0",
                "tools/**": "Apache-2.0",
                "tests/**": "Apache-2.0",
                "**/*.md": "CC-BY-4.0",
            },
        }
        write_text(root / documentation.POLICY_PATH, json.dumps(self.policy, indent=2) + "\n")
        write_text(root / "README.md", "# Fixture\n\nObserved result was negative under condition C.\n")
        write_text(root / "core" / "README.md", "# Core fixture\n\nThe measured result was negative.\n")
        write_text(root / "tools" / "documentation.py", "# fixture tooling\n")
        write_text(root / "verify_checkpoint.py", "# legacy verifier copy\n")
        write_text(root / "core" / "verify_release.py", "# legacy verifier copy\n")
        for case_id in ("splice", "yeast"):
            case_dir = root / "examples" / case_id
            content = f"# {case_id} fixture\n\nOriginal explanation.\n"
            write_text(case_dir / "README.md", content)
            size, digest = identity(case_dir / "README.md")
            case_manifest = {
                "schema": "public-checkpoint.study-case.v1",
                "case_id": case_id,
                "state": {"refit_for_release": False, "status": "not_assessed"},
                "artifacts": [
                    {
                        "path": "README.md",
                        "license": "CC-BY-4.0",
                        "source": [{"path": f"archive/{case_id}/README.md", "bytes": size, "sha256": digest}],
                        "transformation": "Identity copy of original explanation.",
                        "published": {"path": f"{case_id}/README.md", "bytes": size, "sha256": digest},
                    }
                ],
            }
            write_text(case_dir / "CASE-MANIFEST.json", json.dumps(case_manifest, indent=2) + "\n")
        row = {
            "id": "fixture-case",
            "sources": [{"id": "source-1", "kind": "paper", "title": "Fixture source", "url": "https://example.invalid/source/v1"}],
            "design": [{"id": "design-1", "relation": "resulting_design", "path": "core/README.md", "reason": "fixture design"}],
            "borrowing": {"summary": "No external code copied.", "mode": "none", "evidence": []},
            "implementation": [{"path": "tools/documentation.py", "status": "unknown"}],
            "acceptance": [{"kind": "static", "status": "not_run", "public_path": None, "result": "Not run."}],
        }
        write_text(root / documentation.INDEX_PATH, json.dumps(row) + "\n")


class DocumentationCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="documentation-check-")
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "public"
        DocumentationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_detects_visible_hash_and_verification_terms_in_code_but_allows_negative_science_text(self) -> None:
        text = (
            "# Guide\n\n"
            "The observed negative result did not improve under condition C.\n"
            "A visible SHA-256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef is shown.\n"
            "This description does not guarantee an outcome.\n"
            "Inline token: `SHA-256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`.\n"
            "```bash\n"
            "python core/verify_release.py --status\n"
            "```\n"
        )
        write_text(self.root / "README.md", text)
        write_text(self.root / "LICENSES" / "NOTICE.md", "This verbatim legal text contains SHA-256 and does not guarantee.\n")
        report = documentation.check_root(self.root)
        self.assertEqual(report["status"], "fail")
        by_rule = {(item["rule"], item["line"]) for item in report["findings"]}
        self.assertIn(("visible-hex-identifier", 4), by_rule)
        self.assertIn(("forbidden-verification-term", 4), by_rule)
        self.assertIn(("forbidden-boilerplate", 5), by_rule)
        self.assertIn(("visible-hex-identifier", 6), by_rule)
        self.assertIn(("forbidden-verification-term", 6), by_rule)
        self.assertIn(("forbidden-verification-term", 8), by_rule)
        self.assertFalse(any(item["line"] == 3 for item in report["findings"]))
        self.assertFalse(any(item["file"] == "LICENSES/NOTICE.md" for item in report["findings"]))

    def test_version_and_full_hash_in_link_destination_are_not_visible_identifiers(self) -> None:
        digest = "a" * 64
        write_text(self.root / "README.md", f"# Guide\n\n[Release source](https://example.invalid/releases/v1.2.3/{digest})\n")
        report = documentation.check_root(self.root)
        self.assertEqual(report["status"], "pass", report["findings"])
        self.assertEqual(report["checked_markdown"], 4)

    def test_reports_private_history_missing_and_escaping_links_with_line_numbers(self) -> None:
        write_text(
            self.root / "README.md",
            "# Guide\n"
            "[private archive](https://github.com/vvcchh0/BioInformaticsHarness-research-archive/tree/main)\n"
            "[conversation](https://chatgpt.com/c/abc)\n"
            "<codex://threads/abc>\n"
            "[old history](#archive-pointer-deadbeef)\n"
            "[missing](not-here.md)\n"
            "[escape](../outside.md)\n",
        )
        report = documentation.check_root(self.root)
        pairs = {(item["rule"], item["line"]) for item in report["findings"]}
        self.assertIn(("markdown-link-private-history", 2), pairs)
        self.assertIn(("markdown-link-private-history", 3), pairs)
        self.assertIn(("markdown-link-private-history", 4), pairs)
        self.assertIn(("archive-pointer-hex-target", 5), pairs)
        self.assertIn(("markdown-link-missing", 6), pairs)
        self.assertIn(("markdown-link-escapes-root", 7), pairs)

    def test_duplicate_index_ids_and_nonpublic_targets_are_rejected(self) -> None:
        first = {
            "id": "Same-ID",
            "sources": [],
            "design": [{"id": "d1", "relation": " ", "path": "../private/design.md", "reason": "outside"}],
            "borrowing": {"summary": "unknown", "mode": "unknown", "evidence": [{"availability": "private_archive", "locator": "archive:case-1"}]},
            "implementation": [{"path": "missing/tool.py", "status": "unknown"}],
            "acceptance": [{"kind": "runtime", "status": "unknown", "public_path": "missing/result.json", "archive_locator": "private:run-1", "result": "unknown"}],
        }
        second = dict(first)
        second["id"] = "same-id"
        write_text(self.root / documentation.INDEX_PATH, json.dumps(first) + "\n" + json.dumps(second) + "\n")
        report = documentation.check_root(self.root)
        rules = [item["rule"] for item in report["findings"]]
        self.assertIn("source-index-duplicate-id", rules)
        self.assertIn("source-index-design-field", rules)
        self.assertGreaterEqual(rules.count("source-index-public-path"), 6)
        self.assertEqual(len(report["unassessed"]), 4)

    def test_public_borrowing_locators_check_json_pointers_without_resolving_private_or_remote_sources(self) -> None:
        write_text(self.root / "core" / "references.json", '{"entries":[{"id":"optimus-2019"}]}\n')
        write_text(self.root / "core" / "empty.json", "")
        valid = json.loads((self.root / documentation.INDEX_PATH).read_text(encoding="utf-8"))
        valid["borrowing"]["evidence"] = [
            {"availability": "public", "locator": "core/references.json#/entries/0/id"},
            {"availability": "public", "locator": "README.md#overview"},
            {"availability": "public", "locator": "https://example.invalid/references.json#/entries/0"},
            {"availability": "private_archive", "locator": "archive:missing/private-source.json"},
        ]
        write_text(self.root / documentation.INDEX_PATH, json.dumps(valid) + "\n")
        report = documentation.check_root(self.root)
        self.assertEqual(report["status"], "pass", report["findings"])
        self.assertIn(
            {"file": documentation.INDEX_PATH, "line": 1, "kind": "borrowing_locator", "status": "not_online_checked"},
            report["unassessed"],
        )

        invalid = json.loads((self.root / documentation.INDEX_PATH).read_text(encoding="utf-8"))
        invalid["borrowing"]["evidence"] = [
            {"availability": "public", "locator": "core/references.json#/entries/optimus-2019"},
            {"availability": "public", "locator": "core/empty.json"},
            {"availability": "public", "locator": "../outside.json"},
        ]
        write_text(self.root / documentation.INDEX_PATH, json.dumps(invalid) + "\n")
        invalid_report = documentation.check_root(self.root)
        self.assertEqual(invalid_report["status"], "fail")
        self.assertIn("source-index-borrowing-json-pointer", [item["rule"] for item in invalid_report["findings"]])
        self.assertIn("source-index-borrowing-json", [item["rule"] for item in invalid_report["findings"]])
        self.assertIn("source-index-borrowing-public-locator", [item["rule"] for item in invalid_report["findings"]])

    def test_cli_writes_json_and_uses_exit_two_for_documentation_defects(self) -> None:
        write_text(self.root / "README.md", "# Guide\n\nThe description does not guarantee an outcome.\n")
        out = self.root / ".scratch" / "check-report.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = documentation.main(["check", "--root", str(self.root), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result, 2)
        self.assertEqual(report["status"], "fail")
        self.assertIn("forbidden-boilerplate", [item["rule"] for item in report["findings"]])

        unsafe_out = self.root / "report-in-inventory.json"
        rejected_stdout = io.StringIO()
        with contextlib.redirect_stdout(rejected_stdout):
            rejected = documentation.main(["check", "--root", str(self.root), "--out", str(unsafe_out)])
        rejected_report = json.loads(rejected_stdout.getvalue())
        self.assertEqual(rejected, 2)
        self.assertFalse(unsafe_out.exists())
        self.assertEqual(rejected_report["findings"][0]["rule"], "report-inside-public-root")


class RefreshCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="documentation-refresh-")
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "public"
        DocumentationFixture(self.root)
        shutil.copyfile(PUBLIC_ROOT / "verify_checkpoint.py", self.root / "verify_checkpoint.py")
        shutil.copyfile(PUBLIC_ROOT / "core" / "verify_release.py", self.root / "core" / "verify_release.py")
        shutil.copyfile(TOOL_PATH, self.root / "tools" / "documentation.py")
        self._write_old_manifests()
        self.baseline = self._initialize_git()
        self._modify_documentation()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _all_public_files(self):
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and ".git" not in path.relative_to(self.root).parts:
                yield path

    def _checkpoint_entry(self, path: Path) -> dict[str, object]:
        rel = path.relative_to(self.root).as_posix()
        size, digest = identity(path)
        return {
            "path": rel,
            "bytes": size,
            "sha256": digest,
            "license": "Apache-2.0",
            "source": "fixture baseline",
            "transform": "identity",
        }

    def _write_old_manifests(self) -> None:
        core_files = list((self.root / "core").rglob("*"))
        core_files = [path for path in core_files if path.is_file() and path.name != "RELEASE-MANIFEST.json"]
        release_entries = []
        for path in sorted(core_files):
            size, digest = identity(path)
            release_entries.append({
                "path": path.relative_to(self.root / "core").as_posix(),
                "bytes": size,
                "sha256": digest,
                "license": "CC-BY-4.0" if path.suffix == ".md" else "Apache-2.0",
                "source": "fixture baseline source",
                "transform": "identity",
            })
        release_entries.append({
            "path": "RELEASE-MANIFEST.json",
            "bytes": None,
            "sha256": None,
            "self_hashed": False,
            "license": "Apache-2.0",
            "source": "fixture generator",
            "transform": "manifest self hash omitted",
        })
        release = {
            "schema": "bioinformatics-harness-release-manifest/v1",
            "version": "fixture-1.0",
            "files": sorted(release_entries, key=lambda item: item["path"]),
        }
        write_text(self.root / "core" / "RELEASE-MANIFEST.json", json.dumps(release, indent=2) + "\n")

        checkpoint_entries = [self._checkpoint_entry(path) for path in self._all_public_files()]
        checkpoint_entries.append({
            "path": "CHECKPOINT-MANIFEST.json",
            "bytes": None,
            "sha256": None,
            "self_hashed": False,
            "license": "Apache-2.0",
            "source": "fixture generator",
            "transform": "manifest self hash omitted",
        })
        checkpoint = {
            "schema": "bioinformatics-harness-public-checkpoint/v1",
            "files": sorted(checkpoint_entries, key=lambda item: item["path"]),
        }
        write_text(self.root / "CHECKPOINT-MANIFEST.json", json.dumps(checkpoint, indent=2) + "\n")

    def _git(self, *args: str) -> str:
        safe_root = self.root.resolve().as_posix()
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={safe_root}", "-C", safe_root, *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    def _initialize_git(self) -> str:
        self._git("init", "--quiet")
        self._git("config", "user.name", "documentation-test")
        self._git("config", "user.email", "documentation-test@example.invalid")
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", "fixture baseline")
        head = self._git("rev-parse", "--verify", "HEAD^{commit}")
        self._git("tag", "v0.1.0-alpha.2", head)
        write_text(self.root / "README.md", "# Fixture\n\nA later documentation maintenance commit.\n")
        self._git("add", "README.md")
        self._git("commit", "--quiet", "-m", "later maintenance commit")
        return head

    def _modify_documentation(self) -> None:
        write_text(self.root / "README.md", "# Fixture\n\nUpdated explanation with an observed negative result.\n")
        write_text(self.root / "core" / "README.md", "# Core fixture\n\nUpdated purpose and installation notes.\n")
        write_text(self.root / "examples" / "splice" / "README.md", "# splice fixture\n\nUpdated explanation.\n")
        write_text(self.root / "examples" / "yeast" / "README.md", "# yeast fixture\n\nUpdated explanation.\n")

    @staticmethod
    def _load_verifier(path: Path, module_name: str):
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_refresh_updates_case_and_legacy_manifests_then_old_verifiers_accept_and_reject_unknowns(self) -> None:
        before = json.loads((self.root / "examples" / "splice" / "CASE-MANIFEST.json").read_text(encoding="utf-8"))
        stale_report = documentation.check_root(self.root)
        self.assertEqual(stale_report["status"], "fail")
        stale_case_findings = [item for item in stale_report["findings"] if item["rule"] == "case-published-identity"]
        self.assertEqual(len(stale_case_findings), 2)
        self.assertEqual({item["file"] for item in stale_case_findings}, {
            "examples/splice/CASE-MANIFEST.json",
            "examples/yeast/CASE-MANIFEST.json",
        })
        report_path = self.workspace / "refresh-report.json"
        stdout = io.StringIO()
        with mock.patch.object(documentation, "BASELINE_REVISION", self.baseline):
            with contextlib.redirect_stdout(stdout):
                result = documentation.main(["refresh", "--root", str(self.root), "--out", str(report_path)])
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "pass", report)
        self.assertNotEqual(report["head"], report["baseline_commit"])
        self.assertEqual(report["frozen_tag_target"], self.baseline)
        self.assertEqual(report["case_manifests_updated"], [
            "examples/splice/CASE-MANIFEST.json",
            "examples/yeast/CASE-MANIFEST.json",
        ])

        after = json.loads((self.root / "examples" / "splice" / "CASE-MANIFEST.json").read_text(encoding="utf-8"))
        old_artifact = before["artifacts"][0]
        new_artifact = after["artifacts"][0]
        size, digest = identity(self.root / "examples" / "splice" / "README.md")
        self.assertEqual(new_artifact["published"], {"path": "splice/README.md", "bytes": size, "sha256": digest})
        self.assertEqual(new_artifact["previous_published"]["sha256"], old_artifact["published"]["sha256"])
        self.assertEqual(new_artifact["previous_published"]["revision"], self.baseline)
        self.assertIn("fixture-revision", new_artifact["transformation"])
        self.assertEqual(new_artifact["source"], old_artifact["source"])
        self.assertEqual(new_artifact["license"], old_artifact["license"])
        self.assertEqual(after["state"], before["state"])

        release = json.loads((self.root / "core" / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(release["version"], "fixture-1.0")
        self.assertEqual(release["documentation_revision"], "fixture-revision")
        checkpoint = json.loads((self.root / "CHECKPOINT-MANIFEST.json").read_text(encoding="utf-8"))
        checkpoint_paths = [item["path"] for item in checkpoint["files"]]
        self.assertEqual(len(checkpoint_paths), len(set(checkpoint_paths)))
        record = json.loads((self.root / documentation.REFRESH_RECORD_PATH).read_text(encoding="utf-8"))
        new_record = next(item for item in checkpoint["files"] if item["path"] == documentation.REFRESH_RECORD_PATH)
        self.assertEqual(new_record["license"], "CC-BY-4.0")
        self.assertTrue(any(item["path"] == "core/RELEASE-MANIFEST.json" for item in record["changes"]))

        checkpoint_verifier = self._load_verifier(self.root / "verify_checkpoint.py", "fixture_checkpoint_verifier")
        release_verifier = self._load_verifier(self.root / "core" / "verify_release.py", "fixture_release_verifier")
        self.assertEqual(checkpoint_verifier.verify(self.root)["status"], "pass")
        self.assertEqual(release_verifier.verify(self.root / "core")["status"], "pass")

        write_text(self.root / "unknown-root.txt", "unknown\n")
        with self.assertRaises(ValueError):
            checkpoint_verifier.verify(self.root)
        (self.root / "unknown-root.txt").unlink()
        write_text(self.root / "core" / "unknown-core.txt", "unknown\n")
        with self.assertRaises(SystemExit):
            release_verifier.verify(self.root / "core")

    def test_refresh_rejects_a_frozen_tag_moved_off_the_baseline(self) -> None:
        before_checkpoint = (self.root / "CHECKPOINT-MANIFEST.json").read_bytes()
        before_release = (self.root / "core" / "RELEASE-MANIFEST.json").read_bytes()
        wrong_target = self._git("rev-parse", "--verify", "HEAD^{commit}")
        self._git("tag", "--force", "v0.1.0-alpha.2", wrong_target)
        with mock.patch.object(documentation, "BASELINE_REVISION", self.baseline):
            with self.assertRaises(documentation.RefreshError) as raised:
                documentation.refresh_root(self.root)
        self.assertEqual(raised.exception.rule, "frozen-tag-moved")
        self.assertEqual((self.root / "CHECKPOINT-MANIFEST.json").read_bytes(), before_checkpoint)
        self.assertEqual((self.root / "core" / "RELEASE-MANIFEST.json").read_bytes(), before_release)


if __name__ == "__main__":
    unittest.main()
