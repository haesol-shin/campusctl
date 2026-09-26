from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from campusctl.providers.cnu.ui_policy import (
    UiRequestDenied,
    UiRequestDiagnostics,
    UiRequestPolicy,
    bind_selected_file_request,
    guard_ui_request,
    install_ui_request_interceptor,
    match_selected_file_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lms_sources"

_URL_REGEX = re.compile(r'(?i)\bhttps?://[^\s"\'<>]+')
_FORBIDDEN_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "session_id",
    "sessionid",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)
_SECRET_VALUE_REGEX = re.compile(
    r"(?i)(\bbearer\s+[a-z0-9._~+/=-]{8,}|\bey[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}"
    r"|-----begin [a-z ]*private key-----|\b(jsessionid|sessionid|password|passwd|api_key|apikey)\s*[=:])"
)
_FORBIDDEN_PARAM_TOKENS = ("signature", "token", "key", "sig", "expires", "secret", "auth", "session_id", "sessionid")
_FORBIDDEN_VIDEO_EXTS = (".mp4", ".m3u8", ".mpd", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv")
# Display text may name a refused video control; file-bearing fields and URLs may not point at video.
_FILE_BEARING_KEYS = frozenset(
    {"url", "href", "path", "filename", "original_name", "candidate_url", "saved_path", "content_path", "name"}
)
_FORBIDDEN_MEDIA_PREFIXES = ("video/", "audio/")


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _reviewed_policy(headers_fixture: dict[str, Any]) -> UiRequestPolicy:
    route = headers_fixture["route"]
    reviewed = {
        "approved": True,
        "read_only_evidence": "synthetic-test-evidence",
        "origins": [route["origin"]],
        "routes": [route],
        "allowed_media": [],
        "max_bytes": None,
    }
    return UiRequestPolicy.from_reviewed_config(reviewed)


def test_unapproved_fixture_denies_every_request() -> None:
    raw_policy = _fixture("policy.json")
    assert raw_policy["approved"] is False
    assert UiRequestPolicy.validate_reviewed_config(raw_policy)
    assert not UiRequestPolicy.validate_reviewed_config({"approved": False})
    malformed = dict(raw_policy, static_resource_types=["video"])
    assert not UiRequestPolicy.validate_reviewed_config(malformed)
    policy = UiRequestPolicy.from_reviewed_config(raw_policy)
    assert not policy.approved
    assert not policy.origins
    assert not policy.routes

    request = _fixture("request_headers.json")

    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            request["request"]["url"],
            request["request"]["method"],
            {},
            operation=request["route"]["operation"],
            resource_type=request["request"]["resource_type"],
        )
    assert caught.value.code == "policy-blocked"


def assert_fixture_data_hygiene(data: Any, origin_label: str = "fixture") -> None:
    def _scan(node: Any, path: str = "") -> None:
        if isinstance(node, Mapping):
            media_val = str(node.get("media_type") or node.get("mime") or "").strip().lower()
            path_val = str(node.get("path") or node.get("original_name") or "").strip().lower()
            if path_val.endswith(".ts") and media_val.startswith(_FORBIDDEN_MEDIA_PREFIXES):
                raise AssertionError(
                    f"Transport stream '.ts' with video/audio MIME '{media_val}' in {origin_label} at {path}"
                )

            for key, val in node.items():
                key_lower = str(key).lower()
                for token in _FORBIDDEN_KEY_TOKENS:
                    assert token not in key_lower, f"Forbidden key token '{token}' in {origin_label} at {path}.{key}"

                if key_lower in _FILE_BEARING_KEYS and isinstance(val, str):
                    val_path = urlsplit(val).path.lower() if _URL_REGEX.match(val) else val.strip().lower()
                    for ext in _FORBIDDEN_VIDEO_EXTS:
                        assert not val_path.endswith(ext), (
                            f"Forbidden video extension '{ext}' in {origin_label} at {path}.{key}: '{val}'"
                        )
                if key_lower in ("media_type", "mime") and isinstance(val, str):
                    val_lower = val.strip().lower()
                    assert not val_lower.startswith(_FORBIDDEN_MEDIA_PREFIXES), (
                        f"Forbidden media type/mime '{val}' in {origin_label} at {path}.{key}"
                    )
                _scan(val, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _scan(item, f"{path}[{index}]")
        elif isinstance(node, str):
            for match in _URL_REGEX.finditer(node):
                url = match.group(0)
                parsed = urlsplit(url)
                url_path = parsed.path.lower()
                for ext in _FORBIDDEN_VIDEO_EXTS:
                    assert not url_path.endswith(ext), (
                        f"Forbidden video extension '{ext}' in {origin_label} at {path}: '{url}'"
                    )
                host = parsed.hostname
                assert host and (host == "invalid" or host.endswith(".invalid")), (
                    f"Real LMS host '{host}' instead of reserved .invalid in {origin_label} at {path}: '{url}'"
                )

                for param_name, _ in parse_qsl(parsed.query, keep_blank_values=True):
                    param_lower = param_name.lower()
                    for token in _FORBIDDEN_PARAM_TOKENS:
                        assert token not in param_lower, (
                            f"Forbidden query parameter '{param_name}' in URL in {origin_label} at {path}: '{url}'"
                        )

                for param_name, _ in parse_qsl(parsed.fragment, keep_blank_values=True):
                    param_lower = param_name.lower()
                    for token in _FORBIDDEN_PARAM_TOKENS:
                        assert token not in param_lower, (
                            f"Forbidden fragment parameter '{param_name}' in URL in {origin_label} at {path}: '{url}'"
                        )

            assert not _SECRET_VALUE_REGEX.search(node), f"Credential-like value in {origin_label} at {path}"

    _scan(data)


def test_fixture_hygiene_scans_lms_sources() -> None:
    fixture_files = list(FIXTURES.glob("*.json"))
    assert fixture_files, "No fixture files found under tests/fixtures/lms_sources"

    for fixture_file in fixture_files:
        data = json.loads(fixture_file.read_text(encoding="utf-8"))
        assert_fixture_data_hygiene(data, str(fixture_file))


def test_fixture_hygiene_scanner_rejects_malformed_inputs() -> None:
    with pytest.raises(AssertionError, match="Real LMS host 'lms.cnu.ac.kr'"):
        assert_fixture_data_hygiene({"doc": "See [link](https://lms.cnu.ac.kr/std/task) here"})

    with pytest.raises(AssertionError, match="Real LMS host 'real-lms.example.com'"):
        assert_fixture_data_hygiene({"doc": "See [link](HTTPS://REAL-LMS.EXAMPLE.COM/file) here"})

    for key in ("api_key", "ApiKey", "client_secret", "user_password", "Credential"):
        with pytest.raises(AssertionError, match="Forbidden key token"):
            assert_fixture_data_hygiene({key: "synthetic"})

    for value in (
        "Authorization: Bearer abcdefghijklmnop",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
        "JSESSIONID=ABCDEF0123456789",
        "-----BEGIN RSA PRIVATE KEY-----",
    ):
        with pytest.raises(AssertionError, match="Credential-like value"):
            assert_fixture_data_hygiene({"note": value})

    for bad_param in ("signature=xyz123", "token=secret", "sig=abc", "expires=1800000000", "api_key=secret"):
        with pytest.raises(AssertionError, match="Forbidden query parameter"):
            assert_fixture_data_hygiene({"url": f"https://lms.example.invalid/task?{bad_param}"})

    with pytest.raises(AssertionError, match="Forbidden fragment parameter"):
        assert_fixture_data_hygiene({"url": "https://lms.example.invalid/task#access_token=secret"})

    with pytest.raises(AssertionError, match="Forbidden media type/mime 'video/mp4'"):
        assert_fixture_data_hygiene({"mime": "video/mp4"})

    with pytest.raises(AssertionError, match="Forbidden media type/mime 'AUDIO/MPEG'"):
        assert_fixture_data_hygiene({"MIME": "AUDIO/MPEG"})

    with pytest.raises(AssertionError, match="Forbidden media type/mime 'Video/webm'"):
        assert_fixture_data_hygiene({"media_type": "Video/webm"})

    with pytest.raises(AssertionError, match=r"Forbidden video extension '\.mp4'"):
        assert_fixture_data_hygiene({"filename": "movie.mp4"})

    with pytest.raises(AssertionError, match=r"Forbidden video extension '\.m3u8'"):
        assert_fixture_data_hygiene({"filename": "stream.m3u8"})

    with pytest.raises(AssertionError, match=r"Transport stream '\.ts' with video/audio MIME 'video/mp2t'"):
        assert_fixture_data_hygiene({"path": "clip.ts", "media_type": "video/mp2t"})

    with pytest.raises(AssertionError, match=r"Transport stream '\.ts' with video/audio MIME 'video/mp2t'"):
        assert_fixture_data_hygiene({"path": "clip.ts", "mime": "video/mp2t"})

    assert_fixture_data_hygiene({"path": "lecture-notes.ts", "media_type": "text/plain"})
    assert_fixture_data_hygiene({"code": "import { foo } from './bar.ts';"})


def test_range_header_blocks_otherwise_allowed_get() -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)

    for case in fixture["cases"]:
        if case["expected_decision"] == "allow":
            assert (
                guard_ui_request(
                    policy,
                    fixture["request"]["url"],
                    fixture["request"]["method"],
                    case["headers"],
                    operation=fixture["route"]["operation"],
                    resource_type=fixture["request"]["resource_type"],
                )
                == "allow"
            )
        else:
            with pytest.raises(UiRequestDenied) as caught:
                guard_ui_request(
                    policy,
                    fixture["request"]["url"],
                    fixture["request"]["method"],
                    case["headers"],
                    operation=fixture["route"]["operation"],
                    resource_type=fixture["request"]["resource_type"],
                )
            assert caught.value.reason_code == "range"


