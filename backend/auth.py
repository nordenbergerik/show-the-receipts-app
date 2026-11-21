"""Platshållare för inloggning.

Demon har ingen inloggning: alla anrop körs som en demoläkare. När inloggning
ska in byts get_current_user ut (t.ex. mot en session eller en OAuth-token) och
endpoints som ska skyddas får beroendet; resten av koden behöver inte ändras.
"""
from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    id: str
    name: str
    role: str
    authenticated: bool


DEMO_USER = User(id="demo", name="Demo clinician", role="clinician", authenticated=False)


def get_current_user() -> User:
    return DEMO_USER
