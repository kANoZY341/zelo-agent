"""ASGI entry point; reusable explicit configuration lives in app.application."""

from app.application import create_app

app = create_app()
