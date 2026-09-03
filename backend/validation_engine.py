"""
Module 1: Validation Engine
===========================
Intercepts all provisioning requests and validates `db_name` and
`db_unique_name` according to Oracle naming conventions before any
Docker execution is attempted.

Rules:
  db_name        – ≤8 chars, alphanumeric, no special chars, must NOT end in digit
  db_unique_name – ≤15 chars, alphanumeric + underscore only, must NOT end in digit
"""

import re
from dataclasses import dataclass
from typing import Literal


# ─────────────────────────── data transfer objects ───────────────────────────

@dataclass
class ProvisionRequest:
    db_name: str
    db_unique_name: str
    provisioning_type: Literal["seed", "clone"]
    character_set: str = "AL32UTF8"
    national_character_set: str = "AL16UTF16"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]


# ─────────────────────────────── validators ──────────────────────────────────

_ALLOWED_DB_NAME = re.compile(r"^[A-Za-z0-9]+$")
_ALLOWED_DB_UNIQUE = re.compile(r"^[A-Za-z0-9_]+$")


def _has_letter(value: str) -> bool:
    return any(c.isalpha() for c in value)


def _has_digit(value: str) -> bool:
    return any(c.isdigit() for c in value)


def validate_db_name(value: str) -> list[str]:
    """Return a list of violation strings; empty list means valid."""
    errors: list[str] = []

    if len(value) > 8:
        errors.append(
            f"db_name '{value}' exceeds 8 characters (length={len(value)})."
        )

    # Regex check and combination check are intentionally independent
    # so that both errors can be reported simultaneously.
    has_illegal = not _ALLOWED_DB_NAME.match(value)
    if has_illegal:
        errors.append(
            f"db_name '{value}' contains special characters. "
            "Only letters and digits are allowed."
        )

    # Strip non-alphanumeric chars before testing letter/digit mix so that
    # a value like "my!name" still gets the combination check.
    stripped = "".join(c for c in value if c.isalnum())
    if not (_has_letter(stripped) and _has_digit(stripped)):
        errors.append(
            f"db_name '{value}' must contain BOTH letters AND digits."
        )

    if value and value[-1].isdigit():
        errors.append(
            f"db_name '{value}' must NOT end with a digit."
        )

    return errors


def validate_db_unique_name(value: str) -> list[str]:
    """Return a list of violation strings; empty list means valid."""
    errors: list[str] = []

    if len(value) > 15:
        errors.append(
            f"db_unique_name '{value}' exceeds 15 characters (length={len(value)})."
        )

    has_illegal = not _ALLOWED_DB_UNIQUE.match(value)
    if has_illegal:
        errors.append(
            f"db_unique_name '{value}' contains illegal characters. "
            "Only letters, digits, and underscores are allowed."
        )

    # Strip underscores and illegal chars before testing letter/digit mix.
    stripped = "".join(c for c in value if c.isalnum())
    if not (_has_letter(stripped) and _has_digit(stripped)):
        errors.append(
            f"db_unique_name '{value}' must contain BOTH letters AND digits."
        )

    if value and value[-1].isdigit():
        errors.append(
            f"db_unique_name '{value}' must NOT end with a digit."
        )

    return errors


def validate_character_sets(
    character_set: str, national_character_set: str
) -> list[str]:
    errors: list[str] = []
    if character_set != "AL32UTF8":
        errors.append(
            f"character_set must be 'AL32UTF8'; got '{character_set}'."
        )
    if national_character_set != "AL16UTF16":
        errors.append(
            f"national_character_set must be 'AL16UTF16'; got '{national_character_set}'."
        )
    return errors


# ─────────────────────────── public entry point ──────────────────────────────

def validate_provision_request(req: ProvisionRequest) -> ValidationResult:
    """
    Run all validation rules against a ProvisionRequest.

    Returns a ValidationResult.  Callers should raise HTTP 400 when
    result.valid is False.
    """
    errors: list[str] = []
    errors.extend(validate_db_name(req.db_name))
    errors.extend(validate_db_unique_name(req.db_unique_name))
    errors.extend(
        validate_character_sets(req.character_set, req.national_character_set)
    )
    return ValidationResult(valid=len(errors) == 0, errors=errors)
