# backend/app.py
# Wrapper to expose the FastAPI instance defined in main.py.
# This enables `uvicorn app:app --reload` without changing existing scripts.

from .main import app  # re-export the FastAPI application
