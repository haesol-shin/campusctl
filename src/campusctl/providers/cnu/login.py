from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

from campusctl.browser import (
    _SESSION_LABELS,
    CLEANUP_TIMEOUT_SECONDS,
    PROTOCOL_TIMEOUT_SECONDS,
    current_profile,
    profile_labels,
    profile_span,
)
from campusctl.credentials import get_credentials
from campusctl.envelope import CampusError
from campusctl.wait_clock import current_clock

from .courses import COURSE_LINK_SELECTOR

MY_LECTURE_URL = "https://dcs-learning.cnu.ac.kr/std/myLecture"
LOGIN_FORM_SELECTOR = 'input[name="user_id"]'
LOGIN_PASSWORD_SELECTOR = 'input[name="user_password"]'
LOGIN_BUTTON_SELECTOR = 'button[data-act="clickLogin"]'


def _target_kind(url: str) -> str:
    return {
        "/std/myLecture": "roster",
        "/std/todo": "todo",
        "/std/lecture": "course-entry",
        "/std/course": "lecture",
        "/std/task": "assignments",
        "/std/notice": "notices",
        "/std/archive": "archive",
        "/user/login": "login",
    }.get(urlsplit(url).path, "other")


@contextmanager
def _timed(phase: str, wait_kind: str, page_kind: str, recorder: Any = None, labels: Any = None):
    if recorder is None:
        with profile_span(phase, wait_kind=wait_kind, page_kind=page_kind):
            yield
        return
    document = None if labels is None else labels.active_document
    with recorder.span(phase, wait_kind=wait_kind, page_kind=page_kind, document=document):
        yield


def _is_login_url(url: str) -> bool:
    return "apiCnuAuthLogin" in url or "/user/login" in url


def _unavailable() -> CampusError:
    return CampusError(
        "lms-unavailable",
        "The LMS could not be reached or did not complete login in time.",
        "Check the LMS session and network connection, then retry.",
        "error",
    )


def _login_failed() -> CampusError:
    return CampusError(
        "login-failed",
        "The LMS rejected the configured credentials.",
        "Run 'campusctl auth status --check' to check the stored account credentials, correct them if needed, then retry.",
        "user-action",
    )


def _login_action_required() -> CampusError:
    return CampusError(
        "login-action-required",
        "The LMS requires acceptance of the terms before course discovery can continue.",
        "Log in once in a normal browser and accept the terms, then retry.",
        "user-action",
    )


def _password_change_required() -> CampusError:
    return CampusError(
        "login-action-required",
        "The LMS requires a password change before course discovery can continue.",
        "Change or confirm the password in a normal browser, then update the stored credentials and retry.",
        "user-action",
    )


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - current_clock().now())


