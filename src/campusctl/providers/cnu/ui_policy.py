"""Fail-closed, operation-scoped CNU UI request boundary."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from campusctl.browser import current_profile, profile_span
from campusctl.envelope import CampusError

from .course_context import CourseSelection

_REASON_CODES = frozenset({"logging", "range", "origin", "route", "method", "media", "redirect"})
_LOG_WORDS = frozenset({"activity", "activities", "analytics", "beacon", "log", "logs", "logging", "telemetry"})
_REVIEWED_LOGGING_READS = frozenset(
    (
        "https://dcs-learning.cnu.ac.kr",
        "/api/v1/week/getStdActivityStatus",
        operation,
        "POST",
        "xhr",
    )
    for operation in ("assignments.sync", "materials.sync", "materials.download", "notices.sync")
)
_MEDIA_RESOURCE_TYPES = frozenset({"audio", "media", "stream", "video"})
_STATIC_TYPES = frozenset({"script", "stylesheet", "font", "image"})
_PASSIVE_TYPES = _STATIC_TYPES | {"media"}
_STATIC_EXTENSIONS = frozenset(
    {".js", ".css", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"}
)
_MEDIA_EXTENSIONS = frozenset(
    {
        ".aac",
        ".flv",
        ".m3u8",
        ".m4a",
        ".m4s",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mpd",
        ".ogg",
        ".ogv",
        ".ts",
        ".wav",
        ".webm",
    }
)
_SUPPRESS = {
    "panopto-script": ("GET", "/js/common/panopto-{hash}.js", "media-integration"),
    "panopto-disconnection-log": ("POST", "/api/v1/panopto/addInternetDisconnectionLog", "logging"),
    "panopto-connectivity-check": ("GET", "/api/v1/panopto/checkInternetConnection", "logging"),
    "panopto-sso-popup": ("POST", "/Panopto/Pages/Auth/Login.aspx", "panopto-sso-popup"),
}
_TELEMETRY_ORIGIN = "http://0.0.0.0:3000"
_SUPPRESS.update(
    {
        "panopto-saml-script": ("GET", "/js/common/panoptoSaml-{hash}.js", "media-integration"),
        "course-roster-image": ("GET", "/upload/dunetadmin/college/{hash}.png", "course-roster-image"),
        "favicon-icon": ("GET", "/assets/images/favicon-{hash}.ico", "favicon"),
        "external-telemetry": ("POST", "/v1/events", "telemetry"),
    }
)
_SUPPRESS_TYPES = {
    "panopto-saml-script": "script",
    "course-roster-image": "image",
    "favicon-icon": "other",
    "external-telemetry": "fetch",
    "panopto-sso-popup": "document",
}
_FILE_TEMPLATES = {
    "https://dcs-lcms.cnu.ac.kr": "/upload/{storage-id}/{encoded-filename}",
    "https://dcs-learning.cnu.ac.kr": "/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}",
}
_PANOPTO_ORIGIN = "https://cnu.ap.panopto.com"
_PANOPTO_OPERATIONS = frozenset({"assignments.sync", "notices.sync", "materials.sync", "materials.download"})
_QUERY_KINDS = {"_": "cachebuster", "e": "encrypted", "curPage": "page", "no": "board-item-id"}
_POLICY_KEYS = frozenset(
    {
        "approved",
        "read_only_evidence",
        "origins",
        "routes",
        "allowed_media",
        "max_bytes",
        "notes",
        "suppress",
        "static_asset_origins",
        "static_resource_types",
        "selected_file_routes",
    }
)
_REQUIRED_POLICY_KEYS = frozenset({"approved", "read_only_evidence", "origins", "routes", "allowed_media", "max_bytes"})

_COURSE_BOOTSTRAP_PATHS = frozenset(
    {
        "/api/v1/user/getUserInfo",
        "/api/v1/user/getMenuList",
        "/api/v1/alarm/getAlarmListByDate",
        "/api/v1/course/getCeShortcuts",
        "/api/v1/course/get",
        "/api/v1/common/checkEnableUrl",
        "/api/v1/boardM/getBoardItemList",
        "/api/v1/term/getYearTermList",
        "/api/v1/course/getStdMyCourseList",
        "/api/v1/board/courseNotice/list",
        "/api/v1/week/getStdWeekList",
        "/api/v1/week/getStdEtcList",
        "/api/v1/week/getStdActivityStatus",
        "/api/v1/survey/getApplyPopList",
        "/api/v1/board/popup/noticeList",
        "/properties/messages.properties",
        "/properties/messages_ko.properties",
    }
)


@dataclass(frozen=True, slots=True)
class _ReviewedRoute:
    origin: str
    path: str
    operation: str
    methods: frozenset[str]
    query: tuple[tuple[str, str], ...] = ()
    logging_token_reviewed: bool = False
    resource_type: str = ""


@dataclass(frozen=True, slots=True)
class _TemplateRoute:
    origin: str
    path_template: str
    operation: str
    methods: frozenset[str]
    name: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SelectedFileRequest:
    selected_file_id: str
    origin: str
    path: str
    source: Literal["official-control", "fileDownload-response"]
    operation: str


class UiRequestDenied(CampusError):
    """Safe denial raised before an unapproved response is consumed."""

    def __init__(self, reason_code: str) -> None:
        if reason_code not in _REASON_CODES:
            raise ValueError("Unknown UI request denial reason")
        self.reason_code = reason_code
        super().__init__(
            "policy-blocked",
            "An LMS request was blocked by the reviewed UI policy.",
            "Use only an owner-reviewed read-only operation and retry.",
            "error",
        )


@dataclass(frozen=True, slots=True)
class UiRequestPolicy:
    """Immutable pins; malformed review data yields the disabled default instance."""

    approved: bool = False
    origins: frozenset[str] = frozenset()
    routes: tuple[_ReviewedRoute, ...] = ()
    suppress: tuple[_TemplateRoute, ...] = ()
    static_asset_origins: frozenset[str] = frozenset()
    static_resource_types: frozenset[str] = frozenset()
    selected_file_routes: tuple[_TemplateRoute, ...] = ()

    @staticmethod
    def validate_reviewed_config(reviewed: Mapping[str, Any]) -> bool:
        """Validate the policy schema regardless of its approval state."""
        if (
            not isinstance(reviewed, Mapping)
            or not _REQUIRED_POLICY_KEYS.issubset(reviewed)
            or set(reviewed) - _POLICY_KEYS
            or type(reviewed["approved"]) is not bool
        ):
            return False
        evidence = reviewed["read_only_evidence"]
        if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
            return False
        origins, routes, media = reviewed["origins"], reviewed["routes"], reviewed["allowed_media"]
        max_bytes = reviewed["max_bytes"]
        if (
            not isinstance(origins, list)
            or any(not isinstance(origin, str) or _configured_origin(origin) != origin for origin in origins)
            or not isinstance(routes, list)
            or not isinstance(media, list)
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"mime", "extensions"}
                or not isinstance(item["mime"], str)
                or not item["mime"].strip()
                or not isinstance(item["extensions"], list)
                or any(not isinstance(ext, str) or not ext.strip() for ext in item["extensions"])
                for item in media
            )
            or (max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1))
            or ("notes" in reviewed and not isinstance(reviewed["notes"], str))
        ):
            return False
        origin_set = frozenset(origins)
        for item in routes:
            if (
                not isinstance(item, Mapping)
                or set(item)
                - {"origin", "path", "operation", "methods", "query", "logging_token_reviewed", "resource_type"}
                or _route_fields(item, origin_set) is None
                or not isinstance(item.get("path"), str)
                or not _is_exact_path(item["path"])
                or not isinstance(item.get("query", {}), dict)
                or ("logging_token_reviewed" in item and item["logging_token_reviewed"] is not True)
                or ("resource_type" in item and item.get("logging_token_reviewed") is not True)
                or (
                    item.get("logging_token_reviewed") is True
                    and (
                        not isinstance(item.get("resource_type"), str)
                        or item.get("query", {})
                        or len(item["methods"]) != 1
                        or (
                            item["origin"],
                            item["path"],
                            item["operation"],
                            item["methods"][0],
                            item["resource_type"],
                        )
                        not in _REVIEWED_LOGGING_READS
                    )
                )
                or any(
                    name not in _QUERY_KINDS or kind != _QUERY_KINDS[name]
                    for name, kind in item.get("query", {}).items()
                )
            ):
                return False
        for key, required in (
            ("suppress", {"name", "origin", "path_template", "operation", "methods", "reason"}),
            ("selected_file_routes", {"origin", "path_template", "operation", "methods"}),
        ):
            value = reviewed.get(key, [])
            if not isinstance(value, list):
                return False
            for item in value:
                if (
                    not isinstance(item, Mapping)
                    or set(item) != required
                    or _route_fields(item, origin_set, allow_external=key == "suppress") is None
                    or not isinstance(item["path_template"], str)
                    or (
                        key == "suppress" and (not isinstance(item["name"], str) or not isinstance(item["reason"], str))
                    )
                ):
                    return False
        static_origins = reviewed.get("static_asset_origins", [])
        static_types = reviewed.get("static_resource_types", [])
        return (
            isinstance(static_origins, list)
            and all(isinstance(o, str) and o in origin_set for o in static_origins)
            and isinstance(static_types, list)
            and all(isinstance(t, str) and t in _STATIC_TYPES for t in static_types)
        )

    @classmethod
    def from_reviewed_config(cls, reviewed: Mapping[str, Any]) -> UiRequestPolicy:
        """Reject an entire approval on any malformed or overbroad entry."""
        if not cls.validate_reviewed_config(reviewed) or reviewed["approved"] is False:
            return cls()
        evidence = reviewed.get("read_only_evidence")
        raw_origins, raw_routes = reviewed.get("origins"), reviewed.get("routes")
        if (
            not isinstance(evidence, str)
            or not evidence.strip()
            or not isinstance(raw_origins, list)
            or not isinstance(raw_routes, list)
        ):
            return cls()
        origins = [_configured_origin(origin) for origin in raw_origins]
        if not origins or any(origin is None for origin in origins) or len(set(origins)) != len(origins):
            return cls()
        origin_set = frozenset(origins)
        routes: list[_ReviewedRoute] = []
        for item in raw_routes:
            if not isinstance(item, Mapping) or set(item) - {
                "origin",
                "path",
                "operation",
                "methods",
                "query",
                "logging_token_reviewed",
                "resource_type",
            }:
                return cls()
            common = _route_fields(item, origin_set)
            path, query = item.get("path"), item.get("query", {})
            if common is None or not isinstance(path, str) or not _is_exact_path(path) or not isinstance(query, dict):
                return cls()
            if any(name not in _QUERY_KINDS or kind != _QUERY_KINDS[name] for name, kind in query.items()):
                return cls()
            if "_" in query and path not in {"/properties/messages.properties", "/properties/messages_ko.properties"}:
                return cls()
            if "e" in query and not path.endswith("/getAttachFileList"):
                return cls()
            if "curPage" in query and not path.endswith(("/taskView", "/noticeDetail")):
                return cls()
            if "no" in query and (
                path != "/std/noticeDetail" or common[1] != "notices.fetch" or common[2] != frozenset({"GET"})
            ):
                return cls()
            routes.append(
                _ReviewedRoute(
                    common[0],
                    path,
                    common[1],
                    common[2],
                    tuple(query.items()),
                    item.get("logging_token_reviewed") is True,
                    item.get("resource_type", ""),
                )
            )
        static_origins = reviewed.get("static_asset_origins", [])
        static_types = reviewed.get("static_resource_types", [])
        if (
            not isinstance(static_origins, list)
            or not isinstance(static_types, list)
            or any(
                _configured_origin(o) != o or o not in origin_set or not _is_learning_origin(o) for o in static_origins
            )
            or any(t not in _STATIC_TYPES for t in static_types)
            or len(static_origins) != len(set(static_origins))
            or len(static_types) != len(set(static_types))
        ):
            return cls()
        suppress: list[_TemplateRoute] = []
        names: set[tuple[str, str]] = set()
        raw_suppress = reviewed.get("suppress", [])
        if not isinstance(raw_suppress, list):
            return cls()
        for item in raw_suppress:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"name", "origin", "path_template", "operation", "methods", "reason"}
                or not isinstance(item.get("name"), str)
            ):
                return cls()
            common = _route_fields(item, origin_set, allow_external=True)
            name = item["name"]
            pin = _SUPPRESS.get(name)
            template, reason = item.get("path_template"), item.get("reason")
            if (
                common is None
                or pin is None
                or template != pin[1]
                or reason != pin[2]
                or common[2] != frozenset({pin[0]})
                or (
                    name == "panopto-sso-popup"
                    and (common[0] != _PANOPTO_ORIGIN or common[1] not in _PANOPTO_OPERATIONS)
                )
                or (name == "external-telemetry" and common[0] != _TELEMETRY_ORIGIN)
                or (name not in {"panopto-sso-popup", "external-telemetry"} and not _is_learning_origin(common[0]))
                or (common[1], name) in names
            ):
                return cls()
            names.add((common[1], name))
            if any(
                route.origin == common[0]
                and route.operation == common[1]
                and _template_match(template, route.path, hash_only=True) is not None
                for route in routes
            ):
                return cls()
            suppress.append(_TemplateRoute(common[0], template, common[1], common[2], name, reason))
        selected: list[_TemplateRoute] = []
        raw_selected = reviewed.get("selected_file_routes", [])
        if not isinstance(raw_selected, list):
            return cls()
        for item in raw_selected:
            if not isinstance(item, Mapping) or set(item) != {"origin", "path_template", "operation", "methods"}:
                return cls()
            common = _route_fields(item, origin_set)
            template = item.get("path_template")
            if (
                common is None
                or not isinstance(template, str)
                or template != _FILE_TEMPLATES.get(common[0])
                or common[1] != "materials.download"
                or common[2] != frozenset({"GET"})
            ):
                return cls()
            selected.append(_TemplateRoute(common[0], template, common[1], common[2]))
        return cls(
            True,
            origin_set,
            tuple(routes),
            tuple(suppress),
            frozenset(static_origins),
            frozenset(static_types),
            tuple(selected),
        )


def _route_fields(
    item: Mapping[str, Any], origins: frozenset[str], *, allow_external: bool = False
) -> tuple[str, str, frozenset[str]] | None:
    origin, operation, methods = item.get("origin"), item.get("operation"), item.get("methods")
    if (
        _configured_origin(origin) != origin
        or (not allow_external and origin not in origins)
        or not isinstance(operation, str)
        or not operation.strip()
        or not isinstance(methods, list)
        or not methods
        or any(
            not isinstance(method, str) or not _is_http_token(method) or method != method.upper() for method in methods
        )
        or len(methods) != len(set(methods))
    ):
        return None
    return origin, operation, frozenset(methods)


def _is_learning_origin(origin: str) -> bool:
    return origin == "https://dcs-learning.cnu.ac.kr" or origin.endswith(".invalid")


def _template_match(template: str, path: str, *, hash_only: bool = False) -> dict[str, str] | None:
    if hash_only:
        match = re.fullmatch(re.escape(template).replace(r"\{hash\}", r"(?P<hash>[A-Za-z0-9_-]+)"), path)
        return match.groupdict() if match else None
    return match_selected_file_path(template, path)


def match_selected_file_path(path_template: str, path: str) -> dict[str, str] | None:
    """Match a reviewed template without decoding a slash or normalizing a path."""
    if path_template not in _FILE_TEMPLATES.values() or not isinstance(path, str):
        return None
    template_parts, parts = path_template.split("/"), path.split("/")
    if len(parts) != len(template_parts):
        return None
    captures: dict[str, str] = {}
    for expected, segment in zip(template_parts, parts, strict=True):
        if expected.startswith("{") and expected.endswith("}"):
            if not segment or re.search(r"%(?![0-9a-fA-F]{2})", segment):
                return None
            decoded = unquote(segment)
            if (
                not decoded
                or decoded in {".", ".."}
                or "/" in decoded
                or "\\" in decoded
                or re.search(r"%[0-9a-fA-F]{2}", decoded)
                or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            ):
                return None
            captures[expected[1:-1]] = decoded
        elif expected != segment:
            return None
    return captures


def bind_selected_file_request(
    policy: UiRequestPolicy,
    *,
    selected_file_id: str,
    resolved_url: str,
    source: Literal["official-control", "fileDownload-response"],
    operation: str = "materials.download",
) -> SelectedFileRequest:
    """Bind one official selected-file URL, never a whole template."""
    origin, path, query, fragment = _request_parts(resolved_url)
    if (
        not policy.approved
        or not isinstance(selected_file_id, str)
        or not selected_file_id
        or source not in {"official-control", "fileDownload-response"}
        or query
        or fragment
        or not any(
            route.origin == origin
            and route.operation == operation
            and match_selected_file_path(route.path_template, path) is not None
            for route in policy.selected_file_routes
        )
    ):
        raise UiRequestDenied("route")
    return SelectedFileRequest(selected_file_id, origin, path, source, operation)


def _suppression_reason(
    policy: UiRequestPolicy, origin: str, path: str, query: str, method: str, operation: str, resource_type: str
) -> str | None:
    if query:
        return None
    for entry in policy.suppress:
        if (
            entry.origin == origin
            and entry.operation == operation
            and method in entry.methods
            and (_SUPPRESS_TYPES.get(entry.name) is None or resource_type.casefold() == _SUPPRESS_TYPES[entry.name])
            and _template_match(entry.path_template, path, hash_only=True) is not None
        ):
            return entry.reason
    return None


def guard_ui_request(
    policy: UiRequestPolicy,
    url: str,
    method: str,
    headers: Mapping[str, str],
    *,
    operation: str,
    resource_type: str,
    redirected_from: str | None = None,
    selected_file: SelectedFileRequest | None = None,
    selected_file_id: str | None = None,
) -> Literal["allow", "suppress"]:
    """Guard each complete request; suppress only a reviewed, aborted side request."""
    if redirected_from is not None:
        raise UiRequestDenied("redirect")
    if any(isinstance(name, str) and name.casefold() == "range" for name in headers):
        raise UiRequestDenied("range")
    origin, path, query, fragment = _request_parts(url)
    if origin is None or (not policy.approved and origin not in policy.origins):
        raise UiRequestDenied("origin")
    if not policy.approved or path is None or fragment:
        raise UiRequestDenied("route")
    normalized_method = method.upper() if isinstance(method, str) and _is_http_token(method) else ""
    if not normalized_method:
        raise UiRequestDenied("method")
    if _suppression_reason(policy, origin, path, query, normalized_method, operation, resource_type) is not None:
        return "suppress"
    if origin not in policy.origins:
        if normalized_method == "GET" and resource_type.casefold() in _PASSIVE_TYPES:
            return "suppress"
        raise UiRequestDenied("origin")
    if any(
        route.logging_token_reviewed
        and (route.origin, route.path, route.operation) == (origin, path, operation)
        and normalized_method in route.methods
        and resource_type.casefold() == route.resource_type
        and not query
        for route in policy.routes
    ):
        return "allow"
    if _is_logging_path(path):
        raise UiRequestDenied("logging")
    if resource_type.casefold() in _MEDIA_RESOURCE_TYPES or _is_media_path(path):
        raise UiRequestDenied("media")
    if selected_file is not None and (origin, path, operation) == (
        selected_file.origin,
        selected_file.path,
        selected_file.operation,
    ):
        if (
            not query
            and normalized_method == "GET"
            and selected_file_id == selected_file.selected_file_id
            and any(
                route.origin == origin
                and route.operation == operation
                and normalized_method in route.methods
                and match_selected_file_path(route.path_template, path) is not None
                for route in policy.selected_file_routes
            )
        ):
            return "allow"
        raise UiRequestDenied("route")
    if any(
        route.origin == origin
        and route.operation == operation
        and match_selected_file_path(route.path_template, path) is not None
        for route in policy.selected_file_routes
    ):
        raise UiRequestDenied("route")
    if (
        normalized_method == "GET"
        and origin in policy.static_asset_origins
        and resource_type.casefold() in policy.static_resource_types
        and _is_exact_path(path)
        and not path.startswith(("/api/", "/std/", "/file/", "/upload/", "/js/common/panopto"))
        and any(path.casefold().endswith(extension) for extension in _STATIC_EXTENSIONS)
    ):
        return "allow"
    matching = [
        route for route in policy.routes if (route.origin, route.path, route.operation) == (origin, path, operation)
    ]
    if not matching:
        raise UiRequestDenied("route")
    if not any(normalized_method in route.methods for route in matching):
        raise UiRequestDenied("method")
    if not any(_valid_query(query, route.query) for route in matching if normalized_method in route.methods):
        raise UiRequestDenied("route")
    return "allow"


def _valid_query(query: str, validators: tuple[tuple[str, str], ...]) -> bool:
    if not query:
        return True
    if not validators:
        return False
    allowed = dict(validators)
    params = []
    for part in query.split("&"):
        if "=" not in part:
            return False
        params.append(part.split("=", 1))
    if len(params) != len({name for name, _ in params}):
        return False
    for name, value in params:
        kind = allowed.get(name)
        if kind == "cachebuster" and re.fullmatch(r"[0-9]{1,20}", value):
            continue
        if (
            kind == "encrypted"
            and re.fullmatch(r"(?:[A-Za-z0-9_+/=-]|%[0-9A-Fa-f]{2}){1,4096}", value)
            and re.fullmatch(r"[A-Za-z0-9_+/=-]{1,4096}", unquote(value))
        ):
            continue
        if kind == "page" and (value == "undefined" or re.fullmatch(r"[0-9]+", value)):
            continue
        if kind == "board-item-id" and re.fullmatch(r"TB_L_BOARDITEM[0-9]+", value):
            continue
        return False
    return True


def _configured_origin(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    origin, path, query, fragment = _request_parts(value)
    if origin != value or path not in {"", "/"} or query or fragment:
        return None
    return origin


def _request_parts(url: str) -> tuple[str | None, str | None, str, str]:
    if not isinstance(url, str) or not url or "\\" in url or any(char.isspace() or ord(char) < 32 for char in url):
        return None, None, "", ""
    try:
        parsed = urlsplit(url)
        scheme, hostname, port = parsed.scheme.casefold(), parsed.hostname, parsed.port
    except ValueError:
        return None, None, "", ""
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.netloc
        or "@" in parsed.netloc
    ):
        return None, None, "", ""
    host = hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default_port else ""
    query = parsed.query or ("?" if "?" in url.split("#", 1)[0] else "")
    fragment = parsed.fragment or ("#" if "#" in url else "")
    return f"{scheme}://{host}{suffix}", parsed.path or "/", query, fragment


def _is_exact_path(path: str) -> bool:
    return (
        path.startswith("/")
        and not path.startswith("//")
        and not any(c in path for c in "?#\\*%")
        and not any(ord(c) < 32 or ord(c) == 127 for c in path)
        and all(segment not in {".", ".."} for segment in path.split("/"))
    )


def _is_http_token(value: str) -> bool:
    return bool(value) and all(c.isascii() and (c.isalnum() or c in "!#$%&'*+-.^_`|~") for c in value)


def _is_logging_path(path: str) -> bool:
    for segment in unquote(path).split("/"):
        words = re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", segment).casefold())
        if any(word in _LOG_WORDS or word.startswith("attend") for word in words):
            return True
        if "internet" in words and "connection" in words:
            return True
    return False


def _is_media_path(path: str) -> bool:
    decoded = unquote(path).casefold()
    return any(segment in {"media", "stream", "streams"} for segment in decoded.split("/")) or any(
        decoded.endswith(extension) for extension in _MEDIA_EXTENSIONS
    )


@dataclass(slots=True)
class UiRequestDiagnostics:
    suppressed_count: int = 0
    suppressed_reasons: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OperationEpoch:
    """Reviewed operation window tied to a committed document and one selected course."""

    number: int
    operation: str
    policy: UiRequestPolicy
    course_id: str | None = None
    selection_epoch: int | None = None
    frame: Any | None = None
    document_url: str | None = None
    navigation_path: str | None = None
    phase: str = "legacy"


class UiRequestInterceptor:
    """One installed route handler; retain denials that Playwright callback dispatch swallows."""

    def __init__(
        self,
        page_or_context: Any,
        policy: UiRequestPolicy,
        *,
        operation: str,
        diagnostics: UiRequestDiagnostics,
        selected_file: SelectedFileRequest | None = None,
        selected_file_id: str | None = None,
        require_selection: bool = False,
    ) -> None:
        self._target = page_or_context
        self._policy = policy
        self._operation = operation
        self._diagnostics = diagnostics
        self._selected_file = selected_file
        self._selected_file_id = selected_file_id
        self._denial: UiRequestDenied | None = None
        self._capture: tuple[str, Callable[[Any, Any], Awaitable[None]]] | None = None
        self._installed = False
        self._epoch = OperationEpoch(1, operation, policy)
        self._quarantined = False
        self._require_selection = require_selection
        self._selection: CourseSelection | None = None
        self._selection_requested = False
        self._selection_request: Any | None = None
        self._selection_response: asyncio.Task[tuple[str, Any]] | None = None
        self._selection_document_requested = False
        self._roster_document_requested = False
        self._selection_completed = False
        self._selection_course_id: str | None = None

    @property
    def epoch(self) -> OperationEpoch:
        return self._epoch

    def quarantine(self) -> None:
        """Stop old authority before admitting any request from another section."""
        self._quarantined = True
        self._capture = None
        self._selected_file = None
        self._selected_file_id = None

    def arm_roster(self, *, frame: Any, document_url: str) -> OperationEpoch:
        """Pin the first post-login roster navigation beneath the same catch-all."""
        self.raise_if_denied()
        origin, path, _, fragment = _request_parts(document_url)
        if (
            not self._require_selection
            or self._epoch.phase != "legacy"
            or frame is None
            or origin not in self._epoch.policy.origins
            or path is None
            or fragment
            or getattr(frame, "url", None) != document_url
        ):
            raise UiRequestDenied("route")
        old = self._epoch
        self._epoch = OperationEpoch(
            old.number,
            old.operation,
            old.policy,
            frame=frame,
            document_url=document_url,
            navigation_path="/std/myLecture",
            phase="roster-entry",
        )
        self._roster_document_requested = False
        return self._epoch

    def bind_roster(self, *, frame: Any, document_url: str) -> None:
        """Require the reviewed roster document to commit before selecting a course."""
        epoch = self._epoch
        self.raise_if_denied()
        if (
            epoch.phase != "roster-entry"
            or not self._roster_document_requested
            or frame is not epoch.frame
            or getattr(frame, "url", None) != document_url
            or _request_parts(document_url)[:2] != (_request_parts(epoch.document_url)[0], "/std/myLecture")
        ):
            raise UiRequestDenied("route")
        self._epoch = replace(epoch, document_url=document_url, navigation_path=None, phase="roster")

    def arm_selection(self, *, frame: Any, document_url: str, settled: bool = True) -> OperationEpoch:
        """Open only the roster-to-course-entry selection window under the same route."""
        self.raise_if_denied()
        origin, path, _, fragment = _request_parts(document_url)
        if (
            not self._require_selection
            or not settled
            or frame is None
            or path != "/std/myLecture"
            or fragment
            or origin not in self._epoch.policy.origins
            or getattr(frame, "url", None) != document_url
            or (self._selection is None and (self._epoch.phase != "roster" or self._quarantined))
            or (self._selection is not None and (not self._quarantined or self._epoch.document_url != document_url))
        ):
            raise UiRequestDenied("route")
        old = self._epoch
        selection_epoch = 1 if self._selection is None else old.selection_epoch + 1
        self._epoch = OperationEpoch(
            old.number if self._selection is None else old.number + 1,
            old.operation,
            old.policy,
            selection_epoch=selection_epoch,
            frame=frame,
            document_url=document_url,
            navigation_path="/std/lecture",
            phase="selection",
        )
        self._selection = None
        self._selection_requested = False
        self._selection_request = None
        if self._selection_response is not None and not self._selection_response.done():
            self._selection_response.cancel()
        self._selection_response = None
        self._selection_document_requested = False
        self._selection_completed = False
        self._selection_course_id = None
        self._quarantined = False
        return self._epoch

    def bind_selection(self, selection: Any, *, frame: Any, document_url: str) -> None:
        """Arm the first committed course-entry document of the installed route."""
        self.raise_if_denied()
        if (
            self._quarantined
            or self._epoch.course_id is not None
            or not isinstance(selection, CourseSelection)
            or (self._require_selection and not self._selection_requested)
            or (self._require_selection and not self._selection_completed)
            or (self._require_selection and selection.course_id != self._selection_course_id)
            or (self._require_selection and not self._selection_document_requested)
            or (self._require_selection and selection.epoch != self._epoch.selection_epoch)
            or (
                self._require_selection
                and (
                    frame is not self._epoch.frame
                    or _request_parts(document_url)[1] != self._epoch.navigation_path
                    or _request_parts(document_url)[0] != _request_parts(self._epoch.document_url)[0]
                    or getattr(frame, "url", None) != document_url
                )
            )
            or not selection.course_id
            or selection.epoch < 1
            or frame is None
            or _request_parts(document_url)[1] is None
        ):
            raise UiRequestDenied("route")
        epoch = self._epoch
        self._epoch = OperationEpoch(
            epoch.number,
            epoch.operation,
            epoch.policy,
            selection.course_id,
            selection.epoch,
            frame,
            document_url,
            phase="bound",
        )
        self._selection = selection

    def activate(
        self,
        policy: UiRequestPolicy,
        *,
        operation: str,
        selection: Any,
        frame: Any,
        document_url: str,
        navigation_path: str,
        settled: bool,
    ) -> OperationEpoch:
        """Switch only after the old section settled and its selection remains bound.

        The navigation path is one exact reviewed next document, never a route union.
        A caller must re-establish the committed page/selection after navigating.
        """
        self.raise_if_denied()
        if (
            not settled
            or not self._quarantined
            or not policy.approved
            or selection is not self._selection
            or not isinstance(selection.course_id, str)
            or selection.course_id != self._epoch.course_id
            or not selection.course_id
            or not document_url
            or not navigation_path.startswith("/")
            or not _is_exact_path(navigation_path)
            or _request_parts(document_url)[1] is None
            or frame is None
            or getattr(frame, "url", None) != document_url
        ):
            raise UiRequestDenied("route")
        next_epoch = OperationEpoch(
            self._epoch.number + 1,
            operation,
            policy,
            selection.course_id,
            selection.epoch,
            frame,
            document_url,
            navigation_path,
            "navigation",
        )
        self._epoch = next_epoch
        self._policy = policy
        self._operation = operation
        self._quarantined = False
        return next_epoch

    def bind_document(self, *, frame: Any, document_url: str, selection: Any) -> None:
        """Replace the navigation window only after a selected document commits."""
        epoch = self._epoch
        self.raise_if_denied()
        if (
            self._quarantined
            or frame is not epoch.frame
            or selection is not self._selection
            or selection.course_id != epoch.course_id
            or _request_parts(document_url)[1] != epoch.navigation_path
            or getattr(frame, "url", None) != document_url
        ):
            raise UiRequestDenied("route")
        self._epoch = OperationEpoch(
            epoch.number,
            epoch.operation,
            epoch.policy,
            epoch.course_id,
            epoch.selection_epoch,
            frame,
            document_url,
            phase="bound",
        )

    def _allowed_bootstrap(
        self, request: Any, path: str | None, referer: str | None, frame: Any, epoch: OperationEpoch
    ) -> bool:
        if not self._bootstrap_request(request, path, referer, frame):
            return False
        origin, _, _, _ = _request_parts(request.url)
        if request.resource_type in _STATIC_TYPES:
            return origin in epoch.policy.static_asset_origins
        return any(
            route.origin == origin
            and route.path == path
            and route.operation == epoch.operation
            and request.method in route.methods
            for route in epoch.policy.routes
        )

    def _check_epoch_request(self, request: Any, headers: Mapping[str, str], epoch: OperationEpoch) -> None:
        if self._denial is not None:
            raise self._denial
        if self._quarantined or epoch is not self._epoch:
            raise UiRequestDenied("route")
        if epoch.frame is None:
            if self._require_selection:
                raise UiRequestDenied("route")
            return  # Existing single-operation interceptors do not switch epochs.
        origin, path, query, _ = _request_parts(request.url)
        reason = _suppression_reason(
            epoch.policy, origin, path, query, request.method.upper(), epoch.operation, request.resource_type
        )
        if reason is not None and (
            reason == "panopto-sso-popup"
            or (
                epoch.phase in {"roster-entry", "roster", "selection"} and reason in {"logging", "telemetry", "favicon"}
            )
        ):
            return  # Exact reviewed suppressions still pass through guard_ui_request.
        try:
            frame = request.frame
        except Exception:
            raise UiRequestDenied("route") from None
        if frame is not epoch.frame:
            raise UiRequestDenied("route")
        referer = next((value for key, value in headers.items() if key.casefold() == "referer"), None)
        if path == "/api/v1/course/addSessionCourseInfo" and epoch.phase != "selection":
            raise UiRequestDenied("route")
        if epoch.phase == "roster-entry":
            if request.resource_type == "document":
                if (
                    self._roster_document_requested
                    or origin != _request_parts(epoch.document_url)[0]
                    or path != "/std/myLecture"
                    or (referer is not None and referer != epoch.document_url)
                ):
                    raise UiRequestDenied("route")
                self._roster_document_requested = True
            elif not self._roster_document_requested or not self._allowed_bootstrap(
                request, path, referer, frame, epoch
            ):
                raise UiRequestDenied("route")
        elif epoch.phase == "roster":
            if not self._allowed_bootstrap(request, path, referer, frame, epoch):
                raise UiRequestDenied("route")
        elif epoch.phase == "selection":
            if (
                referer == epoch.document_url
                and request.method == "POST"
                and path == "/api/v1/course/addSessionCourseInfo"
            ):
                if self._selection_requested:
                    raise UiRequestDenied("route")
                self._selection_requested = True
                self._selection_request = request
            elif request.resource_type == "document" and referer == epoch.document_url:
                if (
                    origin != _request_parts(epoch.document_url)[0]
                    or path != epoch.navigation_path
                    or not self._selection_requested
                    or self._selection_document_requested
                ):
                    raise UiRequestDenied("route")
                self._selection_document_requested = True
            elif not self._selection_document_requested or not self._allowed_bootstrap(
                request, path, referer, frame, epoch
            ):
                raise UiRequestDenied("route")
        elif request.resource_type == "document":
            if (
                epoch.phase != "navigation"
                or path != epoch.navigation_path
                or origin != _request_parts(epoch.document_url)[0]
                or referer != epoch.document_url
            ):
                raise UiRequestDenied("route")
        elif epoch.phase != "bound" or referer != epoch.document_url:
            raise UiRequestDenied("route")

    @staticmethod
    def _bootstrap_request(request: Any, path: str | None, referer: str | None, frame: Any) -> bool:
        """Only reviewed read-only background from the committed roster/entry page."""
        document = getattr(frame, "url", None)
        return (
            referer == document
            and _request_parts(document)[1] in {"/std/myLecture", "/std/lecture"}
            and (
                (request.method == "GET" and request.resource_type in _STATIC_TYPES)
                or (path in _COURSE_BOOTSTRAP_PATHS and request.resource_type in {"xhr", "fetch"})
            )
        )

    async def install(self) -> UiRequestInterceptor:
        await self._target.route("**/*", self._handle)
        self._installed = True
        return self

    def capture_response(self, path: str, handler: Callable[[Any, Any], Awaitable[None]]) -> None:
        """Read one reviewed response before fulfilling the original page request."""
        self._capture = (path, handler)

    def stop_capture(self) -> None:
        self._capture = None

    def bind_selected_document(self, selected_file: SelectedFileRequest) -> None:
        """Permit only the already bound file and abort its redundant browser navigation."""
        if selected_file.operation != self._operation:
            raise UiRequestDenied("route")
        self._selected_file = selected_file
        self._selected_file_id = selected_file.selected_file_id

    def unbind_selected_document(self) -> None:
        self._selected_file = None
        self._selected_file_id = None

    async def _sso_popup(self, request: Any) -> tuple[bool, Any | None]:
        """Check the popup when Playwright exposes its frame; navigation may hide it."""
        try:
            frame = request.frame
        except Exception:
            return True, None
        try:
            if frame.parent_frame is not None or _request_parts(frame.url)[:2] != (
                "https://dcs-learning.cnu.ac.kr",
                "/SSOServiceLogin",
            ):
                return False, None
            page = frame.page
            opener = await page.opener()
            if opener is None or _request_parts(opener.url)[:2] != (
                "https://dcs-learning.cnu.ac.kr",
                "/std/myLecture",
            ):
                return False, None
            return True, page
        except Exception:
            return True, None

    async def _close_sso_popup(self, popup: Any | None) -> None:
        """Close only the popup tied to the suppressed request; otherwise its own script closes it."""
        if popup is not None:
            with suppress(Exception):
                await asyncio.wait_for(popup.close(), timeout=5)

    async def _handle(self, route: Any) -> None:
        with profile_span("guard-disposition"):
            await self._handle_request(route)

    async def _read_selection_response(self, request: Any, epoch: OperationEpoch) -> tuple[str, Any]:
        response = await asyncio.wait_for(request.response(), timeout=15)
        if response is None or response.status != 200:
            raise UiRequestDenied("route")
        if await asyncio.wait_for(response.finished(), timeout=15) is not None:
            raise UiRequestDenied("route")
        payload = await asyncio.wait_for(response.json(), timeout=15)
        header = payload.get("header") if isinstance(payload, dict) else None
        body = payload.get("body") if isinstance(payload, dict) else None
        data = body.get("data") if isinstance(body, dict) else None
        course_id = data.get("course_id") if isinstance(data, dict) else None
        if (
            not isinstance(header, dict)
            or header.get("code") != 200
            or not isinstance(body, dict)
            or body.get("result") != "Y"
            or not isinstance(course_id, str)
            or not course_id
            or epoch is not self._epoch
            or self._quarantined
        ):
            raise UiRequestDenied("route")
        return course_id, response

    def _selection_response_done(self, finished: asyncio.Task[tuple[str, Any]]) -> None:
        if finished.cancelled():
            return
        try:
            finished.result()
        except Exception:
            if self._denial is None:
                self._denial = UiRequestDenied("route")

    async def _handle_request(self, route: Any) -> None:
        request = route.request
        epoch = self._epoch
        selection_request = self._selection_request
        capture = self._capture
        selected_file = self._selected_file
        selected_file_id = self._selected_file_id
        recorder = current_profile()
        resource_type = request.resource_type
        category = (
            resource_type
            if resource_type in {"document", "xhr", "fetch"}
            else "static"
            if resource_type in _STATIC_TYPES
            else "other"
        )
        try:
            previous = request.redirected_from
            with profile_span("guard-headers"):
                headers = await request.all_headers()
            self._check_epoch_request(request, headers, epoch)
            with profile_span("guard-decision"):
                decision = guard_ui_request(
                    epoch.policy,
                    request.url,
                    request.method,
                    headers,
                    operation=epoch.operation,
                    resource_type=resource_type,
                    redirected_from=previous.url if previous is not None else None,
                    selected_file=selected_file,
                    selected_file_id=selected_file_id,
                )
        except UiRequestDenied as denial:
            if self._denial is None:
                self._denial = denial
            if recorder is not None:
                recorder.request(category, "blocked")
            await route.abort()
            return
        # Capture and authority are fixed before awaiting request headers.
        if decision == "suppress":
            origin, path, query, _ = _request_parts(request.url)
            reason = (
                _suppression_reason(
                    epoch.policy, origin, path, query, request.method.upper(), epoch.operation, request.resource_type
                )
                or "third-party-asset"
            )
            popup = None
            if reason == "panopto-sso-popup":
                valid_popup, popup = await self._sso_popup(request)
                if not valid_popup:
                    if self._denial is None:
                        self._denial = UiRequestDenied("route")
                    await route.abort()
                    if recorder is not None:
                        recorder.request(category, "blocked")
                    return
            await route.abort()
            self._diagnostics.suppressed_count += 1
            self._diagnostics.suppressed_reasons[reason] = self._diagnostics.suppressed_reasons.get(reason, 0) + 1
            if recorder is not None:
                recorder.request(category, "suppressed")
            if reason == "panopto-sso-popup":
                await self._close_sso_popup(popup)
        elif (
            selected_file is not None
            and request.method == "GET"
            and request.url == f"{selected_file.origin}{selected_file.path}"
        ):
            if request.resource_type != "document":
                if self._denial is None:
                    self._denial = UiRequestDenied("route")
                await route.abort()
                if recorder is not None:
                    recorder.request(category, "blocked")
            else:
                await route.abort()
                self._diagnostics.suppressed_count += 1
                reasons = self._diagnostics.suppressed_reasons
                reasons["duplicate-download"] = reasons.get("duplicate-download", 0) + 1
                if recorder is not None:
                    recorder.request("attachment", "suppressed")
        elif (
            self._require_selection
            and epoch.phase == "selection"
            and request.resource_type == "document"
            and urlsplit(request.url).path == epoch.navigation_path
        ):
            try:
                if (
                    selection_request is None
                    or self._selection_response is None
                    or epoch is not self._epoch
                    or self._quarantined
                ):
                    raise UiRequestDenied("route")
                course_id, response = await asyncio.wait_for(self._selection_response, timeout=15)
                if epoch is not self._epoch or self._quarantined:
                    raise UiRequestDenied("route")
                if capture is not None and capture[0] == "/api/v1/course/addSessionCourseInfo":
                    await capture[1](selection_request, response)
                self._selection_course_id = course_id
                self._selection_completed = True
            except Exception:
                if self._denial is None:
                    self._denial = UiRequestDenied("route")
                if recorder is not None:
                    recorder.request(category, "blocked")
                await route.abort()
            else:
                if recorder is not None:
                    recorder.request(category, "allowed")
                await route.continue_()
        elif (
            capture is not None
            and urlsplit(request.url).path == capture[0]
            and request.method.upper() == "POST"
            and not (
                self._require_selection
                and epoch.phase == "selection"
                and capture[0] == "/api/v1/course/addSessionCourseInfo"
            )
        ):
            if recorder is not None:
                recorder.request(category, "allowed")
            response = await route.fetch(max_redirects=0)
            try:
                await capture[1](request, response)
            finally:
                await route.fulfill(response=response)
        elif (
            self._require_selection
            and epoch.phase == "selection"
            and urlsplit(request.url).path == "/api/v1/course/addSessionCourseInfo"
            and request.method.upper() == "POST"
        ):
            if recorder is not None:
                recorder.request(category, "allowed")
            await route.continue_()
            self._selection_response = asyncio.create_task(self._read_selection_response(request, epoch))
            self._selection_response.add_done_callback(self._selection_response_done)
        else:
            if recorder is not None:
                recorder.request(category, "allowed")
            await route.continue_()

    def raise_if_denied(self) -> None:
        if self._denial is not None:
            raise self._denial

    async def close(self) -> None:
        if self._selection_response is not None and not self._selection_response.done():
            self._selection_response.cancel()
        if self._installed:
            self._installed = False
            await self._target.unroute("**/*", self._handle)


async def install_ui_request_interceptor(
    page_or_context: Any,
    policy: UiRequestPolicy,
    *,
    operation: str,
    diagnostics: UiRequestDiagnostics,
    selected_file: SelectedFileRequest | None = None,
    selected_file_id: str | None = None,
    require_selection: bool = False,
) -> UiRequestInterceptor:
    """Install before the first guarded navigation; close in a finally block."""
    return await UiRequestInterceptor(
        page_or_context,
        policy,
        operation=operation,
        diagnostics=diagnostics,
        selected_file=selected_file,
        selected_file_id=selected_file_id,
        require_selection=require_selection,
    ).install()
