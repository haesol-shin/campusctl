from __future__ import annotations

from campusctl import cli, composition


def test_registration_order_is_parser_order() -> None:
    usage = cli.build_parser().format_usage()

    assert "{config,doctor,auth,setup,sync,status,courses,lectures,assignments,materials,notices}" in usage


def test_capabilities_keep_published_key_order_and_values() -> None:
    assert composition.CAPABILITIES == {
        "sync": ["lectures", "assignments", "notices", "materials"],
        "lectures": ["list", "play"],
        "status": ["local"],
        "assignments": ["list", "fetch"],
        "materials": ["list", "download"],
        "notices": ["list", "fetch"],
    }
    assert list(composition.CAPABILITIES) == ["sync", "lectures", "status", "assignments", "materials", "notices"]