def test_unapproved_origin_route_and_method_are_denied() -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)
    request = fixture["request"]
    operation = fixture["route"]["operation"]
    cases = [
        ("https://outside.example.invalid/std/task", "GET", "document", "origin"),
        ("https://lms.example.invalid/std/archive", "GET", "document", "route"),
        (request["url"], "POST", "document", "method"),
    ]

    for url, method, resource_type, reason_code in cases:
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(
                policy,
                url,
                method,
                {},
                operation=operation,
                resource_type=resource_type,
            )
        assert caught.value.reason_code == reason_code
        assert caught.value.code == "policy-blocked"


@pytest.mark.parametrize(
    "url",
    [
        "https://lms.example.invalid/api/v1/panopto/addInternetDisconnectionLog",
        "https://lms.example.invalid/api/v1/panopto/checkInternetConnection",
        "https://lms.example.invalid/api/v1/addAttendnLog",
        "https://lms.example.invalid/api/v1/reCalculateAttend",
        "https://lms.example.invalid/api/v1/activity/record",
        "https://lms.example.invalid/api/v1/attendance/list",
        "https://lms.example.invalid/api/v1/analytics/collect",
        "https://lms.example.invalid/api/v1/beacon",
        "https://lms.example.invalid/api/v1/telemetry",
    ],
)
def test_logging_endpoints_are_denied(url: str) -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)

    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            url,
            "GET",
            {},
            operation=fixture["route"]["operation"],
            resource_type="xhr",
        )

    assert caught.value.reason_code == "logging"


@pytest.mark.parametrize(
    ("path", "expected_reason"),
    [
        ("/std/course", "route"),
        ("/std/task", None),
        ("/std/notice", "route"),
        ("/std/archive", "route"),
        ("/std/lecture", "route"),
    ],
)
def test_normal_routes_are_not_classified_as_logging(path: str, expected_reason: str | None) -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)
    request = {
        "url": f"https://lms.example.invalid{path}",
        "method": "GET",
        "operation": fixture["route"]["operation"],
        "resource_type": "document",
    }

    if expected_reason is None:
        assert (
            guard_ui_request(
                policy,
                request["url"],
                request["method"],
                {},
                operation=request["operation"],
                resource_type=request["resource_type"],
            )
            == "allow"
        )
        return

    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            request["url"],
            request["method"],
            {},
            operation=request["operation"],
            resource_type=request["resource_type"],
        )
    assert caught.value.reason_code == expected_reason


def test_media_requests_are_denied() -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)

    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            fixture["request"]["url"],
            "GET",
            {},
            operation=fixture["route"]["operation"],
            resource_type="media",
        )

    assert caught.value.reason_code == "media"


