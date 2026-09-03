"""
conftest.py – ensures the workspace root is on sys.path and sets test environment variables.
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Set required environment variables for test execution
os.environ.setdefault("PROVISIONING_API_KEY", "dev-secret-key-123")
os.environ.setdefault("ORACLE_PASSWORD", "Oracle_4U")
os.environ.setdefault("DB_SYS_PASSWORD", "Oracle_4U")
os.environ.setdefault("DB_SYSTEM_PASSWORD", "Oracle_4U")
os.environ.setdefault("DB_DBSNMP_PASSWORD", "Oracle_4U")
