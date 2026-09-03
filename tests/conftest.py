"""
conftest.py – ensures the workspace root is on sys.path so that
`oracle_provisioner` is importable without a pip install.
"""
import sys
import os

# Add the workspace root (parent of oracle_provisioner/) to sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