def test_redirected_request_is_denied_even_when_target_is_approved() -> None:
    fixture = _fixture("request_headers.json")
    policy = _reviewed_policy(fixture)

    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            fixture["request"]["url"],
            fixture["request"]["method"],
            {},
            operation=fixture["route"]["operation"],
            resource_type=fixture["request"]["resource_type"],
            redirected_from="https://lms.example.invalid/std/course",
        )

    assert caught.value.reason_code == "redirect"


def _extended_config() -> dict[str, Any]:
    learning = "https://dcs-learning.cnu.ac.kr"
    lcms = "https://dcs-lcms.cnu.ac.kr"
    return {
        "approved": True,
        "read_only_evidence": "synthetic",
        "allowed_media": [],
        "max_bytes": None,
        "origins": [learning, lcms],
        "routes": [
            {"origin": learning, "path": "/std/myLecture", "operation": "materials.download", "methods": ["GET"]},
            {
                "origin": learning,
                "path": "/properties/messages.properties",
                "operation": "materials.download",
                "methods": ["GET"],
                "query": {"_": "cachebuster"},
            },
        ],
        "static_asset_origins": [learning],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
        "suppress": [
            {
                "name": "panopto-script",
                "origin": learning,
                "path_template": "/js/common/panopto-{hash}.js",
                "operation": "materials.download",
                "methods": ["GET"],
                "reason": "media-integration",
            },
            {
                "name": "panopto-disconnection-log",
                "origin": learning,
                "path_template": "/api/v1/panopto/addInternetDisconnectionLog",
                "operation": "materials.download",
                "methods": ["POST"],
                "reason": "logging",
            },
            {
                "name": "panopto-connectivity-check",
                "origin": learning,
                "path_template": "/api/v1/panopto/checkInternetConnection",
                "operation": "materials.download",
                "methods": ["GET"],
                "reason": "logging",
            },
        ],
        "selected_file_routes": [
            {
                "origin": lcms,
                "path_template": "/upload/{storage-id}/{encoded-filename}",
                "operation": "materials.download",
                "methods": ["GET"],
            },
            {
                "origin": learning,
                "path_template": "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}",
                "operation": "materials.download",
                "methods": ["GET"],
            },
        ],
    }


def _guard(policy: UiRequestPolicy, url: str, method: str = "GET", resource_type: str = "xhr", **kwargs: Any) -> str:
    return guard_ui_request(
        policy,
        url,
        method,
        kwargs.pop("headers", {}),
        operation="materials.download",
        resource_type=resource_type,
        **kwargs,
    )


def test_reviewed_suppression_and_static_matrix() -> None:
    config = _extended_config()
    policy = UiRequestPolicy.from_reviewed_config(config)
    assert policy.approved
    origin = config["origins"][0]
    for path, method in (
        ("/js/common/panopto-Ab_90.js", "GET"),
        ("/api/v1/panopto/addInternetDisconnectionLog", "POST"),
        ("/api/v1/panopto/checkInternetConnection", "GET"),
    ):
        assert _guard(policy, origin + path, method) == "suppress"
    for url, method, kind in (
        (origin + "/js/common/panoptoSaml-abc.js", "GET", "script"),
        (origin + "/js/common/panopto-abc.js/extra", "GET", "script"),
        (origin + "/api/v1/panopto/addInternetDisconnectionLog", "GET", "xhr"),
        (origin + "/js/ordinary.js", "GET", "xhr"),
        (config["origins"][1] + "/js/ordinary.js", "GET", "script"),
        (origin + "/video/ordinary.mp4", "GET", "script"),
        (origin + "/js/ordinary.js", "POST", "script"),
    ):
        with pytest.raises(UiRequestDenied):
            _guard(policy, url, method, kind)
    for kind in ("script", "stylesheet", "font", "image"):
        assert _guard(policy, origin + "/assets/ordinary.css?v=3", resource_type=kind) == "allow"
    assert _guard(policy, origin + "/assets/ordinary.js?v=3", resource_type="script") == "allow"
    for path, kind in (
        ("/api/v1/course/addSessionCourseInfo", "script"),
        ("/api/v1/course/addSessionCourseInfo", "image"),
        ("/std/task", "stylesheet"),
        ("/api/v1/synthetic.js", "script"),
        ("/std/synthetic.css", "stylesheet"),
        ("/assets/not-an-asset", "script"),
    ):
        with pytest.raises(UiRequestDenied) as caught:
            _guard(policy, origin + path, resource_type=kind)
        assert caught.value.reason_code == "route"
    assert _guard(policy, origin + "/std/myLecture", resource_type="stylesheet") == "allow"
    assert _guard(policy, origin + "/properties/messages.properties?_=123") == "allow"
    for query in ("?", "#", "?_=123&_=456", "?_=abc", "?other=123", "?_=123#fragment", "?_=%31"):
        with pytest.raises(UiRequestDenied):
            _guard(policy, origin + "/properties/messages.properties" + query)
    with pytest.raises(UiRequestDenied) as caught:
        _guard(policy, origin + "/js/common/panopto-Ab_90.js", headers={"rAnGe": "bytes=0-1"})
    assert caught.value.reason_code == "range"
    with pytest.raises(UiRequestDenied) as caught:
        _guard(policy, origin + "/js/common/panopto-Ab_90.js", redirected_from=origin + "/std/myLecture")
    assert caught.value.reason_code == "redirect"


def test_malformed_review_disables_every_pin() -> None:
    import copy

    base = _extended_config()
    for mutate in (
        lambda c: c["suppress"].append(c["suppress"][0].copy()),
        lambda c: c["suppress"][0].update(path_template="/js/common/*"),
        lambda c: c["suppress"][0].update(reason="safe"),
        lambda c: c["suppress"][0].update(origin=c["origins"][1]),
        lambda c: c["routes"].append(
            {
                "origin": c["origins"][0],
                "path": "/api/v1/panopto/checkInternetConnection",
                "operation": "materials.download",
                "methods": ["GET"],
            }
        ),
        lambda c: c["static_asset_origins"].append(c["origins"][1]),
        lambda c: c["static_resource_types"].append("media"),
        lambda c: c["selected_file_routes"][0].update(path_template="/upload/{anything}"),
        lambda c: c["routes"][1].update(query={"_": "encrypted"}),
        lambda c: c["routes"][0].update(query={"no": "board-item-id"}),
        lambda c: c.update(unreviewed_path=True),
        lambda c: c["routes"][0].update(unreviewed_path=True),
        lambda c: c["selected_file_routes"][0].update(unreviewed_path=True),
        lambda c: c.update(allowed_media=[{"mime": 4, "extensions": []}]),
        lambda c: c.update(max_bytes=True),
    ):
        config = copy.deepcopy(base)
        mutate(config)
        assert not UiRequestPolicy.from_reviewed_config(config).approved


