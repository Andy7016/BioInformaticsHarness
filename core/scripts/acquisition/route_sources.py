#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded, source-aware acquisition routing.

The module deliberately keeps planning separate from execution.  Planning
only validates and describes requests.  Execution is opt-in, uses one HTTP
request at a time, counts every response (including redirects), disables
environment proxies for academic traffic, and selects the GitHub proxy from
the destination host on every hop.

The public Python entry points are :func:`build_plan`, :func:`execute_plan`,
:func:`route_url`, and :func:`load_config`.  The command line interface is
intentionally small so that a saved plan is easy to audit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

try:  # requests is already used by the repository's acquisition checks.
    import requests
except ImportError:  # pragma: no cover - exercised through the dependency receipt
    requests = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "source-routing.json"
SCHEMA_VERSION = "source-routing-v1"
SIGNED_QUERY_KEYS = {
    "x-goog-signature",
    "x-goog-credential",
    "x-goog-date",
    "x-goog-algorithm",
    "x-goog-expires",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-date",
    "x-amz-expires",
    "x-amz-security-token",
    "signature",
    "sig",
    "token",
}
CREDENTIAL_QUERY_KEYS = {
    "api-key",
    "api_key",
    "apikey",
    "access-token",
    "access_token",
    "authorization",
    "client-secret",
    "client_secret",
    "password",
    "passwd",
    "secret",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
FORMAT_SUFFIXES = {"json": ".json", "xml": ".xml", "pdf": ".pdf", "html": ".html", "text": ".txt"}
SUPPORTED_KINDS = {"metadata", "fulltext", "supplement", "review", "repository"}
SUPPORTED_FORMATS = set(FORMAT_SUFFIXES)
MAX_REQUEST_BUDGET = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
QUOTA_HEADERS = {
    "x-ratelimit-remaining",
    "x-rate-limit-remaining",
    "x-quota-remaining",
    "ratelimit-remaining",
}


class RoutingError(ValueError):
    """An invalid plan or unsafe destination."""


class DependencyMissing(RuntimeError):
    """Execution was requested without the optional requests dependency."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_budget": 8,
        "max_file_bytes": 8 * 1024 * 1024,
        "connect_timeout_seconds": 5,
        "read_timeout_seconds": 15,
        "stream_wall_timeout_seconds": 30,
        "max_redirects": 3,
        "request_interval_seconds": 1.0,
        "arxiv_interval_seconds": 3.0,
        "arxiv_domains": ["arxiv.org", "export.arxiv.org"],
        "redirect_delay_seconds": 0.0,
        "github_proxy": "http://127.0.0.1:7890",
        "github_domains": [
            "github.com",
            "api.github.com",
            "raw.githubusercontent.com",
            "githubusercontent.com",
            "codeload.github.com",
            "objects.githubusercontent.com",
            "gist.github.com",
        ],
        "service_domains": {
            "crossref": ["api.crossref.org"],
            "europepmc": ["www.ebi.ac.uk", "europepmc.org", "www.europepmc.org"],
            "ncbi": ["eutils.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"],
            "openalex": ["api.openalex.org"],
            "github": [
                "github.com",
                "api.github.com",
                "raw.githubusercontent.com",
                "githubusercontent.com",
                "codeload.github.com",
                "objects.githubusercontent.com",
                "gist.github.com",
            ],
        },
        "user_agent": "BioInformaticsHarness-source-routing/1.0",
    }


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and validate the routing configuration without changing globals."""

    config = _default_config()
    selected = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if selected.exists():
        try:
            supplied = json.loads(selected.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RoutingError(f"cannot read routing config: {type(exc).__name__}: {exc}") from exc
        if not isinstance(supplied, dict):
            raise RoutingError("routing config must be a JSON object")
        config.update(supplied)
    if config.get("schema_version") != SCHEMA_VERSION:
        raise RoutingError(f"unsupported routing config schema: {config.get('schema_version')!r}")
    for key in ("request_budget", "max_file_bytes", "max_redirects"):
        if not isinstance(config.get(key), int) or config[key] <= 0:
            raise RoutingError(f"config field {key!r} must be a positive integer")
    if config["request_budget"] > MAX_REQUEST_BUDGET:
        raise RoutingError(f"config field 'request_budget' cannot exceed {MAX_REQUEST_BUDGET}")
    if config["max_file_bytes"] > MAX_FILE_BYTES:
        raise RoutingError(f"config field 'max_file_bytes' cannot exceed {MAX_FILE_BYTES} bytes")
    if config["max_redirects"] > MAX_REDIRECTS:
        raise RoutingError(f"config field 'max_redirects' cannot exceed {MAX_REDIRECTS}")
    for key in (
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "stream_wall_timeout_seconds",
        "request_interval_seconds",
        "arxiv_interval_seconds",
    ):
        if not isinstance(config.get(key), (int, float)) or config[key] <= 0:
            raise RoutingError(f"config field {key!r} must be positive")
    if not isinstance(config.get("redirect_delay_seconds"), (int, float)) or config["redirect_delay_seconds"] < 0:
        raise RoutingError("config field 'redirect_delay_seconds' must be non-negative")
    if not isinstance(config.get("github_proxy"), str) or not config["github_proxy"]:
        raise RoutingError("config field 'github_proxy' must be a non-empty URL")
    for key in ("github_domains",):
        values = config.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(x, str) and x for x in values):
            raise RoutingError(f"config field {key!r} must be a non-empty string list")
    services = config.get("service_domains")
    if not isinstance(services, dict) or not all(
        isinstance(name, str) and isinstance(domains, list) and domains and all(isinstance(x, str) for x in domains)
        for name, domains in services.items()
    ):
        raise RoutingError("config field 'service_domains' must map service names to string lists")
    return config


def _coerce_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fill a programmatic partial config without reading or writing globals."""

    if config is None:
        return load_config()
    merged = _default_config()
    merged.update(copy.deepcopy(dict(config)))
    return merged


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().lstrip(".").rstrip(".")
    return host == domain or host.endswith("." + domain)


def _private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower().rstrip(".") in {"localhost", "localhost.localdomain"} or host.lower().endswith(".local")
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _private_destination(host: str) -> bool:
    """Check literal and resolvable private destinations without proxy use."""

    if _private_ip(host):
        return True
    # A hostname can resolve to a private address even when its spelling is
    # public-looking.  Resolution is best-effort: an offline/test hostname
    # with no DNS answer remains eligible for an injected offline transport.
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror):
        return False
    return any(_private_ip(str(answer[4][0])) for answer in answers if answer and len(answer) > 4)


def validate_url(url: str, *, allow_private: bool = False, resolve_private: bool = False) -> str:
    """Validate a destination before any HTTP request is made.

    Hostname resolution is opt-in because planning must not perform network
    access.  Literal private addresses, localhost names, and ``.local``
    destinations are always rejected.  Execution enables best-effort
    resolution before opening the HTTP connection.  A caller that supplies an
    offline transport can opt into private test URLs explicitly.
    """

    if not isinstance(url, str) or not url.strip():
        raise RoutingError("URL must be a non-empty string")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise RoutingError(f"invalid URL: {exc}") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise RoutingError("only http(s) URLs are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise RoutingError("URL credentials are not allowed")
    if not parts.hostname:
        raise RoutingError("URL must contain a destination host")
    if not allow_private and (
        _private_destination(parts.hostname) if resolve_private else _private_ip(parts.hostname)
    ):
        raise RoutingError("loopback/private URL destinations are not allowed")
    try:
        query_keys = {key.lower() for key, _value in parse_qsl(parts.query, keep_blank_values=True)}
    except ValueError as exc:
        raise RoutingError("invalid URL query") from exc
    if query_keys & CREDENTIAL_QUERY_KEYS:
        raise RoutingError("credential-bearing URL query parameters are not allowed")
    return url


def _signed_query(parts: Any) -> bool:
    try:
        keys = {key.lower() for key, _value in parse_qsl(parts.query, keep_blank_values=True)}
    except ValueError:
        keys = set()
    return bool(keys & SIGNED_QUERY_KEYS)


def redact_url(url: str) -> str:
    """Return a safe URL for plans and receipts.

    A signed URL keeps its scheme/host/path for routing evidence while its
    entire query is replaced.  The executable value is kept only in memory.
    """

    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    if _signed_query(parts):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "[signed-query-redacted]", ""))
    return url


def _redact_text(value: Any) -> str:
    text = str(value)
    # Avoid allowing requests exception strings to reintroduce signed URLs.
    def replace(match: re.Match[str]) -> str:
        return redact_url(match.group(0).rstrip(".,);"))

    return re.sub(r"https?://[^\s'\"]+", replace, text)


def _service_for_host(host: str, config: Mapping[str, Any]) -> str:
    services = config["service_domains"]
    for service, domains in services.items():
        if any(_host_matches(host, domain) for domain in domains):
            return str(service)
    return "other:" + host.lower().rstrip(".")


def _is_github_host(host: str, config: Mapping[str, Any]) -> bool:
    return any(_host_matches(host, domain) for domain in config["github_domains"])


def route_url(
    url: str,
    config: Mapping[str, Any] | None = None,
    *,
    allow_private: bool = False,
    resolve_private: bool = False,
) -> dict[str, Any]:
    """Select transport settings from a URL's current destination host."""

    selected = _coerce_config(config)
    validate_url(url, allow_private=allow_private, resolve_private=resolve_private)
    host = urlsplit(url).hostname or ""
    github = _is_github_host(host, selected)
    return {
        "host": host.lower().rstrip("."),
        "service": "github" if github else _service_for_host(host, selected),
        "mode": "github-proxy" if github else "academic-direct",
        "network_mode": "explicit-proxy" if github else "direct",
        "proxy": selected["github_proxy"] if github else None,
    }


def _normalize_doi(doi: str) -> str:
    if not isinstance(doi, str) or not doi.strip():
        raise RoutingError("DOI must be a non-empty string")
    value = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    if any(char.isspace() for char in value) or value.startswith("/"):
        raise RoutingError("DOI contains whitespace or an invalid leading slash")
    return value


def _normalize_pmcid(pmcid: str) -> str:
    if not isinstance(pmcid, str) or not pmcid.strip():
        raise RoutingError("PMCID must be a non-empty string")
    value = pmcid.strip().upper()
    if not re.fullmatch(r"PMC[0-9]+", value):
        raise RoutingError("PMCID must look like PMC123456")
    return value


def _request_record(
    request_id: str,
    url: str,
    *,
    kind: str,
    expected_format: str,
    config: Mapping[str, Any],
    fallback: bool = False,
) -> dict[str, Any]:
    if kind not in SUPPORTED_KINDS:
        raise RoutingError(f"unsupported request kind: {kind!r}")
    if expected_format not in SUPPORTED_FORMATS:
        raise RoutingError(f"unsupported response format: {expected_format!r}")
    validate_url(url)
    route = route_url(url, config)
    record: dict[str, Any] = {
        "id": request_id,
        "kind": kind,
        "format": expected_format,
        "url": redact_url(url),
        "route": route["mode"],
        "network_mode": route["network_mode"],
        "service": route["service"],
        "host": route["host"],
        "proxy": route["proxy"],
        "fallback": bool(fallback),
        "status": "planned",
    }
    if redact_url(url) != url:
        record["url_redaction"] = "signed-query-hidden"
    return record


def build_plan(
    *,
    doi: str | None = None,
    pmcid: str | None = None,
    url: str | None = None,
    kind: str | None = None,
    expected_format: str | None = None,
    format: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a no-network acquisition plan.

    Exactly one of ``doi``, ``pmcid`` and ``url`` is required.  A DOI plan
    contains Crossref and Europe PMC metadata first, then the NCBI metadata
    and OpenAlex single-DOI fallback candidates described by D43.  The caller
    can bound execution with ``max_requests`` in :func:`execute_plan`.
    """

    supplied = [doi is not None, pmcid is not None, url is not None]
    if sum(supplied) != 1:
        raise RoutingError("provide exactly one of --doi, --pmcid, or --url")
    selected = _coerce_config(config)
    records: list[dict[str, Any]] = []
    subject: dict[str, Any]
    if doi is not None:
        normalized = _normalize_doi(doi)
        quoted = quote(normalized, safe="/")
        query = urlencode({"query": f'DOI:"{normalized}"', "format": "json", "resultType": "core", "pageSize": "1"})
        records.extend(
            [
                _request_record(
                    "crossref-metadata",
                    "https://api.crossref.org/works/" + quoted,
                    kind="metadata",
                    expected_format="json",
                    config=selected,
                ),
                _request_record(
                    "europepmc-metadata",
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + query,
                    kind="metadata",
                    expected_format="json",
                    config=selected,
                ),
                _request_record(
                    "ncbi-metadata",
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
                    + urlencode({"db": "pubmed", "term": normalized + "[AID]", "retmode": "json", "retmax": "1"}),
                    kind="metadata",
                    expected_format="json",
                    config=selected,
                    fallback=False,
                ),
                _request_record(
                    "openalex-doi-fallback",
                    "https://api.openalex.org/works/https://doi.org/" + quoted,
                    kind="metadata",
                    expected_format="json",
                    config=selected,
                    fallback=True,
                ),
            ]
        )
        subject = {"type": "doi", "doi": normalized}
    elif pmcid is not None:
        normalized = _normalize_pmcid(pmcid)
        records.append(
            _request_record(
                "europepmc-fulltext-" + normalized.lower(),
                f"https://www.ebi.ac.uk/europepmc/webservices/rest/{normalized}/fullTextXML",
                kind="fulltext",
                expected_format="xml",
                config=selected,
            )
        )
        subject = {"type": "pmcid", "pmcid": normalized}
    else:
        if kind is None or expected_format is None and format is None:
            raise RoutingError("--url requires --kind and --format")
        selected_format = expected_format or format
        if selected_format is None:
            raise RoutingError("--url requires --format")
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]  # type: ignore[union-attr]
        records.append(
            _request_record(
                "url-" + digest,
                url,  # type: ignore[arg-type]
                kind=kind,
                expected_format=selected_format,
                config=selected,
            )
        )
        subject = {"type": "url", "url": redact_url(url)}
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "mode": "plan",
        "subject": subject,
        "request_budget": int(selected["request_budget"]),
        "max_file_bytes": int(selected["max_file_bytes"]),
        "timeouts": {
            "connect_seconds": selected["connect_timeout_seconds"],
            "read_seconds": selected["read_timeout_seconds"],
            "stream_wall_seconds": selected["stream_wall_timeout_seconds"],
        },
        "max_redirects": int(selected["max_redirects"]),
        "firecrawl_calls": 0,
        "api_keys_used": False,
        "emails_used": False,
        "requests": records,
    }


def _safe_headers(headers: Any) -> dict[str, str]:
    wanted = {
        "content-type",
        "content-length",
        "content-disposition",
        "location",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-rate-limit-limit",
        "x-rate-limit-remaining",
        "x-rate-limit-interval",
        "x-concurrency-limit",
        "x-api-pool",
    }
    output: dict[str, str] = {}
    if headers is None:
        return output
    try:
        items = headers.items()
    except AttributeError:
        return output
    for key, value in items:
        lower = str(key).lower()
        if lower not in wanted:
            continue
        if lower == "location":
            output[str(key)] = redact_url(str(value))
        else:
            output[str(key)] = _redact_text(value)
    return output


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
        if value is not None:
            return str(value)
    except AttributeError:
        pass
    try:
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value)
    except AttributeError:
        return None
    return None


def _quota_exhausted(headers: Any) -> bool:
    try:
        items = headers.items()
    except AttributeError:
        return False
    for key, value in items:
        if str(key).lower() in QUOTA_HEADERS and str(value).strip() == "0":
            return True
    return False


def _make_session(route: Mapping[str, Any], config: Mapping[str, Any]) -> Any:
    if requests is None:
        raise DependencyMissing("execute requires the existing 'requests' package; nothing was installed")
    session = requests.Session()
    # Explicitly disable environment proxy inheritance for every route.
    session.trust_env = False
    session.proxies = {}
    adapter = requests.adapters.HTTPAdapter(max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if route["mode"] == "github-proxy":
        session.proxies.update({"http": config["github_proxy"], "https": config["github_proxy"]})
    session.headers.update({"User-Agent": config.get("user_agent", "BioInformaticsHarness-source-routing/1.0")})
    return session


def session_for_route(route: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> Any:
    """Public helper returning a fresh, no-retry session for a route."""

    return _make_session(route, _coerce_config(config))


def _safe_error(exc: BaseException) -> str:
    return _redact_text(f"{type(exc).__name__}: {exc}")[:500]


def _target_path(out_dir: Path, request: Mapping[str, Any]) -> Path:
    request_id = str(request.get("id", "request"))
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", request_id).strip("._") or "request"
    suffix = FORMAT_SUFFIXES.get(str(request.get("format")), ".bin")
    return out_dir / "raw" / (safe_id + suffix)


def _safe_target_path(out_dir: Path, request: Mapping[str, Any]) -> Path:
    """Return a target below ``out_dir`` without following an output symlink."""

    target = _target_path(out_dir, request)
    root = out_dir.resolve()
    parent = target.parent
    if parent.exists() and parent.is_symlink():
        raise RoutingError("refusing to write through a symlinked output directory")
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RoutingError("output target escapes the requested output directory") from exc
    return target


def _invalid_target_path(out_dir: Path, request: Mapping[str, Any]) -> Path:
    target = _target_path(out_dir, request)
    return target.with_name(target.name + ".invalid")


def _read_existing_receipts(out_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(out_dir.glob("receipts*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            records.extend(item for item in payload["results"] if isinstance(item, dict))
    return records


def _cache_match(request: Mapping[str, Any], path: Path, records: Iterable[Mapping[str, Any]]) -> bool:
    requested_url = str(request.get("url", ""))
    for record in records:
        if record.get("request_id") != request.get("id"):
            continue
        if record.get("url") != requested_url:
            continue
        if record.get("outcome") in {"downloaded", "cache_hit"} and path.exists() and path.is_file():
            return True
    return False


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite existing file: {path}")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _format_check(data: bytes, expected_format: str) -> tuple[bool, str]:
    if expected_format == "json":
        try:
            json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return False, f"json_parse_failed: {_safe_error(exc)}"
        return True, "json_parsed"
    if expected_format == "xml":
        try:
            ET.fromstring(data)
        except (ET.ParseError, ValueError) as exc:
            return False, f"xml_parse_failed: {_safe_error(exc)}"
        return True, "xml_parsed"
    if expected_format == "pdf":
        return (data.startswith(b"%PDF-"), "pdf_magic_header" if data.startswith(b"%PDF-") else "not_pdf_magic_header")
    if expected_format in {"html", "text"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            return False, f"utf8_decode_failed: {_safe_error(exc)}"
        return True, "utf8_decoded"
    return False, "unsupported_format"


def _consume_response(response: Any, cap: int, wall_seconds: float, started: float) -> dict[str, Any]:
    headers = getattr(response, "headers", {})
    declared = _header(headers, "Content-Length")
    if declared and declared.isdigit() and int(declared) > cap:
        return {"outcome": "stopped_declared_size_over_cap", "bytes": 0, "body": b""}
    chunks: list[bytes] = []
    total = 0
    try:
        iterator = response.iter_content(chunk_size=65536)
    except AttributeError:
        content = getattr(response, "content", b"")
        iterator = (content,)
    for chunk in iterator:
        if time.monotonic() - started > wall_seconds:
            return {"outcome": "stopped_stream_wall_timeout", "bytes": total, "body": b""}
        if not chunk:
            continue
        if not isinstance(chunk, (bytes, bytearray)):
            chunk = str(chunk).encode("utf-8", "replace")
        piece = bytes(chunk)
        if total + len(piece) > cap:
            return {"outcome": "stopped_stream_size_over_cap", "bytes": total, "body": b""}
        chunks.append(piece)
        total += len(piece)
    body = b"".join(chunks)
    return {"outcome": "body_read", "bytes": len(body), "body": body}


def _close_session(session: Any) -> None:
    try:
        session.close()
    except (AttributeError, Exception):  # noqa: BLE001 - a close failure cannot mask the receipt
        return


class _ExecutionState:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self.stopped_services: set[str] = set()
        self.last_request_at: float | None = None

    def reserve(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    def wait_for_request(self, interval: float) -> None:
        if self.last_request_at is not None:
            remaining = interval - (time.monotonic() - self.last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request_at = time.monotonic()


def _fetch_request(
    request: Mapping[str, Any],
    *,
    out_dir: Path,
    config: Mapping[str, Any],
    state: _ExecutionState,
    session_factory: Callable[[Mapping[str, Any]], Any],
    allow_private: bool,
) -> dict[str, Any]:
    displayed_url = str(request.get("url", ""))
    result: dict[str, Any] = {
        "request_id": request.get("id"),
        "url": redact_url(displayed_url),
        "kind": request.get("kind"),
        "format": request.get("format"),
        "automatic_retries": 0,
        "redirects_followed": 0,
        "route_history": [],
        "outcome": "pending",
    }
    if "[signed-query-redacted]" in displayed_url:
        result.update({"outcome": "signed_query_not_replayable", "reason": "signed URL query is intentionally absent from the saved plan"})
        return result
    current_url = displayed_url
    max_redirects = int(config["max_redirects"])
    redirect_count = 0
    while True:
        try:
            validate_url(current_url, allow_private=allow_private, resolve_private=True)
            route = route_url(current_url, config, allow_private=allow_private, resolve_private=True)
        except RoutingError as exc:
            result.update({"outcome": "unsafe_url", "reason": _safe_error(exc), "final_url": redact_url(current_url)})
            return result
        service = str(route["service"])
        result["final_url"] = redact_url(current_url)
        if service in state.stopped_services:
            result.update({"outcome": "service_stopped", "service": service})
            return result
        if not state.reserve():
            result.update({"outcome": "budget_exhausted", "budget_used": state.used, "budget": state.limit})
            return result
        host = str(route["host"])
        arxiv = any(_host_matches(host, domain) for domain in config.get("arxiv_domains", []))
        interval = float(config.get("arxiv_interval_seconds" if arxiv else "request_interval_seconds", 0))
        state.wait_for_request(max(0.0, interval))
        result["route_history"].append(
            {
                "host": route["host"],
                "service": service,
                "mode": route["mode"],
                "network_mode": route["network_mode"],
                "proxy": route["proxy"],
            }
        )
        session: Any = None
        started = time.monotonic()
        try:
            session = session_factory(route)
            response = session.get(
                current_url,
                timeout=(config["connect_timeout_seconds"], config["read_timeout_seconds"]),
                allow_redirects=False,
                stream=True,
            )
            status = int(getattr(response, "status_code", 0))
            headers = getattr(response, "headers", {})
            result["status"] = status
            result["response_headers"] = _safe_headers(headers)
            if status in {403, 429}:
                state.stopped_services.add(service)
                result["service_stop_reason"] = f"http_{status}"
            elif _quota_exhausted(headers):
                state.stopped_services.add(service)
                result["service_stop_reason"] = "quota_remaining_zero"
            if status in REDIRECT_STATUSES:
                location = _header(headers, "Location")
                if not location:
                    result.update({"outcome": "redirect_without_location"})
                    return result
                if redirect_count >= max_redirects:
                    result.update({"outcome": "redirect_limit_exceeded"})
                    return result
                try:
                    next_url = urljoin(current_url, location)
                    validate_url(next_url, allow_private=allow_private, resolve_private=True)
                except RoutingError as exc:
                    result.update({"outcome": "redirect_rejected", "reason": _safe_error(exc)})
                    return result
                redirect_count += 1
                result["redirects_followed"] = redirect_count
                current_url = next_url
                if float(config.get("redirect_delay_seconds", 0)) > 0:
                    # Tests can set the delay to zero; production defaults are
                    # deliberately conservative without introducing retries.
                    time.sleep(float(config["redirect_delay_seconds"]))
                continue
            body_result = _consume_response(
                response,
                int(config["max_file_bytes"]),
                float(config["stream_wall_timeout_seconds"]),
                started,
            )
            result.update({k: v for k, v in body_result.items() if k != "body"})
            if body_result["outcome"] != "body_read":
                result["outcome"] = body_result["outcome"]
                return result
            body = body_result["body"]
            result["sha256"] = hashlib.sha256(body).hexdigest()
            if status != 200:
                result["outcome"] = f"http_{status}"
                return result
            valid, format_check = _format_check(body, str(request["format"]))
            result["format_check"] = format_check
            if not valid:
                result["outcome"] = "format_invalid"
                result["reason"] = "response was retained only in memory because it did not match the requested format"
                return result
            destination = _safe_target_path(out_dir, request)
            if destination.exists() or destination.is_symlink():
                result.update({"outcome": "existing_file_preserved", "path": destination.relative_to(out_dir).as_posix()})
                return result
            try:
                _atomic_write_bytes(destination, body)
            except FileExistsError:
                result.update({"outcome": "existing_file_preserved", "path": destination.relative_to(out_dir).as_posix()})
                return result
            result.update({"outcome": "downloaded", "path": destination.relative_to(out_dir).as_posix()})
            return result
        except DependencyMissing as exc:
            result.update({"outcome": "dependency_missing", "reason": _safe_error(exc)})
            return result
        except Exception as exc:  # transport errors are receipts, not implicit retries
            result.update({"outcome": "request_exception", "reason": _safe_error(exc)})
            return result
        finally:
            _close_session(session)


def _new_receipt_path(out_dir: Path) -> Path:
    preferred = out_dir / "receipts.json"
    if not preferred.exists():
        return preferred
    stem = "receipts-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = out_dir / (stem + ".json")
    while candidate.exists():
        candidate = out_dir / (stem + "-" + uuid4().hex[:8] + ".json")
    return candidate


def _sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"url", "final_url", "requested_url", "location"} and isinstance(item, str):
                output[key] = redact_url(item)
            else:
                output[key] = _sanitize_for_output(item)
        return output
    if isinstance(value, list):
        return [_sanitize_for_output(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _write_json_no_overwrite(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(_sanitize_for_output(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite existing file: {path}")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def write_plan(plan: Mapping[str, Any], path: str | os.PathLike[str]) -> Path:
    """Persist a plan once, refusing to overwrite an earlier plan."""

    return _write_json_no_overwrite(Path(path), plan)


def execute_plan(
    plan: Mapping[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    max_requests: int | None = None,
    max_bytes: int | None = None,
    session_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    config: Mapping[str, Any] | None = None,
    allow_private: bool = False,
) -> dict[str, Any]:
    """Execute a previously built plan with a hard request and file budget."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise RoutingError("unsupported plan schema")
    selected = _coerce_config(config)
    if max_bytes is not None:
        if not isinstance(max_bytes, int) or max_bytes <= 0 or max_bytes > MAX_FILE_BYTES:
            raise RoutingError(f"max_bytes must be between 1 and {MAX_FILE_BYTES}")
        selected["max_file_bytes"] = max_bytes
    else:
        # A plan may have been created with an explicit --max-bytes override;
        # carry that bound into execution even when the caller does not repeat
        # the CLI option.
        if isinstance(plan.get("max_file_bytes"), int) and plan["max_file_bytes"] > 0:
            selected["max_file_bytes"] = int(plan["max_file_bytes"])
    configured_limit = min(
        int(plan.get("request_budget", selected["request_budget"])),
        int(selected["request_budget"]),
        MAX_REQUEST_BUDGET,
    )
    limit = configured_limit if max_requests is None else min(max_requests, configured_limit)
    if not isinstance(limit, int) or limit < 0:
        raise RoutingError("max_requests must be a non-negative integer")
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    factory = session_factory or (lambda route: _make_session(route, selected))
    state = _ExecutionState(limit)
    old_receipts = _read_existing_receipts(output_dir)
    results: list[dict[str, Any]] = []
    for request in plan.get("requests", []):
        if not isinstance(request, Mapping):
            continue
        try:
            destination = _safe_target_path(output_dir, request)
        except RoutingError as exc:
            results.append(
                {
                    "request_id": request.get("id"),
                    "url": redact_url(str(request.get("url", ""))),
                    "kind": request.get("kind"),
                    "format": request.get("format"),
                    "outcome": "unsafe_output_path",
                    "reason": _safe_error(exc),
                    "automatic_retries": 0,
                    "http_requests": 0,
                }
            )
            continue
        if _cache_match(request, destination, old_receipts):
            results.append(
                {
                    "request_id": request.get("id"),
                    "url": redact_url(str(request.get("url", ""))),
                    "kind": request.get("kind"),
                    "format": request.get("format"),
                    "outcome": "cache_hit",
                    "path": destination.relative_to(output_dir).as_posix(),
                    "automatic_retries": 0,
                    "http_requests": 0,
                }
            )
            continue
        if destination.exists() or destination.is_symlink():
            results.append(
                {
                    "request_id": request.get("id"),
                    "url": redact_url(str(request.get("url", ""))),
                    "kind": request.get("kind"),
                    "format": request.get("format"),
                    "outcome": "existing_file_preserved",
                    "path": destination.relative_to(output_dir).as_posix(),
                    "automatic_retries": 0,
                    "http_requests": 0,
                }
            )
            continue
        if state.used >= state.limit:
            results.append(
                {
                    "request_id": request.get("id"),
                    "url": redact_url(str(request.get("url", ""))),
                    "kind": request.get("kind"),
                    "format": request.get("format"),
                    "outcome": "budget_exhausted",
                    "budget_used": state.used,
                    "budget": state.limit,
                    "automatic_retries": 0,
                    "http_requests": 0,
                }
            )
            continue
        before = state.used
        result = _fetch_request(
            request,
            out_dir=output_dir,
            config=selected,
            state=state,
            session_factory=factory,
            allow_private=allow_private,
        )
        result["http_requests"] = state.used - before
        results.append(result)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "mode": "execute",
        "subject": copy.deepcopy(plan.get("subject")),
        "request_budget": limit,
        "requests_used": state.used,
        "max_file_bytes": selected["max_file_bytes"],
        "firecrawl_calls": 0,
        "api_keys_used": False,
        "emails_used": False,
        "stopped_services": sorted(state.stopped_services),
        "results": results,
    }
    receipt_path = _new_receipt_path(output_dir)
    receipt["receipt_path"] = receipt_path.relative_to(output_dir).as_posix()
    _write_json_no_overwrite(receipt_path, receipt)
    return receipt


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doi")
    group.add_argument("--pmcid")
    group.add_argument("--url")
    parser.add_argument("--kind", choices=sorted(SUPPORTED_KINDS), help="required with --url")
    parser.add_argument("--format", dest="expected_format", choices=sorted(SUPPORTED_FORMATS), help="required with --url")
    parser.add_argument("--execute", action="store_true", help="perform bounded HTTP requests; default is plan-only")
    parser.add_argument("--out-dir", help="directory for plan, receipt, and downloaded raw files")
    parser.add_argument("--out", help="plan output path; without --execute this is the only write")
    parser.add_argument("--config", help="routing configuration JSON")
    parser.add_argument("--max-bytes", type=int, help="per-file cap, overriding config")
    parser.add_argument("--max-requests", "--request-budget", dest="max_requests", type=int, help="hard HTTP request cap")
    parser.add_argument("--allow-private-test-destination", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        plan = build_plan(
            doi=args.doi,
            pmcid=args.pmcid,
            url=args.url,
            kind=args.kind,
            expected_format=args.expected_format,
            config=config,
        )
        if args.max_bytes is not None:
            if args.max_bytes <= 0:
                raise RoutingError("--max-bytes must be positive")
            plan["max_file_bytes"] = args.max_bytes
        output_dir: Path | None = Path(args.out_dir) if args.out_dir else None
        if args.execute and output_dir is None:
            output_dir = ROOT / "routing" / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        plan_path: Path | None = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            plan_path = Path(args.out) if args.out else output_dir / "plan.json"
            write_plan(plan, plan_path)
        elif args.out:
            plan_path = write_plan(plan, args.out)
        if not args.execute:
            payload = dict(plan)
            if plan_path is not None:
                payload["plan_path"] = str(plan_path)
            print(json.dumps(_sanitize_for_output(payload), ensure_ascii=False, indent=2))
            return 0
        assert output_dir is not None
        # Production execution uses the configured total budget.  The public
        # two-request smoke in this round passes --max-requests 2 explicitly.
        cap = args.max_requests
        receipt = execute_plan(
            plan,
            output_dir,
            max_requests=cap,
            max_bytes=args.max_bytes,
            config=config,
            allow_private=args.allow_private_test_destination,
        )
        if plan_path is not None:
            receipt["plan_path"] = str(plan_path)
        print(json.dumps(_sanitize_for_output(receipt), ensure_ascii=False, indent=2))
        return 0
    except (RoutingError, FileExistsError, OSError, DependencyMissing) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": _safe_error(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
