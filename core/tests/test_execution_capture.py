from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support._common import SupportError  # noqa: E402
from scripts.research_support.execution_capture import prepare_capture  # noqa: E402


class ExecutionCaptureTests(unittest.TestCase):
    def test_exact_code_and_config_bytes_are_saved_and_input_is_only_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            out = Path(temporary) / "audit"
            code = root / "audit.py"
            config = root / "config.json"
            input_file = root / "dataset.bin"
            code_bytes = b"# exact source\nprint('ready')\n"
            config_bytes = b'{"mode":"read-only"}\n'
            input_bytes = bytes(range(256)) * 8192
            code.write_bytes(code_bytes)
            config.write_bytes(config_bytes)
            input_file.write_bytes(input_bytes)

            capture = prepare_capture(
                capture_root=root,
                out_prefix=out,
                code_paths=["audit.py"],
                config_path="config.json",
                input_path="dataset.bin",
                argv=[sys.executable, "audit.py"],
                cwd=root,
            )

            snapshot_bytes = capture.snapshot_path.read_bytes()
            snapshot = json.loads(snapshot_bytes.decode("utf-8"))
            self.assertEqual(snapshot["argv"], [sys.executable, "audit.py"])
            self.assertEqual(snapshot["cwd"], str(root.resolve()))
            self.assertEqual(snapshot["capture_id"], capture.capture_id)
            by_role = {item["role"]: item for item in snapshot["assets"]}
            self.assertEqual(set(by_role), {"code", "config", "input"})
            self.assertEqual(by_role["code"]["sha256"], hashlib.sha256(code_bytes).hexdigest())
            self.assertEqual(by_role["config"]["bytes"], len(config_bytes))
            self.assertEqual(by_role["input"]["sha256"], hashlib.sha256(input_bytes).hexdigest())
            self.assertIsNone(by_role["input"]["captured_path"])
            self.assertFalse((capture.directory / "assets" / "input").exists())
            self.assertEqual((capture.directory / by_role["code"]["captured_path"]).read_bytes(), code_bytes)
            self.assertEqual((capture.directory / by_role["config"]["captured_path"]).read_bytes(), config_bytes)

            code.write_bytes(b"changed source\n")
            config.write_bytes(b'{"mode":"changed"}\n')
            input_file.write_bytes(b"changed input")
            receipt = capture.receipt_record()
            after = {(item["role"], item["source"]): item["status"] for item in receipt["selected_assets_after_execution"]}
            self.assertEqual(after[("code", "audit.py")], "changed")
            self.assertEqual(after[("config", "config.json")], "changed")
            self.assertEqual(after[("input", "dataset.bin")], "changed")
            self.assertEqual(capture.snapshot_path.read_bytes(), snapshot_bytes)

    def test_paths_are_root_relative_and_existing_links_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            (root / "audit.py").write_text("print('ok')\n", encoding="utf-8")
            outside = Path(temporary) / "outside.py"
            outside.write_text("print('outside')\n", encoding="utf-8")

            for bad_path in ("../outside.py", str(outside), "missing.py"):
                with self.subTest(path=bad_path):
                    with self.assertRaises(SupportError):
                        prepare_capture(
                            capture_root=root,
                            out_prefix=Path(temporary) / ("bad-" + str(abs(hash(bad_path)))),
                            code_paths=[bad_path],
                            argv=[sys.executable, "audit.py"],
                            cwd=root,
                        )

            hard_link = root / "hard-linked.py"
            os.link(root / "audit.py", hard_link)
            with self.assertRaises(SupportError):
                prepare_capture(
                    capture_root=root,
                    out_prefix=Path(temporary) / "hard-linked",
                    code_paths=["hard-linked.py"],
                    argv=[sys.executable, "audit.py"],
                    cwd=root,
                )

            link = root / "linked.py"
            try:
                os.symlink(root / "audit.py", link)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(SupportError):
                    prepare_capture(
                        capture_root=root,
                        out_prefix=Path(temporary) / "linked",
                        code_paths=["linked.py"],
                        argv=[sys.executable, "audit.py"],
                        cwd=root,
                    )

    def test_python_script_must_be_in_selected_code_and_capture_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            (root / "child.py").write_text("print('child')\n", encoding="utf-8")
            (root / "other.py").write_text("print('other')\n", encoding="utf-8")
            prefix = Path(temporary) / "run"
            with self.assertRaises(SupportError):
                prepare_capture(
                    capture_root=root,
                    out_prefix=prefix,
                    code_paths=["other.py"],
                    argv=[sys.executable, "child.py"],
                    cwd=root,
                )
            capture = prepare_capture(
                capture_root=root,
                out_prefix=prefix,
                code_paths=["child.py"],
                argv=[sys.executable, "child.py"],
                cwd=root,
            )
            original = capture.snapshot_path.read_bytes()
            with self.assertRaises(SupportError):
                prepare_capture(
                    capture_root=root,
                    out_prefix=prefix,
                    code_paths=["child.py"],
                    argv=[sys.executable, "child.py"],
                    cwd=root,
                )
            self.assertEqual(capture.snapshot_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