def test_selected_file_binding_never_authorizes_another_file() -> None:
    policy = UiRequestPolicy.from_reviewed_config(_extended_config())
    url = "https://dcs-lcms.cnu.ac.kr/upload/synthetic/notes%20one.pdf"
    binding = bind_selected_file_request(
        policy, selected_file_id="file-001", resolved_url=url, source="official-control"
    )
    assert _guard(policy, url, selected_file=binding, selected_file_id="file-001") == "allow"
    with pytest.raises(UiRequestDenied):
        _guard(policy, url, selected_file=binding, selected_file_id="file-002")
    with pytest.raises(UiRequestDenied):
        _guard(policy, url.replace("one", "two"), selected_file=binding, selected_file_id="file-001")
    assert match_selected_file_path("/upload/{storage-id}/{encoded-filename}", "/upload/synthetic/notes%20one.pdf") == {
        "storage-id": "synthetic",
        "encoded-filename": "notes one.pdf",
    }
    for path in (
        "/upload/synthetic/notes%2fone.pdf",
        "/upload/synthetic/%2e%2e",
        "/upload/synthetic/%252e%252e",
        "/upload/synthetic/notes.pdf/extra",
        "/upload/synthetic/notes%5cone.pdf",
        "/upload/synthetic/notes%zz.pdf",
    ):
        assert match_selected_file_path("/upload/{storage-id}/{encoded-filename}", path) is None
        with pytest.raises(UiRequestDenied):
            bind_selected_file_request(
                policy,
                selected_file_id="file-001",
                resolved_url="https://dcs-lcms.cnu.ac.kr" + path,
                source="official-control",
            )
    for bad in (url + "?", url + "?_=12", url + "#fragment", url.replace("synthetic", "other")):
        with pytest.raises(UiRequestDenied):
            _guard(policy, bad, selected_file=binding, selected_file_id="file-001")
    for kwargs in ({"headers": {"Range": "bytes=0-1"}}, {"redirected_from": url}):
        with pytest.raises(UiRequestDenied):
            _guard(policy, url, selected_file=binding, selected_file_id="file-001", **kwargs)
    for source in ("page", "", None):
        with pytest.raises(UiRequestDenied):
            bind_selected_file_request(policy, selected_file_id="file-001", resolved_url=url, source=source)


class _FakeRequest:
    def __init__(
        self,
        url: str,
        method: str = "GET",
        resource_type: str = "document",
        headers: dict[str, str] | None = None,
        redirected_from: Any = None,
        frame: Any = None,
    ) -> None:
        self.url, self.method, self.resource_type = url, method, resource_type
        self.headers = headers or {}
        self.redirected_from = redirected_from
        if frame is not None:
            self.frame = frame

    async def all_headers(self) -> dict[str, str]:
        return self.headers


class _FakeRoute:
    def __init__(self, request: _FakeRequest) -> None:
        self.request = request
        self.action: str | None = None

    async def abort(self) -> None:
        self.action = "abort"

    async def continue_(self) -> None:
        self.action = "continue"


class _FakeTarget:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    async def route(self, pattern: str, handler: Any) -> None:
        self.handlers[pattern] = handler

    async def unroute(self, pattern: str, handler: Any) -> None:
        assert self.handlers[pattern] == handler
        del self.handlers[pattern]

    async def dispatch(self, request: _FakeRequest) -> str:
        route = _FakeRoute(request)
        await self.handlers["**/*"](route)
        assert route.action is not None
        return route.action


@pytest.mark.parametrize("reused_context", [False, True])
def test_interceptor_suppression_denial_and_scoped_cleanup(reused_context: bool) -> None:
    import asyncio

    async def scenario() -> None:
        policy = UiRequestPolicy.from_reviewed_config(_extended_config())
        target = _FakeTarget()
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target, policy, operation="materials.download", diagnostics=diagnostics
        )
        origin = "https://dcs-learning.cnu.ac.kr"
        try:
            assert await target.dispatch(_FakeRequest(origin + "/std/myLecture")) == "continue"
            assert (
                await target.dispatch(_FakeRequest(origin + "/js/common/panopto-Ab.js", resource_type="script"))
                == "abort"
            )
            assert (
                await target.dispatch(
                    _FakeRequest(
                        origin + "/api/v1/panopto/addInternetDisconnectionLog", method="POST", resource_type="xhr"
                    )
                )
                == "abort"
            )
            assert diagnostics.suppressed_count == 2
            assert diagnostics.suppressed_reasons == {"media-integration": 1, "logging": 1}
            interceptor.raise_if_denied()
            assert (
                await target.dispatch(_FakeRequest(origin + "/std/myLecture", headers={"RaNgE": "bytes=0-1"}))
                == "abort"
            )
            assert (
                await target.dispatch(
                    _FakeRequest(origin + "/login", redirected_from=_FakeRequest(origin + "/std/myLecture"))
                )
                == "abort"
            )
            with pytest.raises(UiRequestDenied) as caught:
                interceptor.raise_if_denied()
            assert caught.value.reason_code == "range"
        finally:
            await interceptor.close()
        assert target.handlers == {}
        if reused_context:
            next_operation = await install_ui_request_interceptor(
                target, policy, operation="materials.download", diagnostics=UiRequestDiagnostics()
            )
            await next_operation.close()
            assert target.handlers == {}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("path", "operation", "query_kind", "valid", "invalid"),
    [
        (
            "/properties/messages_ko.properties",
            "materials.download",
            {"_": "cachebuster"},
            ["?_=0", "?_=12345678901234567890"],
            ["?_=123456789012345678901", "?_=１２"],
        ),
        (
            "/api/v1/getAttachFileList",
            "materials.download",
            {"e": "encrypted"},
            ["?e=A+z/_-9=", "?e=A%2Bz%2F_%2D9%3D"],
            ["?e=", "?e=hello%252Fworld", "?e=A%26B", "?e=%2E%2E%2F", "?e=한글"],
        ),
        (
            "/std/taskView",
            "assignments.sync",
            {"curPage": "page"},
            ["?curPage=undefined", "?curPage=17"],
            ["?curPage=-1", "?curPage=Undefined"],
        ),
        (
            "/std/noticeDetail",
            "notices.fetch",
            {"no": "board-item-id", "curPage": "page"},
            ["?no=TB_L_BOARDITEM" + "123", "?curPage=1&no=TB_L_BOARDITEM" + "123"],
            ["?no=TB_L_BOARDITEMx", "?no=TB_L_BOARDITEM" + "1&no=TB_L_BOARDITEM" + "2"],
        ),
    ],
)
def test_reviewed_query_kinds(
    path: str, operation: str, query_kind: dict[str, str], valid: list[str], invalid: list[str]
) -> None:
    origin = "https://lms.example.invalid"
    config = {
        "approved": True,
        "read_only_evidence": "synthetic",
        "allowed_media": [],
        "max_bytes": None,
        "origins": [origin],
        "routes": [{"origin": origin, "path": path, "operation": operation, "methods": ["GET"], "query": query_kind}],
    }
    policy = UiRequestPolicy.from_reviewed_config(config)
    assert policy.approved
    for query in valid + [""]:
        assert (
            guard_ui_request(policy, origin + path + query, "GET", {}, operation=operation, resource_type="xhr")
            == "allow"
        )
    for query in invalid + ["?unknown=1", "#frag"]:
        with pytest.raises(UiRequestDenied):
            guard_ui_request(policy, origin + path + query, "GET", {}, operation=operation, resource_type="xhr")


