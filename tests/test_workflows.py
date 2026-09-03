"""
Unit tests for Workflow Safety Checks (Part 3).
"""

from backend.workflows import validate_pre_delete_path, STAGING_DIR


def test_valid_pre_delete_path():
    valid_path = f"{STAGING_DIR}/MYDB1A"
    assert validate_pre_delete_path(valid_path, "mydb1a") is True


def test_invalid_pre_delete_path_wrong_root():
    invalid_path = "/var/lib/oracle/oradata/MYDB1A"
    assert validate_pre_delete_path(invalid_path, "mydb1a") is False


def test_invalid_pre_delete_path_missing_dbname():
    invalid_path = f"{STAGING_DIR}/OTHERDB"
    assert validate_pre_delete_path(invalid_path, "mydb1a") is False


def test_invalid_pre_delete_path_root_directory():
    invalid_path = "/"
    assert validate_pre_delete_path(invalid_path, "mydb1a") is False
