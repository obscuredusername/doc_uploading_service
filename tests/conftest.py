"""
Test-time defaults applied BEFORE any app module is imported.

We force rate-limit storage to in-memory so the test suite does not require
a live Redis. pytest discovers conftest.py before test modules, and module
top-level code here runs before `from app.main import app` in any test.
"""
import os

os.environ.setdefault("RATE_LIMIT_STORAGE_URI", "memory://")
# Stop pydantic-settings from picking up the developer's local .env file.
os.environ.setdefault("APP_ENV", "test")