def test_named_suppression_origin_need_not_be_an_allowed_origin() -> None:
    learning = "https://dcs-learning.cnu.ac.kr"
    content = "https://dcs-lcms.cnu.ac.kr"
    policy = UiRequestPolicy.from_reviewed_config(
        {
            "approved": True,
            "read_only_evidence": "synthetic",
            "origins": [content],
            "routes": [
                {"origin": content, "path": "/std/myLecture", "operation": "materials.sync", "methods": ["GET"]}
            ],
            "suppress": [
                {
                    "name": "panopto-saml-script",
                    "origin": learning,
                    "path_template": "/js/common/panoptoSaml-{hash}.js",
                    "operation": "materials.sync",
                    "methods": ["GET"],
                    "reason": "media-integration",
                }
            ],
            "allowed_media": [],
            "max_bytes": None,
        }
    )
    assert policy.approved
    assert (
        guard_ui_request(
            policy,
            learning + "/js/common/panoptoSaml-Ab.js",
            "GET",
            {},
            operation="materials.sync",
            resource_type="script",
        )
        == "suppress"
    )
    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            learning + "/std/myLecture",
            "GET",
            {},
            operation="materials.sync",
            resource_type="document",
        )
    assert caught.value.reason_code == "origin"


@pytest.mark.parametrize(
    "change",
    [
        {"path": "/api/v1/week/{week}/getStdActivityStatus"},
        {"path": "/api/v1/week/getStdActivityStatus*"},
        {"path": "/api/v1/activity/record"},
        {"resource_type": "document"},
        {"resource_type": "script"},
        {"resource_type": "fetch"},
        {"resource_type": []},
        {"resource_type": {}},
        {"methods": ["DELETE"]},
        {"methods": ["GET"]},
        {"methods": ["POST", "GET"]},
        {"logging_token_reviewed": False},
        {"operation": "unreviewed.operation"},
    ],
)
def test_reviewed_logging_exemption_rejects_invalid_routes(change: dict[str, Any]) -> None:
    from campusctl.commands.assignments import CAPABILITY

    reviewed = {**CAPABILITY["policy"], "routes": [dict(route) for route in CAPABILITY["policy"]["routes"]]}
    route = next(route for route in reviewed["routes"] if route.get("logging_token_reviewed"))
    route.update(change)
    assert not UiRequestPolicy.validate_reviewed_config(reviewed)
    assert not UiRequestPolicy.from_reviewed_config(reviewed).approved


def test_reviewed_logging_exemption_rejects_other_approved_origin() -> None:
    from campusctl.commands.assignments import CAPABILITY

    other = "https://other.example.invalid"
    reviewed = {
        **CAPABILITY["policy"],
        "origins": [*CAPABILITY["policy"]["origins"], other],
        "routes": [dict(route) for route in CAPABILITY["policy"]["routes"]],
    }
    route = next(route for route in reviewed["routes"] if route.get("logging_token_reviewed"))
    route["origin"] = other
    assert not UiRequestPolicy.validate_reviewed_config(reviewed)
    assert not UiRequestPolicy.from_reviewed_config(reviewed).approved


def test_logging_route_without_reviewed_exemption_is_denied() -> None:
    from campusctl.commands.assignments import CAPABILITY

    reviewed = {**CAPABILITY["policy"], "routes": [dict(route) for route in CAPABILITY["policy"]["routes"]]}
    route = next(route for route in reviewed["routes"] if route.get("logging_token_reviewed"))
    del route["logging_token_reviewed"], route["resource_type"]
    policy = UiRequestPolicy.from_reviewed_config(reviewed)
    assert policy.approved
    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            "https://dcs-learning.cnu.ac.kr/api/v1/week/getStdActivityStatus",
            "POST",
            {},
            operation="assignments.sync",
            resource_type="xhr",
        )
    assert caught.value.reason_code == "logging"