async def ensure_logged_in(
    page: Any,
    config: dict,
    *,
    target_url: str = MY_LECTURE_URL,
    expected_selector: str = COURSE_LINK_SELECTOR,
    timeout_ms: int = 30000,
) -> None:
    timeout_seconds = max(timeout_ms / 1000, 0.001)

    landing_kind = _target_kind(target_url)
    with profile_labels(domain=None, course=None, page_kind="login"):
        try:
            with _timed("document-commit", "navigation", landing_kind):
                await current_clock().wait_for(
                    page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms),
                    timeout_seconds,
                )
            with _timed("dom-ready", "selector", "login"):
                await current_clock().wait_for(
                    page.wait_for_selector(f"{expected_selector}, {LOGIN_FORM_SELECTOR}", timeout=timeout_ms),
                    timeout_seconds,
                )
            with _timed("wait", "selector", landing_kind):
                landing_visible = await current_clock().wait_for(
                    page.is_visible(expected_selector), PROTOCOL_TIMEOUT_SECONDS
                )
            if landing_visible:
                form_visible = False
            else:
                with _timed("wait", "selector", "login"):
                    form_visible = await current_clock().wait_for(
                        page.is_visible(LOGIN_FORM_SELECTOR), PROTOCOL_TIMEOUT_SECONDS
                    )
        except Exception:
            raise _unavailable() from None
        if landing_visible:
            return
        if not form_visible:
            raise _unavailable()

    # Credential-provider failures are part of the provider's public contract and
    # must pass through unchanged. This call happens only after the login form is
    # confirmed visible; the already-authenticated path never reads credentials.
    username, password = get_credentials(config)
    fill_error: CampusError | None = None
    try:
        with _timed("wait", "function", "login"):
            await current_clock().wait_for(
                page.evaluate("""() => {
                    const univ = document.querySelector('input[name="univ_no"]');
                    if (univ) univ.value = 'CNU';
                    const cnuAuth = document.querySelector('#cnu_auth');
                    if (cnuAuth) cnuAuth.checked = true;
                }"""),
                PROTOCOL_TIMEOUT_SECONDS,
            )
        with _timed("wait", "action", "login"):
            await current_clock().wait_for(page.fill(LOGIN_FORM_SELECTOR, username), PROTOCOL_TIMEOUT_SECONDS)
        with _timed("wait", "action", "login"):
            await current_clock().wait_for(page.fill(LOGIN_PASSWORD_SELECTOR, password), PROTOCOL_TIMEOUT_SECONDS)
    except Exception:
        fill_error = _unavailable()
    finally:
        username = ""
        password = ""
    if fill_error is not None:
        raise fill_error

    loop = asyncio.get_running_loop()
    login_result: asyncio.Future[tuple[dict[str, Any] | None, CampusError | None]] = loop.create_future()
    response_deadline: float | None = None
    handler_tasks: set[asyncio.Task[Any]] = set()
    bound = current_profile()
    labels = _SESSION_LABELS.get()

    def settle(data: dict[str, Any] | None, error: CampusError | None) -> None:
        if not login_result.done():
            login_result.set_result((data, error))

    async def abort_route(route: Any) -> None:
        with suppress(BaseException), _timed("wait", "timeout", "login", bound, labels):
            await current_clock().wait_for(route.abort(), CLEANUP_TIMEOUT_SECONDS)

    async def capture_login_response(route: Any) -> None:
        handler_task = asyncio.current_task()
        if handler_task is not None:
            handler_tasks.add(handler_task)
        try:
            deadline = response_deadline
            if deadline is None:
                raise TimeoutError
            with _timed("response-completion", "response", "login", bound, labels):
                response = await current_clock().wait_for(route.fetch(), _remaining(deadline))
                body = await current_clock().wait_for(response.body(), _remaining(deadline))
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError
            with _timed("wait", "action", "login", bound, labels):
                await current_clock().wait_for(
                    route.fulfill(response=response),
                    PROTOCOL_TIMEOUT_SECONDS,
                )
        except asyncio.CancelledError:
            settle(None, _unavailable())
            await abort_route(route)
            raise
        except Exception:
            await abort_route(route)
            settle(None, _unavailable())
        else:
            settle(data, None)
        finally:
            if handler_task is not None:
                handler_tasks.discard(handler_task)

    async def drain_route_handlers() -> None:
        tasks = tuple(handler_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if not tasks:
            return
        with _timed("wait", "timeout", "login"):
            try:
                done, pending = await current_clock().wait_for(asyncio.wait(tasks), CLEANUP_TIMEOUT_SECONDS)
            except TimeoutError:
                done = {task for task in tasks if task.done()}
                pending = set(tasks) - done
        for task in done:
            with suppress(BaseException):
                task.result()
        for task in pending:
            task.cancel()

    try:
        try:
            with _timed("wait", "action", "login"):
                await current_clock().wait_for(
                    page.route(_is_login_url, capture_login_response),
                    PROTOCOL_TIMEOUT_SECONDS,
                )
        except Exception:
            settle(None, _unavailable())
            raise _unavailable() from None

        response_deadline = current_clock().now() + timeout_seconds
        click_failed = False
        try:
            with _timed("wait", "action", "login"):
                await current_clock().wait_for(
                    page.click(LOGIN_BUTTON_SELECTOR),
                    _remaining(response_deadline),
                )
        except Exception:
            click_failed = True

        if login_result.done():
            data, response_error = login_result.result()
        elif click_failed:
            raise _unavailable()
        else:
            try:
                with _timed("response-completion", "response", "login"):
                    data, response_error = await current_clock().wait_for(
                        asyncio.shield(login_result),
                        _remaining(response_deadline),
                    )
            except Exception:
                raise _unavailable() from None

        if response_error is not None:
            raise response_error
        if data is None:
            raise _unavailable()

        header = data.get("header")
        if not isinstance(header, dict):
            raise _unavailable()
        if header.get("code") not in (200, "200"):
            raise _login_failed()

        ret_code = str(data.get("ret_code"))
        if ret_code == "0":
            raise _login_action_required()
        if ret_code == "-2":
            raise _password_change_required()
        if click_failed:
            raise _unavailable()
    finally:
        if not login_result.done():
            settle(None, _unavailable())
        try:
            with suppress(Exception), _timed("wait", "timeout", "login"):
                await current_clock().wait_for(
                    page.unroute(_is_login_url, capture_login_response),
                    CLEANUP_TIMEOUT_SECONDS,
                )
        finally:
            await drain_route_handlers()

    try:
        with _timed("dom-ready", "selector", landing_kind):
            await current_clock().wait_for(
                page.wait_for_selector(expected_selector, timeout=timeout_ms),
                timeout_seconds,
            )
    except Exception:
        raise _unavailable() from None
