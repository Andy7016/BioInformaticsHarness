# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from scripts.research_support import plan_seal
from scripts.research_support._common import SupportError, dump_new, is_root_controlled_link
from scripts.research_support.plan_seal import (
    PUBLIC_APPLICATIONS_PROTOCOL,
    STANDARD_PROTOCOL,
    build_ready,
    main,
    seal_plan,
    validate_ready,
)


class PlanSealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = self.root / "plan-v1"
        self.plan.mkdir()
        (self.plan / "PLAN.md").write_text("A frozen plan.\n", encoding="utf-8")
        (self.plan / "notes").mkdir()
        (self.plan / "notes" / "scope.txt").write_text("Bounded scope.\n", encoding="utf-8")
        self.archive = self.root / "run" / "first-plan"
        (self.root / "run").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ready(self, protocol: str = STANDARD_PROTOCOL) -> dict:
        payload = build_ready(self.plan, "fixture-case", protocol)
        dump_new(self.plan / "READY.json", payload)
        return payload

    def assert_invalid(self, ready: dict, message: str) -> None:
        with self.assertRaisesRegex(SupportError, message):
            validate_ready(self.plan, ready, STANDARD_PROTOCOL)

    def test_cli_prepare_check_then_seal_and_repeat(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                main(
                    [
                        "prepare",
                        "--plan-root",
                        str(self.plan),
                        "--case-id",
                        "fixture-case",
                    ]
                ),
                0,
            )
        prepared = json.loads(stdout.getvalue())
        self.assertEqual(prepared["manifest"]["schema"], "research-support.first-plan-ready.v1")
        self.assertEqual(
            [item["path"] for item in prepared["manifest"]["files"]],
            ["PLAN.md", "notes/scope.txt"],
        )
        self.assertNotIn("READY.json", {item["path"] for item in prepared["manifest"]["files"]})

        checked = io.StringIO()
        with contextlib.redirect_stdout(checked):
            self.assertEqual(
                main(["check", "--plan-root", str(self.plan)]),
                0,
            )
        self.assertEqual(json.loads(checked.getvalue())["status"], "valid")

        sealed = io.StringIO()
        with contextlib.redirect_stdout(sealed):
            self.assertEqual(
                main(
                    [
                        "seal",
                        "--plan-root",
                        str(self.plan),
                        "--archive-root",
                        str(self.archive),
                        "--case-id",
                        "fixture-case",
                    ]
                ),
                0,
            )
        first = json.loads(sealed.getvalue())
        self.assertEqual(first["status"], "archived")
        ack = json.loads((self.plan / "ARCHIVED.json").read_text(encoding="utf-8"))
        self.assertEqual(ack["schema"], "research-support.first-plan-archived.v1")
        self.assertEqual(ack["status"], "archived")
        self.assertEqual(
            {row["path"] for row in ack["sealed_files"]},
            {"PLAN.md", "notes/scope.txt"},
        )
        self.assertEqual(
            (self.plan / "READY.json").read_bytes(),
            (self.archive / "READY.json").read_bytes(),
        )
        self.assertEqual(
            (self.plan / "PLAN.md").read_bytes(),
            (self.archive / "PLAN.md").read_bytes(),
        )

        second = seal_plan(self.plan, self.archive, "fixture-case", STANDARD_PROTOCOL)
        self.assertEqual(second["status"], "already_archived")
        self.assertEqual(second["acknowledgement"], ack)
        self.assertEqual((self.archive / "notes" / "scope.txt").read_text(encoding="utf-8"), "Bounded scope.\n")

    def test_public_applications_protocol_uses_explicit_schema(self) -> None:
        payload = build_ready(self.plan, "splice", PUBLIC_APPLICATIONS_PROTOCOL)
        self.assertEqual(payload["schema"], "public-applications.first-plan-ready.v1")
        self.assertEqual(
            set(payload),
            {"schema", "case_id", "files"},
        )
        self.assertEqual(set(payload["files"][0]), {"path", "bytes", "sha256"})
        dump_new(self.plan / "READY.json", payload)
        self.assertEqual(
            validate_ready(self.plan, payload, PUBLIC_APPLICATIONS_PROTOCOL),
            payload["files"],
        )

    def test_validation_rejects_schema_and_structure_errors(self) -> None:
        payload = build_ready(self.plan, "fixture-case")
        bad = dict(payload, schema="unknown.v1")
        self.assert_invalid(bad, "schema mismatch")

        bad = dict(payload, unexpected=True)
        self.assert_invalid(bad, "unknown fields")

        bad = dict(payload, files=[dict(payload["files"][0], extra="no")])
        self.assert_invalid(bad, "fields invalid")

        bad = dict(payload, files=[dict(payload["files"][0], bytes=True)])
        self.assert_invalid(bad, "non-negative integer")

    def test_validation_rejects_duplicate_escape_root_prefix_and_unsorted_paths(self) -> None:
        payload = build_ready(self.plan, "fixture-case")

        duplicate = dict(payload, files=[*payload["files"], dict(payload["files"][0])])
        self.assert_invalid(duplicate, "duplicate READY path")

        traversal = dict(payload, files=[dict(payload["files"][0], path="../outside.txt")])
        self.assert_invalid(traversal, "canonical relative path")

        absolute = dict(payload, files=[dict(payload["files"][0], path=str(self.plan / "PLAN.md"))])
        self.assert_invalid(absolute, "must be relative")

        repeated_root = dict(payload, files=[dict(payload["files"][0], path="plan-v1/PLAN.md")])
        self.assert_invalid(repeated_root, "repeats the explicit plan_root prefix")

        unsorted = dict(payload, files=list(reversed(payload["files"])))
        self.assert_invalid(unsorted, "not in canonical sorted order")

    def test_validation_rejects_missing_extra_and_changed_identities(self) -> None:
        payload = build_ready(self.plan, "fixture-case")
        (self.plan / "notes" / "scope.txt").unlink()
        with self.assertRaisesRegex(SupportError, "READY lists missing plan files"):
            validate_ready(self.plan, payload, STANDARD_PROTOCOL)

        (self.plan / "notes" / "scope.txt").write_text("Bounded scope.\n", encoding="utf-8")
        (self.plan / "extra.txt").write_text("unlisted\n", encoding="utf-8")
        with self.assertRaisesRegex(SupportError, "READY omits plan files"):
            validate_ready(self.plan, payload, STANDARD_PROTOCOL)

        (self.plan / "extra.txt").unlink()
        changed = dict(payload, files=[dict(item) for item in payload["files"]])
        changed["files"][0]["bytes"] += 1
        with self.assertRaisesRegex(SupportError, "bytes expected"):
            validate_ready(self.plan, changed, STANDARD_PROTOCOL)

        changed = dict(payload, files=[dict(item) for item in payload["files"]])
        changed["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(SupportError, "sha256 expected"):
            validate_ready(self.plan, changed, STANDARD_PROTOCOL)

    def test_seal_validates_before_creating_archive_or_ack(self) -> None:
        self.ready()
        (self.plan / "PLAN.md").write_text("changed after READY\n", encoding="utf-8")
        with self.assertRaisesRegex(SupportError, "identity mismatch"):
            seal_plan(self.plan, self.archive, "fixture-case", STANDARD_PROTOCOL)
        self.assertFalse(self.archive.exists())
        self.assertFalse((self.plan / "ARCHIVED.json").exists())

    def test_conflicting_existing_archive_and_ack_are_not_overwritten(self) -> None:
        self.ready()
        self.archive.mkdir()
        (self.archive / "PLAN.md").write_text("different\n", encoding="utf-8")
        with self.assertRaisesRegex(SupportError, "existing archive conflicts"):
            seal_plan(self.plan, self.archive, "fixture-case", STANDARD_PROTOCOL)
        self.assertEqual((self.archive / "PLAN.md").read_text(encoding="utf-8"), "different\n")
        self.assertFalse((self.plan / "ARCHIVED.json").exists())

        (self.archive / "PLAN.md").unlink()
        self.archive.rmdir()
        seal_plan(self.plan, self.archive, "fixture-case", STANDARD_PROTOCOL)
        ack_path = self.plan / "ARCHIVED.json"
        ack = json.loads(ack_path.read_text(encoding="utf-8"))
        ack["case_id"] = "changed"
        ack_path.write_text(json.dumps(ack), encoding="utf-8")
        with self.assertRaisesRegex(SupportError, "existing ARCHIVED.json conflicts"):
            seal_plan(self.plan, self.archive, "fixture-case", STANDARD_PROTOCOL)

    def test_plan_root_must_be_absolute_and_protocol_is_explicit(self) -> None:
        with self.assertRaisesRegex(SupportError, "plan_root must be absolute"):
            build_ready(Path("relative-plan"), "fixture-case")
        with self.assertRaisesRegex(SupportError, "unsupported protocol"):
            build_ready(self.plan, "fixture-case", "guess")

    def test_symlinked_plan_root_and_member_are_refused(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
        root_link = self.root / "linked-plan"
        member_link = self.plan / "linked-dir"
        try:
            os.symlink(outside, root_link, target_is_directory=True)
            os.symlink(outside, member_link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaisesRegex(SupportError, "symlink or reparse"):
            build_ready(root_link, "fixture-case")
        with self.assertRaisesRegex(SupportError, "symlink or reparse"):
            build_ready(self.plan, "fixture-case")

    def test_user_owned_ancestor_link_is_refused(self) -> None:
        outside = self.root / "outside"
        (outside / "plan").mkdir(parents=True)
        (outside / "plan" / "PLAN.md").write_text("A frozen plan.\n", encoding="utf-8")
        ancestor_link = self.root / "linked-parent"
        try:
            os.symlink(outside, ancestor_link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        if os.name == "posix" and os.geteuid() == 0:
            self.skipTest("a root-created link is root-controlled by design")

        self.assertFalse(is_root_controlled_link(ancestor_link, ancestor_link.lstat()))
        with self.assertRaisesRegex(SupportError, "symlink or reparse"):
            build_ready(ancestor_link / "plan", "fixture-case")

    @unittest.skipUnless(
        os.name == "posix" and Path("/var").is_symlink(),
        "requires a system /var link, as on macOS",
    )
    def test_root_controlled_system_link_is_accepted(self) -> None:
        system_link = Path("/var")
        self.assertTrue(is_root_controlled_link(system_link, system_link.lstat()))


def _system_alias_bases() -> list[Path]:
    """Existing writable temporary bases whose spelling crosses a system link."""

    bases = []
    for value in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        base = Path(value)
        if base.is_dir() and base.resolve() != base and os.access(base, os.W_OK):
            bases.append(base)
    return bases


@unittest.skipUnless(_system_alias_bases(), "requires a system-link temp alias, as on macOS")
class PhysicalAliasOverlapTests(unittest.TestCase):
    """L is a path through a system link; P is the same directory resolved."""

    def make_fixture(self, alias_base: Path) -> tuple[Path, Path, Path, Path]:
        alias = Path(tempfile.mkdtemp(dir=alias_base))
        self.addCleanup(shutil.rmtree, alias.resolve(), True)
        physical = alias.resolve()
        self.assertNotEqual(alias, physical)
        plan_alias = alias / "plan"
        plan_alias.mkdir()
        (plan_alias / "PLAN.md").write_text("A frozen plan.\n", encoding="utf-8")
        (plan_alias / "notes").mkdir()
        (plan_alias / "notes" / "scope.txt").write_text("Bounded scope.\n", encoding="utf-8")
        dump_new(plan_alias / "READY.json", build_ready(plan_alias, "fixture-case"))
        return alias, physical, plan_alias, physical / "plan"

    def snapshot(self, root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def assert_refused_before_writes(self, plan: Path, archive: Path, base: Path) -> None:
        before = self.snapshot(base)
        with mock.patch.object(plan_seal.tempfile, "mkdtemp", wraps=tempfile.mkdtemp) as stage, \
                mock.patch.object(plan_seal, "_copy_one", wraps=plan_seal._copy_one) as copy, \
                mock.patch.object(plan_seal, "dump_new", wraps=plan_seal.dump_new) as ack:
            with self.assertRaisesRegex(SupportError, "physical paths overlap"):
                seal_plan(plan, archive, "fixture-case", STANDARD_PROTOCOL)
        self.assertEqual((stage.call_count, copy.call_count, ack.call_count), (0, 0, 0))
        self.assertEqual(self.snapshot(base), before)

    def test_overlapping_alias_spellings_are_refused_before_any_write(self) -> None:
        for alias_base in _system_alias_bases():
            for swapped in (False, True):
                rows = {
                    "same-physical-root": lambda a, p, pa, pp: pp,
                    "archive-physical-descendant": lambda a, p, pa, pp: pp / "archive-new",
                    "archive-physical-ancestor": lambda a, p, pa, pp: p,
                }
                for row, archive_for in rows.items():
                    with self.subTest(base=str(alias_base), swapped=swapped, row=row):
                        alias, physical, plan_alias, plan_physical = self.make_fixture(alias_base)
                        plan, archive = plan_alias, archive_for(alias, physical, plan_alias, plan_physical)
                        if swapped:
                            plan = plan_physical
                            archive = Path(str(archive).replace(str(physical), str(alias), 1))
                        self.assert_refused_before_writes(plan, archive, physical)
                        self.assertFalse((plan_physical / "ARCHIVED.json").exists())

    def test_distinct_sibling_through_system_link_seals_an_independent_copy(self) -> None:
        for alias_base in _system_alias_bases():
            with self.subTest(base=str(alias_base)):
                alias, physical, plan_alias, plan_physical = self.make_fixture(alias_base)
                archive = physical / "archive-new"
                first = seal_plan(plan_alias, archive, "fixture-case", STANDARD_PROTOCOL)
                self.assertEqual(first["status"], "archived")
                self.assertFalse(os.path.samefile(plan_physical, archive))
                for name in ("READY.json", "PLAN.md", "notes/scope.txt"):
                    source, copied = plan_physical / name, archive / name
                    self.assertEqual(source.read_bytes(), copied.read_bytes())
                    self.assertFalse(os.path.samefile(source, copied))
                    self.assertEqual(copied.stat().st_nlink, 1)
                ack_bytes = (plan_physical / "ARCHIVED.json").read_bytes()
                second = seal_plan(plan_alias, archive, "fixture-case", STANDARD_PROTOCOL)
                self.assertEqual(second["status"], "already_archived")
                self.assertEqual((plan_physical / "ARCHIVED.json").read_bytes(), ack_bytes)

    def test_user_owned_link_to_archive_parent_is_still_refused(self) -> None:
        alias_base = _system_alias_bases()[0]
        alias, physical, plan_alias, plan_physical = self.make_fixture(alias_base)
        outside = physical / "outside"
        outside.mkdir()
        link = alias / "linked-parent"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.assertRaisesRegex(SupportError, "symlink or reparse"):
            seal_plan(plan_alias, link / "archive-new", "fixture-case", STANDARD_PROTOCOL)
        self.assertEqual(list(outside.iterdir()), [])


class CaseInsensitiveOverlapTests(unittest.TestCase):
    def test_case_variant_of_plan_root_is_refused_as_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            plan = root / "plan"
            plan.mkdir()
            if not (root / "PLAN").exists():
                self.skipTest("file system is case-sensitive")
            (plan / "PLAN.md").write_text("A frozen plan.\n", encoding="utf-8")
            dump_new(plan / "READY.json", build_ready(plan, "fixture-case"))
            with self.assertRaisesRegex(SupportError, r"(?:must not contain one another|physical paths overlap)"):
                seal_plan(plan, root / "PLAN", "fixture-case", STANDARD_PROTOCOL)
            self.assertFalse((plan / "ARCHIVED.json").exists())


if __name__ == "__main__":
    unittest.main()