def test_lecture_sync_reviewed_routes_and_suppressions_are_exact() -> None:
    from campusctl.commands.notices import LECTURES_SYNC_POLICY

    origin = "https://dcs-learning.cnu.ac.kr"
    expected_get = {
        "/std/myLecture",
        "/std/lecture",
        "/std/notice",
        "/std/todo",
        "/std/course",
        "/properties/messages.properties",
        "/properties/messages_ko.properties",
    }
    expected_post = {
        "/api/v1/board/std/notice/list",
        "/api/v1/week/getStdTodoList",
        "/api/v1/course/getStdMyCourseList",
        "/api/v1/term/getYearTermList",
        "/api/v1/board/std/qna/list",
        "/api/v1/boardM/getBoardItemList",
        "/api/v1/common/checkEnableUrl",
        "/api/v1/course/addSessionCourseInfo",
        "/api/v1/board/courseNotice/list",
        "/api/v1/week/getStdWeekList",
        "/api/v1/week/getStdEtcList",
        "/api/v1/survey/getApplyPopList",
        "/api/v1/board/popup/noticeList",
        "/api/v1/board/notice/list/top",
        "/api/v1/board/notice/list",
        "/api/v1/user/getUserInfo",
        "/api/v1/user/getMenuList",
        "/api/v1/alarm/getAlarmListByDate",
        "/api/v1/course/get",
        "/api/v1/course/getCeShortcuts",
        "/api/v1/week/getStdActivityStatus",
    }
    assert LECTURES_SYNC_POLICY["approved"] is True
    assert LECTURES_SYNC_POLICY["read_only_evidence"] == "owner-held sanitized evidence (2026-09-27)"
    assert LECTURES_SYNC_POLICY["origins"] == [origin]
    assert LECTURES_SYNC_POLICY["allowed_media"] == []
    assert LECTURES_SYNC_POLICY["selected_file_routes"] == []
    routes = LECTURES_SYNC_POLICY["routes"]
    assert len(routes) == len(expected_get) + len(expected_post)
    assert {(route["path"], tuple(route["methods"])) for route in routes} == {
        *((path, ("GET",)) for path in expected_get),
        *((path, ("POST",)) for path in expected_post),
    }
    assert all(route["origin"] == origin and route["operation"] == "lectures.sync" for route in routes)
    assert {route["path"]: route["query"] for route in routes if "query" in route} == {
        "/properties/messages.properties": {"_": "cachebuster"},
        "/properties/messages_ko.properties": {"_": "cachebuster"},
    }
    assert [(route["path"], route["resource_type"]) for route in routes if route.get("logging_token_reviewed")] == [
        ("/api/v1/week/getStdActivityStatus", "xhr")
    ]
    assert {
        item["name"]: (item["origin"], item["path_template"], tuple(item["methods"]), item["reason"])
        for item in LECTURES_SYNC_POLICY["suppress"]
    } == {
        "panopto-script": (origin, "/js/common/panopto-{hash}.js", ("GET",), "media-integration"),
        "panopto-saml-script": (origin, "/js/common/panoptoSaml-{hash}.js", ("GET",), "media-integration"),
        "panopto-sso-popup": (
            "https://cnu.ap.panopto.com",
            "/Panopto/Pages/Auth/Login.aspx",
            ("POST",),
            "panopto-sso-popup",
        ),
        "course-roster-image": (origin, "/upload/dunetadmin/college/{hash}.png", ("GET",), "course-roster-image"),
        "favicon-icon": (origin, "/assets/images/favicon-{hash}.ico", ("GET",), "favicon"),
        "external-telemetry": ("http://0.0.0.0:3000", "/v1/events", ("POST",), "telemetry"),
        "panopto-disconnection-log": (origin, "/api/v1/panopto/addInternetDisconnectionLog", ("POST",), "logging"),
        "panopto-connectivity-check": (origin, "/api/v1/panopto/checkInternetConnection", ("GET",), "logging"),
    }
    assert len(LECTURES_SYNC_POLICY["suppress"]) == 8
    assert all(item["operation"] == "lectures.sync" for item in LECTURES_SYNC_POLICY["suppress"])
    assert UiRequestPolicy.validate_reviewed_config(LECTURES_SYNC_POLICY)
    policy = UiRequestPolicy.from_reviewed_config(LECTURES_SYNC_POLICY)
    assert policy.approved
    assert (
        guard_ui_request(policy, origin + "/std/course", "GET", {}, operation="lectures.sync", resource_type="document")
        == "allow"
    )
    assert (
        guard_ui_request(
            policy,
            origin + "/api/v1/week/getStdActivityStatus",
            "POST",
            {},
            operation="lectures.sync",
            resource_type="xhr",
        )
        == "allow"
    )
    assert (
        guard_ui_request(
            policy,
            "https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx",
            "POST",
            {},
            operation="lectures.sync",
            resource_type="document",
        )
        == "suppress"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"path": "/api/v1/week/getStdActivityStatusExtra"},
        {"methods": ["GET"]},
        {"resource_type": "fetch"},
        {"operation": "lectures.play"},
    ],
)
def test_lecture_sync_logging_exemption_rejects_unreviewed_variants(change: dict[str, Any]) -> None:
    from campusctl.commands.notices import LECTURES_SYNC_POLICY

    reviewed = {**LECTURES_SYNC_POLICY, "routes": [dict(route) for route in LECTURES_SYNC_POLICY["routes"]]}
    route = next(route for route in reviewed["routes"] if route.get("logging_token_reviewed"))
    route.update(change)
    assert not UiRequestPolicy.validate_reviewed_config(reviewed)
    assert not UiRequestPolicy.from_reviewed_config(reviewed).approved


@pytest.mark.parametrize(
    ("url", "method", "kind", "operation"),
    [
        ("https://dcs-learning.cnu.ac.kr/std/course", "POST", "document", "lectures.sync"),
        ("https://dcs-learning.cnu.ac.kr/std/lectureDetail", "GET", "document", "lectures.sync"),
        ("https://dcs-learning.cnu.ac.kr/api/v1/week/getStdActivityStatus", "POST", "fetch", "lectures.sync"),
        ("https://dcs-learning.cnu.ac.kr/std/course", "GET", "document", "notices.sync"),
        ("https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx", "POST", "document", "lectures.play"),
        ("https://dcs-learning.cnu.ac.kr/video/example.mp4", "GET", "media", "lectures.sync"),
    ],
)
def test_lecture_sync_does_not_expand_other_authority(url: str, method: str, kind: str, operation: str) -> None:
    from campusctl.commands.notices import LECTURES_SYNC_POLICY

    policy = UiRequestPolicy.from_reviewed_config(LECTURES_SYNC_POLICY)
    with pytest.raises(UiRequestDenied):
        guard_ui_request(policy, url, method, {}, operation=operation, resource_type=kind)


