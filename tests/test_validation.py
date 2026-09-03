"""
Unit tests for the Validation Engine (Module 1).
Run with:  pytest tests/test_validation.py -v
"""

import pytest
from backend.validation_engine import (
    ProvisionRequest,
    validate_db_name,
    validate_db_unique_name,
    validate_provision_request,
)


# ─────────────────────────── db_name tests ───────────────────────────────────

class TestDbName:

    def test_valid(self):
        assert validate_db_name("mydb1a") == []

    def test_too_long(self):
        errs = validate_db_name("toolongname")
        assert any("8 characters" in e for e in errs)

    def test_special_char(self):
        errs = validate_db_name("db-name1")
        assert any("special" in e for e in errs)

    def test_no_digit(self):
        errs = validate_db_name("mydbname")
        assert any("BOTH letters AND digits" in e for e in errs)

    def test_no_letter(self):
        errs = validate_db_name("12345678")
        assert any("BOTH letters AND digits" in e for e in errs)

    def test_ends_in_digit(self):
        errs = validate_db_name("mydb1")
        assert any("NOT end with a digit" in e for e in errs)

    def test_exactly_8_chars_valid(self):
        # 8 chars, has letter+digit, doesn't end in digit
        assert validate_db_name("ab1cde2f") == []  # ends in 'f'

    def test_underscore_not_allowed(self):
        errs = validate_db_name("my_db1a")
        assert any("special" in e for e in errs)


# ─────────────────────────── db_unique_name tests ────────────────────────────

class TestDbUniqueName:

    def test_valid(self):
        assert validate_db_unique_name("mydb1a_site1a") == []

    def test_too_long(self):
        errs = validate_db_unique_name("a" * 16)
        assert any("15 characters" in e for e in errs)

    def test_dash_not_allowed(self):
        errs = validate_db_unique_name("mydb1-site")
        assert any("illegal" in e for e in errs)

    def test_no_digit(self):
        errs = validate_db_unique_name("mydb_site")
        assert any("BOTH letters AND digits" in e for e in errs)

    def test_ends_in_digit(self):
        errs = validate_db_unique_name("mydb1_site2")
        assert any("NOT end with a digit" in e for e in errs)

    def test_underscore_allowed(self):
        assert validate_db_unique_name("db1a_prod") == []

    def test_exactly_15_chars(self):
        # exactly 15 chars: "ab1cd_ef2gh3ija" → ends in 'a'
        assert validate_db_unique_name("ab1cd_ef2gh3ija") == []


# ─────────────────────────── full request tests ──────────────────────────────

class TestValidateProvisionRequest:

    def _make(self, db_name="mydb1a", db_unique_name="mydb1a_sitea",
              char_set="AL32UTF8", nat_char="AL16UTF16", ptype="seed"):
        return ProvisionRequest(
            db_name=db_name,
            db_unique_name=db_unique_name,
            provisioning_type=ptype,
            character_set=char_set,
            national_character_set=nat_char,
        )

    def test_all_valid(self):
        result = validate_provision_request(self._make())
        assert result.valid is True
        assert result.errors == []

    def test_bad_db_name_fails(self):
        result = validate_provision_request(self._make(db_name="bad!name"))
        assert result.valid is False
        assert len(result.errors) >= 1

    def test_bad_char_set_fails(self):
        result = validate_provision_request(self._make(char_set="UTF8"))
        assert result.valid is False
        assert any("AL32UTF8" in e for e in result.errors)

    def test_bad_nat_char_set_fails(self):
        result = validate_provision_request(self._make(nat_char="UTF16"))
        assert result.valid is False
        assert any("AL16UTF16" in e for e in result.errors)

    def test_multiple_errors_accumulated(self):
        result = validate_provision_request(
            self._make(db_name="toolongname!!", db_unique_name="ends_in_2")
        )
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_clone_standby_flag_rejected(self):
        req = ProvisionRequest(
            db_name="mydb1a",
            db_unique_name="mydb1a_sitea",
            provisioning_type="clone",
            is_standby=True
        )
        result = validate_provision_request(req)
        assert result.valid is False
        assert any("standby database or enable Data Guard" in e for e in result.errors)

    def test_seed_standby_flag_allowed(self):
        req = ProvisionRequest(
            db_name="mydb1a",
            db_unique_name="mydb1a_sitea",
            provisioning_type="seed",
            is_standby=True
        )
        result = validate_provision_request(req)
        assert result.valid is True
