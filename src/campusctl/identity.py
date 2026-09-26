from __future__ import annotations


def _component(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.replace("%", "%25").replace(":", "%3A")


def assignment_entity_id(course_id: str, task_id: str) -> str:
    """Return the stable full identity for an assignment."""
    return f"cnu_assignment:{_component(course_id, 'course_id')}:{_component(task_id, 'task_id')}"


def notice_entity_id(course_id: str, displayed_date_time: str, number: str) -> str:
    """Return the stable full identity for a notice without normalizing display fields."""
    return (
        f"cnu_notice:{_component(course_id, 'course_id')}:{_component(displayed_date_time, 'displayed_date_time')}"
        f":{_component(number, 'number')}"
    )


def material_entity_id(course_id: str, file_id: str) -> str:
    """Return the stable full identity for a course material."""
    return f"cnu_lms_material:{_component(course_id, 'course_id')}:{_component(file_id, 'file_id')}"
