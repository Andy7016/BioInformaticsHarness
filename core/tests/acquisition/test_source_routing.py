# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "acquisition" / "route_sources.py"
SPEC = importlib.util.spec_from_file_location("route_sources", SCRIPT)
assert SPEC and SPEC.loader
route_sources = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_sources)


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None, chunks=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._body = body
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=65536):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self._body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def close(self):
        pass


class SessionSequence:
    def __init__(self, responses):
        self.responses = list(responses)
        self.routes = []
        self.calls = []

    def __call__(self, route):
        self.routes.append(dict(route))
        response = self.responses.pop(0)
        return FakeSession(response, self.calls)


def quiet_config():
    config = route_sources.load_config()
    config["redirect_delay_seconds"] = 0
    config["request_interval_seconds"] = 0
    config["arxiv_interval_seconds"] = 0
    config["stream_wall_timeout_seconds"] = 5
    return config


class SourceRoutingTests(unittest.TestCase):
    def test_cli_plan_uses_core_local_config_in_original_and_relocated_roots_without_network(self):
        temp_parent = Path(os.environ.get("TEMP") or tempfile.gettempdir())
        with tempfile.TemporaryDirectory(dir=temp_parent) as temporary_name:
            temporary = Path(temporary_name)
            guard_dir = temporary / "network-guard"
            guard_dir.mkdir()
            (guard_dir / "sitecustomize.py").write_text(
                "import socket\n"
                "\n"
                "def _blocked(*args, **kwargs):\n"
                "    raise RuntimeError('network access forbidden in plan-only CLI test')\n"
                "\n"
                "socket.getaddrinfo = _blocked\n"
                "socket.socket.connect = _blocked\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(guard_dir) + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            def run_plan(root: Path, output: Path, expected_budget: int):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        "scripts/acquisition/route_sources.py",
                        "--doi",
                        "10.1371/journal.pone.0310814",
                        "--out",
                        str(output),
                    ],
                    cwd=root,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                plan = json.loads(completed.stdout)
                self.assertEqual(plan["mode"], "plan")
                self.assertEqual(plan["request_budget"], expected_budget)
                self.assertEqual(len(plan["requests"]), 4)
                self.assertEqual({item["route"] for item in plan["requests"]}, {"academic-direct"})
                self.assertEqual(plan["firecrawl_calls"], 0)
                self.assertFalse(plan["api_keys_used"])
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["mode"], "plan")

            core_output = temporary / "core-plan.json"
            run_plan(ROOT, core_output, expected_budget=8)

            relocated_root = temporary / "relocated-core"
            relocated_script = relocated_root / "scripts" / "acquisition" / "route_sources.py"
            relocated_config = relocated_root / "config" / "source-routing.json"
            relocated_script.parent.mkdir(parents=True)
            relocated_config.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPT, relocated_script)
            config = json.loads((ROOT / "config" / "source-routing.json").read_text(encoding="utf-8"))
            config["request_budget"] = 7
            relocated_config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            relocated_output = temporary / "relocated-plan.json"
            run_plan(relocated_root, relocated_output, expected_budget=7)

    def test_plan_is_default_shape_and_doi_plan_has_declared_fallbacks(self):
        with mock.patch.object(route_sources.socket, "getaddrinfo", side_effect=AssertionError("planning must not resolve hosts")):
            plan = route_sources.build_plan(doi="10.1371/journal.pone.0310814", config=quiet_config())
        self.assertEqual(plan["schema_version"], "source-routing-v1")
        self.assertEqual(plan["mode"], "plan")
        self.assertEqual([item["id"] for item in plan["requests"]], [
            "crossref-metadata",
            "europepmc-metadata",
            "ncbi-metadata",
            "openalex-doi-fallback",
        ])
        self.assertEqual(plan["firecrawl_calls"], 0)
        self.assertFalse(plan["api_keys_used"])

    def test_github_defaults_to_direct_without_configured_proxy(self):
        config = quiet_config()
        self.assertIsNone(config["github_proxy"])
        github = route_sources.route_url("https://raw.githubusercontent.com/a/b/main.py", config)
        self.assertEqual(github["service"], "github")
        self.assertEqual(github["mode"], "github-direct")
        self.assertEqual(github["network_mode"], "direct")
        self.assertIsNone(github["proxy"])
        session = route_sources.session_for_route(github, config)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies, {})
        session.close()

    def test_route_selects_explicit_github_proxy_and_academic_direct(self):
        config = quiet_config()
        config["github_proxy"] = "http://127.0.0.1:7890"
        github = route_sources.route_url("https://raw.githubusercontent.com/a/b/main.py", config)
        academic = route_sources.route_url("https://api.crossref.org/works/10.1/example", config)
        self.assertEqual(github["mode"], "github-proxy")
        self.assertEqual(github["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(academic["mode"], "academic-direct")
        self.assertIsNone(academic["proxy"])

    def test_session_configuration_disables_environment_and_retries(self):
        config = quiet_config()
        config["github_proxy"] = "http://127.0.0.1:7890"
        academic = route_sources.session_for_route(route_sources.route_url("https://api.crossref.org/works/x", config), config)
        github = route_sources.session_for_route(route_sources.route_url("https://github.com/a/b", config), config)
        self.assertFalse(academic.trust_env)
        self.assertEqual(academic.proxies, {})
        self.assertFalse(github.trust_env)
        self.assertEqual(github.proxies, {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
        academic.close()
        github.close()

    def test_signed_query_is_hidden_and_not_replayed_from_saved_plan(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/file?X-Goog-Signature=SECRET&x-goog-date=20261018",
            kind="supplement",
            expected_format="pdf",
            config=config,
        )
        encoded = json.dumps(plan)
        self.assertNotIn("SECRET", encoded)
        sessions = SessionSequence([])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=sessions)
        self.assertEqual(receipt["results"][0]["outcome"], "signed_query_not_replayable")
        self.assertEqual(sessions.routes, [])

    def test_budget_counts_http_responses_and_marks_remaining_requests(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/one",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        second = copy.deepcopy(plan["requests"][0])
        second["id"] = "second"
        second["url"] = "https://example.org/two"
        plan["requests"].append(second)
        sessions = SessionSequence([FakeResponse(body=b"{}")])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, max_requests=1, config=config, session_factory=sessions)
        self.assertEqual(receipt["requests_used"], 1)
        self.assertEqual([item["outcome"] for item in receipt["results"]], ["downloaded", "budget_exhausted"])
        self.assertEqual(len(sessions.calls), 1)

    def test_redirect_reselects_route_for_new_domain_and_counts_both_requests(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://api.github.com/repos/a/b/archive",
            kind="repository",
            expected_format="text",
            config=config,
        )
        sessions = SessionSequence([
            FakeResponse(status=302, headers={"Location": "https://example.org/source.txt"}),
            FakeResponse(status=200, body=b"source"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, max_requests=2, config=config, session_factory=sessions)
        result = receipt["results"][0]
        self.assertEqual(result["outcome"], "downloaded")
        self.assertEqual(result["http_requests"], 2)
        self.assertEqual([route["mode"] for route in sessions.routes], ["github-direct", "academic-direct"])
        self.assertEqual(result["redirects_followed"], 1)

    def test_403_stops_service_without_fallback_or_retry(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/one",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        second = copy.deepcopy(plan["requests"][0])
        second["id"] = "same-service-second"
        second["url"] = "https://example.org/two"
        plan["requests"].append(second)
        sessions = SessionSequence([FakeResponse(status=403, body=b"denied")])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, max_requests=8, config=config, session_factory=sessions)
        self.assertEqual(receipt["results"][0]["outcome"], "http_403")
        self.assertEqual(receipt["results"][1]["outcome"], "service_stopped")
        self.assertEqual(receipt["requests_used"], 1)
        self.assertEqual(len(sessions.calls), 1)

    def test_429_and_zero_quota_stop_service(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/one",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        second = copy.deepcopy(plan["requests"][0])
        second["id"] = "quota-second"
        second["url"] = "https://example.org/two"
        plan["requests"].append(second)
        sessions = SessionSequence([FakeResponse(status=429, body=b"rate limited")])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, max_requests=8, config=config, session_factory=sessions)
        self.assertEqual(receipt["results"][0]["outcome"], "http_429")
        self.assertEqual(receipt["results"][1]["outcome"], "service_stopped")
        self.assertIn("other:example.org", receipt["stopped_services"])

        quota_sessions = SessionSequence([FakeResponse(status=200, body=b"{}", headers={"X-RateLimit-Remaining": "0"})])
        with tempfile.TemporaryDirectory() as directory:
            quota_receipt = route_sources.execute_plan(
                plan, directory, max_requests=8, config=config, session_factory=quota_sessions
            )
        self.assertEqual(quota_receipt["results"][0]["outcome"], "downloaded")
        self.assertEqual(quota_receipt["results"][1]["outcome"], "service_stopped")

    def test_cache_hit_uses_prior_receipt_without_http(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/cache.json",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        first_sessions = SessionSequence([FakeResponse(body=b"{}")])
        with tempfile.TemporaryDirectory() as directory:
            first = route_sources.execute_plan(plan, directory, config=config, session_factory=first_sessions)
            second_sessions = SessionSequence([])
            second = route_sources.execute_plan(plan, directory, config=config, session_factory=second_sessions)
            self.assertEqual(first["results"][0]["outcome"], "downloaded")
            self.assertEqual(second["results"][0]["outcome"], "cache_hit")
            self.assertEqual(second_sessions.calls, [])

    def test_existing_file_is_preserved_without_http(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/old.json",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        with tempfile.TemporaryDirectory() as directory:
            target = route_sources._target_path(Path(directory), plan["requests"][0])
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old-original")
            sessions = SessionSequence([FakeResponse(body=b"{\"new\": true}")])
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=sessions)
            self.assertEqual(receipt["results"][0]["outcome"], "existing_file_preserved")
            self.assertEqual(target.read_bytes(), b"old-original")
            self.assertEqual(sessions.calls, [])

    def test_declared_and_stream_file_caps_leave_no_original(self):
        config = quiet_config()
        config["max_file_bytes"] = 3
        plan = route_sources.build_plan(
            url="https://example.org/big.json",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        declared = SessionSequence([FakeResponse(body=b"abcd", headers={"Content-Length": "4"})])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=declared)
            self.assertEqual(receipt["results"][0]["outcome"], "stopped_declared_size_over_cap")
            self.assertEqual(list((Path(directory) / "raw").glob("*")) if (Path(directory) / "raw").exists() else [], [])

        streamed = SessionSequence([FakeResponse(chunks=[b"ab", b"cd"])])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=streamed)
            self.assertEqual(receipt["results"][0]["outcome"], "stopped_stream_size_over_cap")
            self.assertEqual(list((Path(directory) / "raw").glob("*")) if (Path(directory) / "raw").exists() else [], [])

    def test_format_failure_is_not_marked_downloaded(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/not-json",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        sessions = SessionSequence([FakeResponse(body=b"not-json")])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=sessions)
            self.assertEqual(receipt["results"][0]["outcome"], "format_invalid")
            self.assertFalse((Path(directory) / "raw").exists())

    def test_transport_exception_is_one_attempt_with_no_retry(self):
        config = quiet_config()
        plan = route_sources.build_plan(
            url="https://example.org/fails",
            kind="metadata",
            expected_format="json",
            config=config,
        )
        sessions = SessionSequence([TimeoutError("simulated timeout")])
        with tempfile.TemporaryDirectory() as directory:
            receipt = route_sources.execute_plan(plan, directory, config=config, session_factory=sessions)
        self.assertEqual(receipt["results"][0]["outcome"], "request_exception")
        self.assertEqual(receipt["results"][0]["automatic_retries"], 0)
        self.assertEqual(receipt["requests_used"], 1)
        self.assertEqual(len(sessions.routes), 1)

    def test_unsafe_destinations_are_rejected_before_execution(self):
        with self.assertRaises(route_sources.RoutingError):
            route_sources.build_plan(url="file:///tmp/a", kind="metadata", expected_format="json")
        with self.assertRaises(route_sources.RoutingError):
            route_sources.build_plan(url="https://user:password@example.org/a", kind="metadata", expected_format="json")
        with self.assertRaises(route_sources.RoutingError):
            route_sources.build_plan(url="https://example.org/a?api_key=secret", kind="metadata", expected_format="json")
        with self.assertRaises(route_sources.RoutingError):
            route_sources.build_plan(url="http://127.0.0.1:9/a", kind="metadata", expected_format="json")


if __name__ == "__main__":
    unittest.main()