@pytest.mark.parametrize(
    ("domain", "operation"),
    [
        ("assignments", "assignments.sync"),
        ("materials", "materials.sync"),
        ("materials", "materials.download"),
        ("notices", "notices.sync"),
    ],
)
def test_reviewed_side_requests_and_fatal_boundaries(domain: str, operation: str) -> None:
    from campusctl.commands import assignments, materials, notices

    reviewed = {
        "assignments": assignments.CAPABILITY,
        "materials": materials.CAPABILITY,
        "notices": notices.CAPABILITY,
    }[domain]["policy"]
    policy = UiRequestPolicy.from_reviewed_config(reviewed)
    learning = "https://dcs-learning.cnu.ac.kr"
    status = learning + "/api/v1/week/getStdActivityStatus"
    assert policy.approved
    assert guard_ui_request(policy, status, "POST", {}, operation=operation, resource_type="xhr") == "allow"
    exact = (
        (learning + "/js/common/panoptoSaml-Ab_9.js", "GET", "script"),
        (learning + "/upload/dunetadmin/college/opaque-123.png", "GET", "image"),
        (learning + "/assets/images/favicon-Ab_9.ico", "GET", "other"),
        ("http://0.0.0.0:3000/v1/events", "POST", "fetch"),
    )
    for headers, redirected_from, expected in (
        ({"rAnGe": "bytes=0-1"}, None, "range"),
        ({}, learning + "/std/lecture", "redirect"),
    ):
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(
                policy,
                status,
                "POST",
                headers,
                operation=operation,
                resource_type="xhr",
                redirected_from=redirected_from,
            )
        assert caught.value.reason_code == expected
    for url, method, kind in exact:
        assert guard_ui_request(policy, url, method, {}, operation=operation, resource_type=kind) == "suppress"
        for kwargs in ({"headers": {"rAnGe": "bytes=0-1"}}, {"redirected_from": learning + "/std/myLecture"}):
            with pytest.raises(UiRequestDenied) as caught:
                guard_ui_request(
                    policy,
                    url,
                    method,
                    kwargs.get("headers", {}),
                    operation=operation,
                    resource_type=kind,
                    redirected_from=kwargs.get("redirected_from"),
                )
            assert caught.value.reason_code in {"range", "redirect"}
    for kind in ("script", "stylesheet", "image", "font", "media"):
        assert (
            guard_ui_request(
                policy,
                "https://assets.example.invalid/optional?theme=1",
                "GET",
                {},
                operation=operation,
                resource_type=kind,
            )
            == "suppress"
        )
    for method, kind in (("POST", "script"), ("GET", "xhr"), ("GET", "document"), ("GET", "fetch"), ("GET", "other")):
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(
                policy,
                "https://assets.example.invalid/optional",
                method,
                {},
                operation=operation,
                resource_type=kind,
            )
        assert caught.value.reason_code == "origin"
    for bad_url, kind in (
        (learning + "/upload/dunetadmin/college/opaque-123.png", "xhr"),
        (learning + "/upload/dunetadmin/college/opaque-123.png/extra", "image"),
        (learning + "/upload/dunetadmin/college/opaque-123.png?x=1", "image"),
        (learning + "/assets/images/favicon-Ab_9.ico", "xhr"),
        (learning + "/assets/images/favicon-Ab_9.ico?x=1", "other"),
        (learning + "/assets/images/favicon-Ab_9.ico/extra", "other"),
        (learning + "/js/common/panoptoSaml-Ab_9.js", "xhr"),
    ):
        with pytest.raises(UiRequestDenied):
            guard_ui_request(policy, bad_url, "GET", {}, operation=operation, resource_type=kind)
    for url, kind in (
        ("https://assets.example.invalid/v1/events", "fetch"),
        ("http://0.0.0.0:3000/v1/events?extra=1", "fetch"),
        ("http://0.0.0.0:3000/v1/events", "xhr"),
    ):
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(policy, url, "POST", {}, operation=operation, resource_type=kind)
        assert caught.value.reason_code == "origin"
    with pytest.raises(UiRequestDenied) as caught:
        guard_ui_request(
            policy,
            learning + "/api/v1/week/getStdActivityStatus",
            "GET",
            {},
            operation=operation,
            resource_type="xhr",
        )
    assert caught.value.reason_code == "logging"
    for other_operation, kind, url in (
        ("unreviewed.operation", "xhr", status),
        (operation, "fetch", status),
        (operation, "xhr", status + "?unexpected=1"),
        (operation, "xhr", learning + "/api/v1/panopto/addInternetDisconnectionLog/extra"),
    ):
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(policy, url, "POST", {}, operation=other_operation, resource_type=kind)
        assert caught.value.reason_code == "logging"
    assert (
        guard_ui_request(
            policy,
            learning + "/api/v1/panopto/addInternetDisconnectionLog",
            "POST",
            {},
            operation=operation,
            resource_type="xhr",
        )
        == "suppress"
    )
    for kwargs in ({"headers": {"Range": "bytes=0-1"}}, {"redirected_from": learning + "/std/myLecture"}):
        with pytest.raises(UiRequestDenied) as caught:
            guard_ui_request(
                policy,
                "https://assets.example.invalid/optional",
                "GET",
                kwargs.get("headers", {}),
                operation=operation,
                resource_type="script",
                redirected_from=kwargs.get("redirected_from"),
            )
        assert caught.value.reason_code in {"range", "redirect"}


@pytest.mark.parametrize(("domain", "operation"), [("assignments", "assignments.fetch"), ("notices", "notices.fetch")])
def test_fetch_interceptor_aborts_roster_side_requests_without_denial(domain: str, operation: str) -> None:
    import asyncio

    from campusctl.commands import assignments, notices

    class Popup:
        url = "https://dcs-learning.cnu.ac.kr/SSOServiceLogin"
        closed = False

        async def opener(self) -> Any:
            return SimpleNamespace(url="https://dcs-learning.cnu.ac.kr/std/myLecture")

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        config = assignments.FETCH_POLICY if domain == "assignments" else notices.FETCH_POLICY
        target = _FakeTarget()
        popup = Popup()
        target.pages = [popup]
        frame = SimpleNamespace(parent_frame=None, url=popup.url, page=popup)
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target, UiRequestPolicy.from_reviewed_config(config), operation=operation, diagnostics=diagnostics
        )
        try:
            for request in (
                _FakeRequest("https://dcs-learning.cnu.ac.kr/std/myLecture"),
                _FakeRequest(
                    "https://dcs-learning.cnu.ac.kr/upload/dunetadmin/college/Ab_9.png", resource_type="image"
                ),
                _FakeRequest("https://dcs-learning.cnu.ac.kr/assets/images/favicon-Ab_9.ico", resource_type="other"),
                _FakeRequest("http://0.0.0.0:3000/v1/events", method="POST", resource_type="fetch"),
                _FakeRequest("https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx", "POST", frame=frame),
            ):
                assert await target.dispatch(request) == (
                    "continue" if request.url.endswith("/std/myLecture") else "abort"
                )
                interceptor.raise_if_denied()
            assert popup.closed
            assert diagnostics.suppressed_count == 4
            assert diagnostics.suppressed_reasons == {
                "course-roster-image": 1,
                "favicon": 1,
                "telemetry": 1,
                "panopto-sso-popup": 1,
            }
        finally:
            await interceptor.close()

    asyncio.run(scenario())


