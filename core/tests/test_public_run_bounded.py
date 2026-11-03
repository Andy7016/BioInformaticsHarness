from __future__ import annotations

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "research_support" / "run_bounded.py"
HAS_PSUTIL = importlib.util.find_spec("psutil") is not None


@unittest.skipUnless(HAS_PSUTIL, "run_bounded integration tests require the optional psutil dependency")
class PublicRunBoundedTests(unittest.TestCase):
    def _invoke(self, root: Path, prefix: Path, child_argv: list[str], capture: list[str] | None = None) -> subprocess.CompletedProcess[bytes]:
        command = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(RUNNER),
            "--cwd",
            str(root),
            "--out-prefix",
            str(prefix),
            "--timeout-seconds",
            "10",
            "--memory-limit-mib",
            "256",
            "--threads",
            "1",
            "--sample-interval-seconds",
            "0.05",
        ]
        if capture is not None:
            command.extend(capture)
        command.extend(["--", *child_argv])
        return subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_child_reads_snapshot_before_work_and_receipt_binds_capture_after_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            child = root / "child.py"
            subject = root / "subject.py"
            config = root / "config.json"
            input_file = root / "input.bin"
            marker = Path(temporary) / "child-saw-snapshot.txt"
            child.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                "snapshot = Path(sys.argv[1])\n"
                "target = Path(sys.argv[2])\n"
                "marker = Path(sys.argv[3])\n"
                "payload = json.loads(snapshot.read_text(encoding='utf-8'))\n"
                "asset = next(item for item in payload['assets'] if item['source'] == 'subject.py')\n"
                "saved = snapshot.parent / asset['captured_path']\n"
                "assert saved.read_bytes() == target.read_bytes()\n"
                "marker.write_text('snapshot-before-child:' + payload['capture_id'], encoding='utf-8')\n"
                "target.write_bytes(b'changed after capture\\n')\n"
                "sys.exit(7 if sys.argv[4] == 'fail' else 0)\n",
                encoding="utf-8",
            )
            original_subject = b"# old selected source\n"
            subject.write_bytes(original_subject)
            config.write_bytes(b'{"mode":"count-only"}\n')
            input_file.write_bytes(b"input bytes")
            prefix = Path(temporary) / "nonzero-run"
            snapshot_path = Path(str(prefix) + ".capture") / "snapshot.json"

            completed = self._invoke(
                root,
                prefix,
                [sys.executable, "child.py", str(snapshot_path), "subject.py", str(marker), "fail"],
                [
                    "--capture-root",
                    str(root),
                    "--capture-code",
                    "child.py",
                    "--capture-code",
                    "subject.py",
                    "--capture-input",
                    "input.bin",
                    "--capture-config",
                    "config.json",
                ],
            )

            self.assertEqual(completed.returncode, 1, completed.stderr.decode("utf-8", "replace"))
            self.assertTrue(marker.is_file())
            self.assertTrue(marker.read_text(encoding="utf-8").startswith("snapshot-before-child:"))
            self.assertEqual(subject.read_bytes(), b"changed after capture\n")
            receipt_path = Path(str(prefix) + ".receipt.json")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            record = receipt["execution_capture"]
            self.assertEqual(receipt["result"]["kind"], "nonzero_exit")
            self.assertEqual(receipt["result"]["root_exit_code"], 7)
            self.assertEqual(record["capture_id"], snapshot["capture_id"])
            subject_record = next(item for item in record["selected_assets_after_execution"] if item["source"] == "subject.py")
            self.assertEqual(subject_record["status"], "changed")
            captured_subject = snapshot_path.parent / next(item for item in snapshot["assets"] if item["source"] == "subject.py")["captured_path"]
            self.assertEqual(captured_subject.read_bytes(), original_subject)

    def test_missing_asset_and_capture_conflict_do_not_launch_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            child = root / "child.py"
            marker = Path(temporary) / "should-not-exist.txt"
            child.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('launched', encoding='utf-8')\n",
                encoding="utf-8",
            )

            missing_prefix = Path(temporary) / "missing"
            missing = self._invoke(
                root,
                missing_prefix,
                [sys.executable, "child.py"],
                ["--capture-root", str(root), "--capture-code", "missing.py"],
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn(b"child was not started", missing.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(Path(str(missing_prefix) + ".receipt.json").exists())

            escape_prefix = Path(temporary) / "escape"
            escape = self._invoke(
                root,
                escape_prefix,
                [sys.executable, "child.py"],
                ["--capture-root", str(root), "--capture-code", "../child.py"],
            )
            self.assertEqual(escape.returncode, 2)
            self.assertIn(b"child was not started", escape.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(Path(str(escape_prefix) + ".receipt.json").exists())

            conflict_prefix = Path(temporary) / "conflict"
            Path(str(conflict_prefix) + ".capture").mkdir()
            conflict = self._invoke(
                root,
                conflict_prefix,
                [sys.executable, "child.py"],
                ["--capture-root", str(root), "--capture-code", "child.py"],
            )
            self.assertEqual(conflict.returncode, 2)
            self.assertIn(b"child was not started", conflict.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(Path(str(conflict_prefix) + ".receipt.json").exists())

    def test_inconsistent_capture_is_rejected_before_child_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            child = root / "child.py"
            subject = root / "subject.py"
            marker = Path(temporary) / "should-not-exist.txt"
            child.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('launched', encoding='utf-8')\n",
                encoding="utf-8",
            )
            subject.write_bytes(b"initial bytes")

            from scripts.research_support import execution_capture
            from scripts.research_support import run_bounded

            original = execution_capture._read_identity

            def mutate_then_capture(
                path: Path,
                *,
                expected_signature: object,
                destination: Path | None = None,
            ) -> dict[str, int | str]:
                # The capture root is resolved, e.g. /var -> /private/var on macOS.
                if path == subject.resolve():
                    path.write_bytes(b"changed during capture")
                return original(path, expected_signature=expected_signature, destination=destination)  # type: ignore[arg-type]

            prefix = Path(temporary) / "inconsistent"
            args = [
                "--cwd",
                str(root),
                "--out-prefix",
                str(prefix),
                "--capture-root",
                str(root),
                "--capture-code",
                "child.py",
                "--capture-code",
                "subject.py",
                "--",
                sys.executable,
                "child.py",
            ]
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with mock.patch.object(execution_capture, "_read_identity", side_effect=mutate_then_capture):
                with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                    exit_code = run_bounded.main(args)
            self.assertEqual(exit_code, 2)
            self.assertIn("child was not started", captured_stderr.getvalue())
            self.assertFalse(marker.exists())
            self.assertFalse(Path(str(prefix) + ".receipt.json").exists())

    def test_legacy_cli_runs_without_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "cwd"
            root.mkdir()
            prefix = Path(temporary) / "legacy"
            completed = self._invoke(root, prefix, [sys.executable, "-c", "print('legacy-ok')"])
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            self.assertIn(b"legacy-ok", Path(str(prefix) + ".stdout").read_bytes())
            receipt = json.loads(Path(str(prefix) + ".receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["result"]["kind"], "success")
            self.assertNotIn("execution_capture", receipt)
            self.assertFalse(Path(str(prefix) + ".capture").exists())


if __name__ == "__main__":
    unittest.main()
