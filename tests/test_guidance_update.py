"""Contract tests for provenance-safe guidance updates.

These tests build all fixtures under the task-controlled TMP directory. Git is
used only against temporary fixture repositories when the test suite is run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.guidance_update import capture, check_adoption, make_plan, verify_snapshot, _rel, main

try:
    from tools.guidance_update import SupportError
except ImportError:
    from scripts.research_support._common import SupportError


INVENTORY = "CHECKPOINT-MANIFEST.json"
CURRENT_SCHEMA = "bioinformatics-harness-public-checkpoint/v1"
LEGACY_SCHEMA = "harness.current-guidance-freeze/v1"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class GuidanceUpdateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        # Deliberately leave fixture directories in place for inspection.
        self.temp_root = Path(tempfile.mkdtemp(prefix="guidance-update-tests-"))

    def _write_package(
        self,
        name: str,
        files: Mapping[str, str | bytes],
        *,
        legacy: bool = False,
    ) -> Path:
        root = self.temp_root / name
        root.mkdir(parents=True)
        manifest_files = []
        for relative, value in files.items():
            raw = value.encode("utf-8") if isinstance(value, str) else value
            target = root.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            manifest_files.append(
                {"path": relative, "bytes": len(raw), "sha256": _digest(raw)}
            )
        manifest = {
            "schema": LEGACY_SCHEMA if legacy else CURRENT_SCHEMA,
            "files": manifest_files,
        }
        (root / INVENTORY).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return root

    def _git(self, repo: Path, *args: str) -> str:
        hooks = self.temp_root / "empty-git-hooks"
        hooks.mkdir(exist_ok=True)
        command = [
            "git",
            "-c",
            "user.name=Guidance Update Test",
            "-c",
            "user.email=guidance-update-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            f"safe.directory={repo}",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(repo),
            *args,
        ]
        completed = subprocess.run(
            command,
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _one_review_plan(self) -> tuple[Path, Path, dict, dict[str, bytes]]:
        old_content = b"Original method\n"
        new_content = b"Revised method\n"
        old_root = self._write_package(
            "old-package", {"core/docs/methods/method.md": old_content}
        )
        new_root = self._write_package(
            "new-package", {"core/docs/methods/method.md": new_content}
        )
        old = capture(old_root)
        new = capture(new_root)
        plan = make_plan(old, new)
        return old_root, new_root, plan, {"core/docs/methods/method.md": new_content}

    def _receipt(self, plan: dict, decisions: list[dict]) -> dict:
        return {
            "schema": "harness.guidance-adoption/v1",
            "plan_id": plan["plan_id"],
            "from_package_id": plan["from_package_id"],
            "to_package_id": plan["to_package_id"],
            "actor": "contract-test",
            "scope": "temporary fixture",
            "decisions": decisions,
        }

    def _decision(
        self,
        plan: dict,
        *,
        disposition: str,
        reason: str,
        read_bytes: bytes | None = None,
        artifacts: list[dict] | None = None,
        acceptance_ref: str | None = None,
    ) -> dict:
        review = plan["reviews"][0]
        decision = {
            "review_id": review["id"],
            "disposition": disposition,
            "reason": reason,
            "artifacts": artifacts or [],
        }
        if review.get("reading_required") and read_bytes is not None:
            decision["readings"] = [{"path": review["path"], "sha256": _digest(read_bytes)}]
        if acceptance_ref is not None:
            decision["acceptance_ref"] = acceptance_ref
        return decision

    def test_clean_payload_git_records_exact_commit(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        root = self._write_package("clean-git", {"core/docs/methods/a.md": "text\n"})
        self._git(root, "init", "--quiet")
        self._git(root, "remote", "add", "origin", "https://github.com/vvcchh0/BioInformaticsHarness.git")
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "fixture")
        expected_commit = self._git(root, "rev-parse", "HEAD")

        snapshot = capture(root)

        self.assertEqual(snapshot["source"]["commit"], expected_commit)
        self.assertTrue(snapshot["source"]["git_available"])
        self.assertFalse(snapshot["source"]["dirty"])
        self.assertTrue(snapshot["source"]["exact_commit"])

    def test_dirty_payload_git_reports_base_commit_without_exact_identity(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        root = self._write_package("dirty-git", {"core/docs/methods/a.md": "before\n"})
        self._git(root, "init", "--quiet")
        self._git(root, "remote", "add", "origin", "https://github.com/vvcchh0/BioInformaticsHarness.git")
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "fixture")
        base_commit = self._git(root, "rev-parse", "HEAD")
        (root / "core" / "docs" / "methods" / "a.md").write_bytes(b"after\n")
        manifest = json.loads((root / INVENTORY).read_text(encoding="utf-8"))
        data = b"after\n"
        manifest["files"][0].update({"bytes": len(data), "sha256": _digest(data)})
        (root / INVENTORY).write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = capture(root)

        self.assertEqual(snapshot["source"]["commit"], base_commit)
        self.assertTrue(snapshot["source"]["dirty"])
        self.assertFalse(snapshot["source"]["exact_commit"])

    def test_parent_repository_is_not_used_and_legacy_commit_stays_unknown(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        parent = self.temp_root / "outer-repository"
        parent.mkdir()
        self._git(parent, "init", "--quiet")
        nested = self._write_package(
            "outer-repository/nested-payload",
            {"core/docs/methods/a.md": "text\n"},
        )
        self._git(parent, "add", "--all")
        self._git(parent, "commit", "--quiet", "-m", "outer fixture")

        nested_snapshot = capture(nested)
        self.assertFalse(nested_snapshot["source"]["git_available"])
        self.assertIsNone(nested_snapshot["source"]["commit"])

        legacy = self._write_package(
            "legacy-payload", {"guidance.md": "frozen\n"}, legacy=True
        )
        legacy_snapshot = capture(legacy, legacy=True)
        self.assertIsNone(legacy_snapshot["source"]["commit"])

    def test_verify_snapshot_rejects_snapshot_identity_and_payload_tampering(self) -> None:
        root = self._write_package("verify-target", {"core/docs/methods/a.md": "safe\n"})
        snapshot = capture(root)

        forged = dict(snapshot)
        forged["package_id"] = "forged-package-id"
        identity_result = verify_snapshot(root, forged)
        self.assertEqual(identity_result["overall"], "fail")

        target = root / "core" / "docs" / "methods" / "a.md"
        target.write_text("changed\n", encoding="utf-8")
        payload_result = verify_snapshot(root, snapshot)
        self.assertEqual(payload_result["overall"], "fail")
        self.assertEqual(payload_result["scientific_acceptance"], "not_assessed")

    def test_capture_rejects_traversal_and_duplicate_inventory_paths(self) -> None:
        root = self._write_package("bad-paths", {"inside.md": "ok\n"})
        manifest_path = root / INVENTORY
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../outside.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SupportError):
            capture(root)

        duplicate = self._write_package("duplicate-paths", {"inside.md": "ok\n"})
        manifest_path = duplicate / INVENTORY
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(dict(manifest["files"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SupportError):
            capture(duplicate)

    def test_capture_rejects_symlinked_payload_file(self) -> None:
        root = self._write_package("symlink-payload", {})
        outside = self.temp_root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        link = root / "linked.md"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks are unavailable: {error}")
        raw = outside.read_bytes()
        (root / INVENTORY).write_text(
            json.dumps(
                {
                    "schema": CURRENT_SCHEMA,
                    "files": [
                        {"path": "linked.md", "bytes": len(raw), "sha256": _digest(raw)}
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(SupportError):
            capture(root)

    def test_plan_covers_modified_added_removed_review_paths_and_unindexed_items(self) -> None:
        old_root = self._write_package(
            "diff-old",
            {
                "core/docs/methods/method.md": "old\n",
                "core/machine/design/interface.json": "{\"v\":1}\n",
                "README.md": "same\n",
            },
        )
        new_root = self._write_package(
            "diff-new",
            {
                "core/docs/methods/method.md": "new\n",
                "core/scripts/helper.py": "print('new')\n",
                "README.md": "same\n",
            },
        )

        plan = make_plan(capture(old_root), capture(new_root))

        by_path = {item["path"]: item["kind"] for item in plan["changes"] if item["path"] != INVENTORY}
        self.assertEqual(
            by_path,
            {
                "core/docs/methods/method.md": "modified",
                "core/machine/design/interface.json": "removed",
                "core/scripts/helper.py": "added",
            },
        )
        review_paths = {item["path"] for item in plan["reviews"]}
        expected_reviews = set(by_path)
        self.assertTrue(expected_reviews.issubset(review_paths))
        self.assertTrue(expected_reviews.issubset(set(plan["unindexed_paths"])))

    def test_deferred_review_remains_unaccepted(self) -> None:
        _, new_root, plan, new_contents = self._one_review_plan()
        decision = self._decision(
            plan,
            disposition="defer",
            reason="Needs later study-level review.",
            read_bytes=next(iter(new_contents.values())),
        )
        study_root = self.temp_root / "deferred-study"
        study_root.mkdir()

        result = check_adoption(study_root, plan, self._receipt(plan, [decision]))

        self.assertEqual(result["overall"], "unknown")
        self.assertEqual(result["adoption_readiness"], "pending")
        self.assertEqual(result["scientific_acceptance"], "not_assessed")
        self.assertFalse(result["active_version_written"])
        self.assertTrue(new_root.exists())

    def test_revise_requires_real_derived_artifact_and_acceptance_reference(self) -> None:
        _, _, plan, _ = self._one_review_plan()
        study_root = self.temp_root / "revision-study"
        (study_root / "updates").mkdir(parents=True)
        (study_root / "records").mkdir()
        artifact_bytes = b"Derived method for this study\n"
        artifact_path = study_root / "updates" / "method.md"
        artifact_path.write_bytes(artifact_bytes)
        acceptance_path = study_root / "records" / "acceptance.md"
        acceptance_path.write_text("Accepted derived revision.\n", encoding="utf-8")
        good_decision = self._decision(
            plan,
            disposition="revise",
            reason="Adapted for this study.",
            read_bytes=b"Revised method\n",
            artifacts=[
                {
                    "path": "updates/method.md",
                    "bytes": len(artifact_bytes),
                    "sha256": _digest(artifact_bytes),
                }
            ],
            acceptance_ref="records/acceptance.md",
        )
        good = check_adoption(
            study_root, plan, self._receipt(plan, [good_decision])
        )
        self.assertEqual(good["overall"], "pass")

        bad_decision = dict(good_decision)
        bad_decision["artifacts"] = [
            {"path": "updates/method.md", "bytes": len(artifact_bytes), "sha256": "0" * 64}
        ]
        bad = check_adoption(study_root, plan, self._receipt(plan, [bad_decision]))
        self.assertEqual(bad["overall"], "fail")

    def test_preserved_study_file_tampering_fails_adoption_check(self) -> None:
        study_root = self.temp_root / "preserve-study"
        protected = study_root / "source" / "protocol.md"
        protected.parent.mkdir(parents=True)
        original = b"Protected study source\n"
        protected.write_bytes(original)
        preserve = [
            {
                "path": "source/protocol.md",
                "bytes": len(original),
                "sha256": _digest(original),
            }
        ]
        empty_root = self._write_package("preserve-empty", {"README.md": "same\n"})
        snapshot = capture(empty_root)
        plan = make_plan(snapshot, snapshot, preserve=preserve)
        protected.write_bytes(b"changed\n")

        result = check_adoption(study_root, plan, self._receipt(plan, []))

        self.assertEqual(result["overall"], "fail")

    def test_adoption_check_does_not_modify_existing_study_source(self) -> None:
        _, _, plan, new_contents = self._one_review_plan()
        study_root = self.temp_root / "immutable-study"
        source = study_root / "core" / "docs" / "methods" / "method.md"
        source.parent.mkdir(parents=True)
        old_bytes = b"Original method\n"
        source.write_bytes(old_bytes)
        before_hash = _digest(source.read_bytes())
        decision = self._decision(
            plan,
            disposition="retain",
            reason="Existing study source remains under its original custody.",
            read_bytes=next(iter(new_contents.values())),
        )

        check_adoption(study_root, plan, self._receipt(plan, [decision]))

        self.assertEqual(_digest(source.read_bytes()), before_hash)
        self.assertEqual(source.read_bytes(), old_bytes)

    def _note_files(self) -> dict[str, str]:
        note = {
            "id": "fixture-change", "summary": "Read the supporting guide too",
            "impact": "guidance", "revisit": "Current study interpretation",
            "acceptance": "Explain applicability", "paths": ["core/docs/methods/a.md"],
            "reading_paths": ["supporting-guide.md"],
        }
        return {"core/docs/methods/a.md": "same\n", "supporting-guide.md": "context\n",
                "machine/changes/fixture.json": json.dumps(note)}

    def test_note_only_update_requires_all_indexed_readings(self) -> None:
        files = self._note_files()
        old = self._write_package("note-old", {k: v for k, v in files.items() if not k.startswith("machine/changes/")})
        new = self._write_package("note-new", files)
        plan = make_plan(capture(old), capture(new))
        self.assertEqual(len(plan["reviews"]), 1)
        review = plan["reviews"][0]
        self.assertEqual(review["kind"], "reappraise")
        decision = {"review_id": review["id"], "disposition": "retain", "reason": "Context reviewed",
                    "readings": [{"path": r["path"], "sha256": r["sha256"]} for r in review["required_readings"]]}
        study = self.temp_root / "note-study"
        study.mkdir()
        self.assertEqual(check_adoption(study, plan, self._receipt(plan, [decision]))["overall"], "pass")
        decision["readings"] = [r for r in decision["readings"] if r["path"] != "supporting-guide.md"]
        self.assertEqual(check_adoption(study, plan, self._receipt(plan, [decision]))["overall"], "fail")

    def test_historical_note_cannot_be_removed_or_narrowed(self) -> None:
        files = self._note_files()
        old = capture(self._write_package("history-old", files))
        removed = {k: v for k, v in files.items() if not k.startswith("machine/changes/")}
        with self.assertRaises(SupportError):
            make_plan(old, capture(self._write_package("history-removed", removed)))
        changed = dict(files)
        note = json.loads(changed["machine/changes/fixture.json"])
        note["reading_paths"] = ["core/docs/methods/a.md"]
        changed["machine/changes/fixture.json"] = json.dumps(note)
        with self.assertRaises(SupportError):
            make_plan(old, capture(self._write_package("history-narrowed", changed)))

    def test_old_note_does_not_silently_cover_later_unindexed_change(self) -> None:
        files = self._note_files()
        old = capture(self._write_package("stale-note-old", files))
        changed = {**files, "core/docs/methods/a.md": "later unindexed revision\n"}
        plan = make_plan(old, capture(self._write_package("stale-note-new", changed)))
        self.assertIn("core/docs/methods/a.md", plan["unindexed_paths"])

    def test_windows_aliases_and_protected_names_are_rejected(self) -> None:
        for relative in [".git./new.json", ".git /new.json", "NUL.txt", "a/COM1", "a/name.", "a/name "]:
            with self.subTest(relative=relative), self.assertRaises(SupportError):
                _rel(relative)

    def test_inventory_self_entry_is_measured_without_recursive_self_hash(self) -> None:
        root = self._write_package("self-entry", {"README.md": "package\n"})
        path = root / INVENTORY
        value = json.loads(path.read_text(encoding="utf-8"))
        value["files"].append({"path": INVENTORY, "self_hashed": False, "bytes": None, "sha256": None})
        path.write_text(json.dumps(value), encoding="utf-8")
        snapshot = capture(root)
        measured = next(e for e in snapshot["files"] if e["path"] == INVENTORY)
        self.assertEqual(measured["sha256"], _digest(path.read_bytes()))
        self.assertEqual(verify_snapshot(root, snapshot)["overall"], "pass")

    def test_study_repository_commit_is_not_harness_upstream_commit(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        root = self._write_package("study-root", {"README.md": "study\n"})
        self._git(root, "init", "--quiet")
        self._git(root, "remote", "add", "origin", "https://example.invalid/private-study.git")
        self._git(root, "add", "--all")
        self._git(root, "commit", "--quiet", "-m", "study fixture")
        source = capture(root)["source"]
        self.assertEqual(source["local_commit"], self._git(root, "rev-parse", "HEAD"))
        self.assertIsNone(source["commit"])
        self.assertIsNone(source["repository"])
        self.assertFalse(source["exact_commit"])

    def test_unlisted_material_and_existing_cli_output_are_rejected(self) -> None:
        root = self._write_package("unlisted", {"README.md": "package\n"})
        (root / "tools").mkdir()
        (root / "tools" / "missing.py").write_bytes(b"pass\n")
        with self.assertRaises(SupportError):
            capture(root)
        target = self.temp_root / "existing.json"
        target.write_bytes(b"keep\n")
        self.assertEqual(main(["--work-root", str(self.temp_root), "--out", "existing.json",
                               "capture", "--root", str(root)]), 1)
        self.assertEqual(target.read_bytes(), b"keep\n")

    def test_receiver_rejects_extra_unlisted_material(self) -> None:
        root = self._write_package("received-extra", {"README.md": "package\n"})
        snapshot = capture(root)
        (root / "core/scripts").mkdir(parents=True)
        (root / "core/scripts/unlisted.py").write_bytes(b"pass\n")
        self.assertEqual(verify_snapshot(root, snapshot)["overall"], "fail")


if __name__ == "__main__":
    unittest.main()
