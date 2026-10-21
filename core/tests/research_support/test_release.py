from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support.release import inspect_release, publish_handoff  # noqa: E402
from scripts.research_support._common import SupportError  # noqa: E402


def _draft(root: Path, *, identity: dict | None = None) -> Path:
    payload = root / "inputs" / "实际输入.txt"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_text("中文 payload\n", encoding="utf-8")
    artifact = {
        "id": "input",
        "path": "inputs/实际输入.txt",
        "role": "input",
        "required": True,
        "use": "receiver input",
    }
    if identity:
        artifact.update(identity)
    draft = root / "draft.json"
    draft.write_text(
        json.dumps(
            {
                "schema_version": "research-support-handoff-v1",
                "id": "release-fixture",
                "artifacts": [
                    artifact,
                    {
                        "id": "optional-cache",
                        "path": "cache/missing.bin",
                        "role": "cache",
                        "required": False,
                        "use": "optional local cache",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return draft


class ReleaseSupportTests(unittest.TestCase):
    def test_publish_receive_and_root_move_use_same_entry_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="release 中文 ") as directory:
            root = Path(directory) / "中文 root"
            root.mkdir()
            _draft(root)
            (root / "published").mkdir()

            published = publish_handoff(
                root,
                "draft.json",
                "published/handoff.json",
                "producer-一",
                True,
            )
            self.assertEqual(published["overall"], "pass")
            self.assertEqual(published["publication"]["entry_path"], "published/handoff.json")
            self.assertTrue(published["publication"]["identity_observations"])
            self.assertTrue((root / "published/handoff.json").is_file())
            entry_sha = published["entry_identity"]["sha256"]

            moved = Path(directory) / "moved root"
            shutil.copytree(root, moved)
            received = inspect_release(
                moved,
                "published/handoff.json",
                actor="independent-receiver",
                expected_entry_sha256=entry_sha,
            )
            self.assertEqual(received["overall"], "pass")
            self.assertEqual(received["phase"], "receiver")
            self.assertEqual(received["entry_identity"]["sha256"], entry_sha)
            self.assertEqual(received["handoff_report"]["overall"], "pass")
            self.assertEqual(received["scientific_acceptance"], "not_assessed")

    def test_payload_mutation_fails_receiver_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _draft(root)
            (root / "published").mkdir()
            published = publish_handoff(root, "draft.json", "published/handoff.json", "producer", True)
            self.assertEqual(published["overall"], "pass")
            (root / "inputs" / "实际输入.txt").write_text("mutated\n", encoding="utf-8")
            received = inspect_release(root, "published/handoff.json")
            self.assertEqual(received["overall"], "fail")
            self.assertTrue(
                any(item["code"] == "artifact_identity_conflict" for item in received["handoff_report"]["checks"])
            )

    def test_missing_writer_and_declared_identity_conflict_do_not_write_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "inputs.txt"
            payload.write_text("payload", encoding="utf-8")
            wrong = {"bytes": payload.stat().st_size + 1, "sha256": "0" * 64}
            _draft(root, identity=wrong)
            (root / "published").mkdir()
            missing = publish_handoff(root, "draft.json", "published/handoff.json", "producer", False)
            self.assertEqual(missing["overall"], "fail")
            self.assertFalse((root / "published/handoff.json").exists())

            conflict = publish_handoff(root, "draft.json", "published/handoff.json", "producer", True)
            self.assertEqual(conflict["overall"], "fail")
            self.assertFalse((root / "published/handoff.json").exists())
            self.assertTrue(any(item["code"] == "artifact_identity_conflict" for item in conflict["checks"]))

    def test_existing_entry_and_expected_sha_are_exclusive_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _draft(root)
            (root / "published").mkdir()
            existing = root / "published/handoff.json"
            existing.write_text('{"occupied": true}\n', encoding="utf-8")
            report = publish_handoff(root, "draft.json", "published/handoff.json", "producer", True)
            self.assertEqual(report["overall"], "fail")
            self.assertTrue(any(item["code"] == "entry_exists" for item in report["checks"]))
            self.assertEqual(existing.read_text(encoding="utf-8"), '{"occupied": true}\n')

    def test_required_missing_is_unknown_and_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft = root / "draft.json"
            draft.write_text(
                json.dumps(
                    {
                        "schema_version": "research-support-handoff-v1",
                        "id": "missing-required",
                        "artifacts": [
                            {
                                "id": "missing",
                                "path": "inputs/missing.txt",
                                "role": "input",
                                "required": True,
                                "use": "required input",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "published").mkdir()
            report = publish_handoff(root, draft, "published/handoff.json", "producer", True)
            self.assertEqual(report["overall"], "unknown")
            self.assertTrue(any(item["code"] == "required_artifact_missing" for item in report["checks"]))
            self.assertFalse((root / "published/handoff.json").exists())

    def test_unknown_fingerprint_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _draft(root)
            (root / "published").mkdir()

            def unavailable(_path: Path) -> dict:
                raise SupportError("bounded fingerprint unavailable", "unknown")

            with patch("scripts.research_support.release.fingerprint", side_effect=unavailable):
                report = publish_handoff(root, "draft.json", "published/handoff.json", "producer", True)
            self.assertEqual(report["overall"], "unknown")
            self.assertTrue(any(item["code"] == "artifact_fingerprint_unavailable" for item in report["checks"]))
            self.assertFalse((root / "published/handoff.json").exists())

    def test_unsupported_exclusive_link_is_unknown_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _draft(root)
            target_dir = root / "published"
            target_dir.mkdir()

            def unsupported(_src: str, _dst: str) -> None:
                raise OSError("hardlinks unsupported")

            with patch("scripts.research_support.release.os.link", side_effect=unsupported):
                report = publish_handoff(root, "draft.json", "published/handoff.json", "producer", True)
            self.assertEqual(report["overall"], "unknown")
            self.assertTrue(any(item["code"] == "entry_publish_failed" for item in report["checks"]))
            self.assertFalse((target_dir / "handoff.json").exists())
            self.assertEqual(list(target_dir.glob(".*.release-tmp")), [])

    def test_cli_publish_and_receive_emit_utf8_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _draft(root)
            (root / "published").mkdir()
            command = [
                sys.executable,
                "-m",
                "scripts.research_support.release",
                "publish",
                "--root",
                str(root),
                "--draft",
                "draft.json",
                "--entry",
                "published/handoff.json",
                "--actor",
                "生产者",
                "--writer-complete",
            ]
            completed = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            decoded = completed.stdout.decode("utf-8")
            report = json.loads(decoded)
            self.assertEqual(report["overall"], "pass")
            self.assertIn("生产者", decoded)
            received = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.research_support.release",
                    "receive",
                    "--root",
                    str(root),
                    "--entry",
                    "published/handoff.json",
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(received.returncode, 0, received.stderr.decode("utf-8", "replace"))
            self.assertEqual(json.loads(received.stdout.decode("utf-8"))["overall"], "pass")


if __name__ == "__main__":
    unittest.main()
