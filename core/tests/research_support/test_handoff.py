from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support._common import (  # noqa: E402
    SupportError,
    dump_new,
    fingerprint,
    load_json,
    resolve_path,
)
from scripts.research_support.handoff import inspect_handoff, main  # noqa: E402


class HandoffSupportTests(unittest.TestCase):
    def test_oversized_timeout_is_structured_invalid_environment_without_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"schema_version": "research-support-handoff-v1", "id": "invalid-huge-budget",
                        "artifacts": [{"id": "cache", "path": "optional.bin", "role": "cache",
                                       "required": False, "use": "optional cache"}],
                        "environment": {"python": sys.executable, "cwd": ".", "imports": ["json"],
                                        "required": True, "timeout_seconds": 10 ** 400}}
            with mock.patch("scripts.research_support.handoff.subprocess.run") as launch:
                report = inspect_handoff(manifest, root, actor="receiver", phase="receiver", probe=True)
            launch.assert_not_called()
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(report["environment_probe"]["status"], "fail")

    def test_probe_budget_can_exceed_old_limit_and_keeps_task_basis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"schema_version": "research-support-handoff-v1", "id": "slow-cold-start",
                        "artifacts": [{"id": "cache", "path": "optional.bin", "role": "cache",
                                       "required": False, "use": "optional cache"}],
                        "environment": {"python": sys.executable, "cwd": ".",
                        "imports": ["json"], "required": True, "timeout_seconds": 180,
                        "timeout_basis": "Measured cold environment for this task"}}
            report = inspect_handoff(manifest, root, actor="receiver", phase="receiver", probe=True)
            self.assertEqual(report["overall"], "pass")
            self.assertEqual(report["environment_probe"]["timeout_seconds"], 180)
            self.assertEqual(report["environment_probe"]["timeout_basis"], manifest["environment"]["timeout_basis"])
            self.assertFalse(report["environment_probe"]["permission_changes"])

    def test_probe_interrupt_keeps_required_task_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"schema_version": "research-support-handoff-v1", "id": "interrupted-probe",
                        "artifacts": [{"id": "cache", "path": "optional.bin", "role": "cache",
                                       "required": False, "use": "optional cache"}],
                        "environment": {"python": sys.executable, "cwd": ".",
                        "imports": ["json"], "required": True}}
            with mock.patch("scripts.research_support.handoff.subprocess.run", side_effect=KeyboardInterrupt()):
                report = inspect_handoff(manifest, root, actor="receiver", phase="receiver", probe=True)
            self.assertEqual(report["overall"], "unknown")
            self.assertEqual(report["environment_probe"]["reason"], "probe_interrupted")
            self.assertEqual(report["scientific_acceptance"], "not_assessed")

    def test_path_resolution_accepts_nested_and_contained_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "inputs" / "sample.txt"
            nested.parent.mkdir()
            nested.write_text("sample", encoding="utf-8")
            self.assertEqual(resolve_path(root, "inputs/sample.txt"), nested.resolve())
            self.assertEqual(resolve_path(root, str(nested.resolve())), nested.resolve())

            outside = root.parent / (root.name + "-outside.txt")
            outside.write_text("outside", encoding="utf-8")
            try:
                with self.assertRaises(SupportError) as caught:
                    resolve_path(root, "../" + outside.name)
                self.assertEqual(caught.exception.status, "fail")
            finally:
                outside.unlink()

            with self.assertRaises(SupportError):
                resolve_path(root, ".")

    def test_load_json_rejects_duplicate_keys_and_fingerprint_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"x": 1, "x": 2}', encoding="utf-8")
            with self.assertRaises(SupportError) as caught:
                load_json(duplicate)
            self.assertEqual(caught.exception.status, "fail")

            non_object = root / "list.json"
            non_object.write_text("[1]", encoding="utf-8")
            with self.assertRaises(SupportError):
                load_json(non_object)

            sample = root / "sample.bin"
            sample.write_bytes(b"abcd")
            expected = {"bytes": 4, "sha256": hashlib.sha256(b"abcd").hexdigest()}
            self.assertEqual(fingerprint(sample), expected)
            with self.assertRaises(SupportError) as caught:
                fingerprint(sample, max_bytes=3)
            self.assertEqual(caught.exception.status, "unknown")
            with self.assertRaises(SupportError) as caught:
                fingerprint(root / "missing.bin")
            self.assertEqual(caught.exception.status, "unknown")

    def test_successful_receiver_probe_and_explicit_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "data" / "input.txt"
            artifact.parent.mkdir()
            artifact.write_text("payload", encoding="utf-8")
            identity = fingerprint(artifact)
            manifest = root / "handoff.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "research-support-handoff-v1",
                        "id": "handoff-success",
                        "artifacts": [
                            {
                                "id": "input",
                                "path": "data/input.txt",
                                "role": "input",
                                "required": True,
                                "use": "receiver input",
                                **identity,
                            },
                            {
                                "id": "cache",
                                "path": "cache/optional.bin",
                                "role": "cache",
                                "required": False,
                                "use": "optional cache",
                            },
                        ],
                        "environment": {
                            "python": sys.executable,
                            "cwd": ".",
                            "imports": ["json"],
                            "required": True,
                            "timeout_seconds": 5,
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = inspect_handoff(manifest, root, actor="receiver", phase="receiver", probe=True)
            self.assertEqual(report["overall"], "pass")
            self.assertEqual(report["actor"], "receiver")
            self.assertEqual(report["phase"], "receiver")
            self.assertEqual(report["file_counts"]["required"], 1)
            self.assertEqual(report["file_counts"]["cache"], 1)
            self.assertEqual(report["file_counts"]["cache_missing"], 1)
            self.assertEqual(report["environment_probe"]["status"], "pass")
            self.assertEqual(report["scientific_acceptance"], "not_assessed")

    def test_missing_identity_and_missing_cache_are_distinct_from_hash_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required_file = root / "required.txt"
            required_file.write_text("required", encoding="utf-8")
            manifest_data = {
                "schema_version": "research-support-handoff-v1",
                "id": "identity-cases",
                "artifacts": [
                    {
                        "id": "required",
                        "path": "required.txt",
                        "role": "result",
                        "required": True,
                        "use": "must be identified",
                    },
                    {
                        "id": "cache",
                        "path": "cache/missing.bin",
                        "role": "cache",
                        "required": False,
                        "use": "optional cache",
                    },
                ],
            }
            report = inspect_handoff(manifest_data, root, actor="producer", phase="producer")
            self.assertEqual(report["overall"], "unknown")
            states = {item["id"]: item["state"] for item in report["files"]}
            self.assertEqual(states["required"], "missing_expected_identity")
            self.assertEqual(states["cache"], "missing")
            self.assertEqual(report["file_counts"]["cache_missing"], 1)
            self.assertTrue(any(item["code"] == "required_identity_missing" for item in report["checks"]))

            conflict = dict(manifest_data)
            conflict["artifacts"] = [dict(manifest_data["artifacts"][0])]
            conflict["artifacts"][0].update({"sha256": "0" * 64, "bytes": 1})
            conflict_report = inspect_handoff(conflict, root, actor="producer", phase="producer")
            self.assertEqual(conflict_report["overall"], "fail")
            self.assertTrue(
                any(item["code"] == "artifact_identity_conflict" for item in conflict_report["checks"])
            )

    def test_missing_module_and_timeout_are_unknown_for_required_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = {
                "schema_version": "research-support-handoff-v1",
                "id": "missing-import",
                "artifacts": [
                    {
                        "id": "input",
                        "path": "input.txt",
                        "role": "input",
                        "required": False,
                        "use": "optional",
                    }
                ],
                "environment": {
                    "python": sys.executable,
                    "cwd": ".",
                    "imports": ["module_that_does_not_exist_for_handoff_test"],
                    "required": True,
                    "timeout_seconds": 5,
                },
            }
            missing_report = inspect_handoff(missing, root, actor="receiver", phase="receiver", probe=True)
            self.assertEqual(missing_report["overall"], "unknown")
            self.assertEqual(missing_report["environment_probe"]["status"], "unknown")
            self.assertEqual(missing_report["environment_probe"]["reason"], "dependency_import_failed")

            slow_module = root / "handoff_slow_module.py"
            slow_module.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            timeout_manifest = dict(missing)
            timeout_manifest["id"] = "timeout-import"
            timeout_manifest["environment"] = dict(missing["environment"])
            timeout_manifest["environment"].update(
                {"imports": ["handoff_slow_module"], "timeout_seconds": 0.1}
            )
            timeout_report = inspect_handoff(timeout_manifest, root, actor="receiver", phase="receiver", probe=True)
            self.assertEqual(timeout_report["overall"], "unknown")
            self.assertEqual(timeout_report["environment_probe"]["reason"], "timeout")

    def test_dump_new_and_cli_refuse_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "result.json"
            dump_new(target, {"ok": True})
            with self.assertRaises(FileExistsError):
                dump_new(target, {"ok": False})

            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "research-support-handoff-v1",
                        "id": "cli-overwrite",
                        "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--root",
                        str(root),
                        "--manifest",
                        "manifest.json",
                        "--actor",
                        "producer",
                        "--phase",
                        "producer",
                        "--out",
                        "manifest.json",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("output_overwrites_input", stdout.getvalue())
            self.assertIn('"artifacts": []', manifest.read_text(encoding="utf-8"))

    def test_cli_stdout_round_trips_multibyte_text_as_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "input.txt"
            artifact.write_text("内容", encoding="utf-8")
            identity = fingerprint(artifact)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "research-support-handoff-v1",
                        "id": "utf8-cli",
                        "artifacts": [
                            {
                                "id": "input",
                                "path": "input.txt",
                                "role": "input",
                                "required": True,
                                "use": "多字节回环",
                                **identity,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "research_support" / "handoff.py"),
                    "--root",
                    str(root),
                    "--manifest",
                    str(manifest),
                    "--actor",
                    "生产者",
                    "--phase",
                    "producer",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            decoded = completed.stdout.decode("utf-8")
            self.assertIn("多字节回环", decoded)
            self.assertIn("生产者", decoded)


if __name__ == "__main__":
    unittest.main()