def test_interceptor_counts_named_and_passive_suppressions() -> None:
    import asyncio

    from campusctl.commands.assignments import CAPABILITY

    async def scenario() -> None:
        target = _FakeTarget()
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target,
            UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"]),
            operation="assignments.sync",
            diagnostics=diagnostics,
        )
        try:
            for request in (
                _FakeRequest("https://assets.example.invalid/npm/hls.js@latest", resource_type="script"),
                _FakeRequest("https://assets.example.invalid/css2?display=swap", resource_type="stylesheet"),
                _FakeRequest("https://dcs-learning.cnu.ac.kr/js/common/panoptoSaml-Ab.js", resource_type="script"),
                _FakeRequest(
                    "https://dcs-learning.cnu.ac.kr/upload/dunetadmin/college/opaque.png", resource_type="image"
                ),
                _FakeRequest("https://dcs-learning.cnu.ac.kr/assets/images/favicon-Ab.ico", resource_type="other"),
                _FakeRequest("http://0.0.0.0:3000/v1/events", method="POST", resource_type="fetch"),
            ):
                assert await target.dispatch(request) == "abort"
                interceptor.raise_if_denied()
            assert (
                await target.dispatch(
                    _FakeRequest(
                        "https://dcs-learning.cnu.ac.kr/api/v1/week/getStdActivityStatus",
                        method="POST",
                        resource_type="xhr",
                    )
                )
                == "continue"
            )
            interceptor.raise_if_denied()
            assert diagnostics.suppressed_count == 6
            assert diagnostics.suppressed_reasons == {
                "third-party-asset": 2,
                "media-integration": 1,
                "course-roster-image": 1,
                "favicon": 1,
                "telemetry": 1,
            }
            assert await target.dispatch(_FakeRequest("https://assets.example.invalid/data", method="POST")) == "abort"
            with pytest.raises(UiRequestDenied) as caught:
                interceptor.raise_if_denied()
            assert caught.value.reason_code == "origin"
            assert diagnostics.suppressed_count == 6
        finally:
            await interceptor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("domain", "operation"),
    [
        ("assignments", "assignments.sync"),
        ("notices", "notices.sync"),
        ("materials", "materials.sync"),
        ("materials", "materials.download"),
    ],
)
def test_panopto_sso_popup_is_only_reviewed_for_nonplayback_operations(domain: str, operation: str) -> None:
    from campusctl.commands import assignments, materials, notices

    policy = UiRequestPolicy.from_reviewed_config(
        {"assignments": assignments, "notices": notices, "materials": materials}[domain].CAPABILITY["policy"]
    )
    url = "https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx"
    assert policy.approved
    assert guard_ui_request(policy, url, "POST", {}, operation=operation, resource_type="document") == "suppress"
    for invalid_url, method, resource_type, scope in (
        (url, "POST", "document", "lectures.play"),
        (url + "/other", "POST", "document", operation),
        (url, "GET", "document", operation),
        (url, "POST", "xhr", operation),
        ("https://other.panopto.com/Panopto/Pages/Auth/Login.aspx", "POST", "document", operation),
    ):
        with pytest.raises(UiRequestDenied):
            guard_ui_request(policy, invalid_url, method, {}, operation=scope, resource_type=resource_type)


def test_panopto_popup_is_aborted_counted_and_closed_without_latched_denial() -> None:
    import asyncio

    from campusctl.commands.materials import CAPABILITY

    class Popup:
        url = "https://dcs-learning.cnu.ac.kr/SSOServiceLogin"
        closed = False

        async def opener(self) -> Any:
            return SimpleNamespace(url="https://dcs-learning.cnu.ac.kr/std/myLecture")

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        popup = Popup()
        frame = SimpleNamespace(parent_frame=None, url=popup.url, page=popup)
        target = _FakeTarget()
        target.pages = [popup]
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target,
            UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"]),
            operation="materials.download",
            diagnostics=diagnostics,
        )
        request_url = "https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx"
        try:
            assert await target.dispatch(_FakeRequest(request_url, "POST", frame=frame)) == "abort"
            interceptor.raise_if_denied()
            assert popup.closed
            assert diagnostics.suppressed_reasons == {"panopto-sso-popup": 1}
        finally:
            await interceptor.close()
        other = Popup()
        wrong_frame = SimpleNamespace(parent_frame=None, url="https://dcs-learning.cnu.ac.kr/std/archive", page=other)
        target = _FakeTarget()
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target,
            UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"]),
            operation="materials.download",
            diagnostics=diagnostics,
        )
        try:
            assert await target.dispatch(_FakeRequest(request_url, "POST", frame=wrong_frame)) == "abort"
            interceptor.raise_if_denied()
            assert diagnostics.suppressed_reasons == {"panopto-sso-popup": 1}
            assert not other.closed
        finally:
            await interceptor.close()
        unrelated = Popup()
        target = _FakeTarget()
        target.pages = [unrelated]
        diagnostics = UiRequestDiagnostics()
        interceptor = await install_ui_request_interceptor(
            target,
            UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"]),
            operation="materials.download",
            diagnostics=diagnostics,
        )
        try:
            assert await target.dispatch(_FakeRequest(request_url, "POST")) == "abort"
            interceptor.raise_if_denied()
            assert diagnostics.suppressed_reasons == {"panopto-sso-popup": 1}
            assert not unrelated.closed
        finally:
            await interceptor.close()

    asyncio.run(scenario())
