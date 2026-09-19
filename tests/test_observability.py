"""Homework 2 authentication tests. Offline: no Langfuse, Docker, or model key."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from server import app as server_app


def test_create_session_rejects_claimed_role_that_is_not_the_stored_role(world) -> None:
    """User 9002 is a merchant in the database, so a support claim must fail."""
    server_app._SESSIONS.clear()

    with pytest.raises(HTTPException) as excinfo:
        # The role comes from the request; the database disagrees with it.
        server_app.create_session(server_app.SessionCreate(user_id=9002, role="support"))

    assert excinfo.value.status_code == 403
    # A rejected request must not leave a usable session behind.
    assert server_app._SESSIONS == {}


def test_token_for_one_session_does_not_authorize_another(world) -> None:
    """A validly signed token is still refused against a different session id."""
    server_app._SESSIONS.clear()

    # Two separate sessions, both for real shoppers in the seeded world.
    first = server_app.create_session(server_app.SessionCreate(user_id=1, role="shopper"))
    second = server_app.create_session(server_app.SessionCreate(user_id=2, role="shopper"))
    assert first["session_id"] != second["session_id"]

    # The signature is genuine, so this is a binding failure, not a forgery.
    assert server_app.verify_token(first["token"]) is not None

    with pytest.raises(HTTPException) as excinfo:
        # First session's token presented against the second session's id.
        server_app._authorize(second["session_id"], f"Bearer {first['token']}")

    assert excinfo.value.status_code == 403

    # The token still works for the session it was actually issued for.
    ctx = server_app._authorize(first["session_id"], f"Bearer {first['token']}")
    assert ctx.user_id == 1
    assert ctx.role == "shopper"
