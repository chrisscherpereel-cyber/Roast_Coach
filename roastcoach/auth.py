"""
Who is allowed in.

The app is public — the repository is public and anyone can open the URL — but
the roasts behind it are not. Everything is gated on a sign-in, and the accounts
live in Streamlit secrets rather than in the code, so the repository can stay
open without giving anything away.

Passwords are never stored. What is stored is a PBKDF2-SHA256 hash of the
password with a per-account salt, which is what ``make_login.py`` prints:

    [passwords]
    chris = "pbkdf2_sha256$240000$9f3c…$1a2b…"

Add an account by adding a line. Remove one by deleting it. Anyone signed in can
import roasts and edit them, and their name is recorded against what they change.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from base64 import b64decode, b64encode

import streamlit as st

ALGORITHM = "pbkdf2_sha256"
ROUNDS = 240_000
SESSION_KEY = "roast_coach_user"
ATTEMPTS_KEY = "roast_coach_attempts"
MAX_ATTEMPTS = 6
LOCKOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: bytes | None = None, rounds: int = ROUNDS) -> str:
    """The string to paste into secrets for one account."""
    salt = salt or os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{ALGORITHM}${rounds}${b64encode(salt).decode()}${b64encode(derived).decode()}"


def verify(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash.

    A plain-text entry is accepted too, so a first run can work before anyone has
    generated hashes — :func:`weak_accounts` names those so the app can say so.
    """
    stored = str(stored)
    if not stored.startswith(ALGORITHM + "$"):
        return hmac.compare_digest(password, stored)
    try:
        _, rounds, salt, expected = stored.split("$", 3)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                        b64decode(salt), int(rounds))
        return hmac.compare_digest(b64encode(candidate).decode(), expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The accounts
# ---------------------------------------------------------------------------


def accounts() -> dict:
    """``{name: stored hash}`` from secrets, or from the environment for tests."""
    from_env = os.environ.get("ROAST_COACH_PASSWORDS")
    if from_env:
        pairs = [entry.split(":", 1) for entry in from_env.split(",") if ":" in entry]
        return {name.strip(): value.strip() for name, value in pairs}
    try:
        return {str(name): str(value) for name, value in dict(st.secrets["passwords"]).items()}
    except Exception:
        return {}


def configured() -> bool:
    return bool(accounts())


def weak_accounts() -> list[str]:
    """Accounts whose password is sitting in secrets in the clear."""
    return sorted(name for name, stored in accounts().items()
                  if not str(stored).startswith(ALGORITHM + "$"))


def current_user() -> str | None:
    return st.session_state.get(SESSION_KEY)


def sign_out() -> None:
    st.session_state.pop(SESSION_KEY, None)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _locked_for() -> int:
    """Seconds left in a lockout, after too many wrong passwords."""
    count, since = st.session_state.get(ATTEMPTS_KEY, (0, 0.0))
    if count < MAX_ATTEMPTS:
        return 0
    remaining = int(LOCKOUT_SECONDS - (time.time() - since))
    if remaining <= 0:
        st.session_state[ATTEMPTS_KEY] = (0, 0.0)
        return 0
    return remaining


def _record_failure() -> None:
    count, since = st.session_state.get(ATTEMPTS_KEY, (0, 0.0))
    st.session_state[ATTEMPTS_KEY] = (count + 1, time.time() if count + 1 >= MAX_ATTEMPTS else since)


def sign_in_form(logo: str | None = None) -> None:
    """The whole page, when nobody is signed in."""
    left, middle, right = st.columns([1, 1.15, 1])
    with middle:
        st.write("")
        st.write("")
        if logo:
            st.markdown(
                f'<div style="display:flex;justify-content:center;margin-bottom:10px">'
                f'<img src="{logo}" width="64" height="64" alt=""></div>',
                unsafe_allow_html=True)
        st.markdown(
            '<div style="text-align:center;font-size:1.6rem;font-weight:650;'
            'letter-spacing:-.02em">Roast <span style="color:#E8622A">Coach</span></div>'
            '<div style="text-align:center;opacity:.65;margin-bottom:18px">'
            'Sign in to reach your roasts.</div>',
            unsafe_allow_html=True)

        if not configured():
            st.error("No accounts are set up yet. Add a `[passwords]` section to this app's "
                     "secrets — running `python3 make_login.py` prints the lines to paste.")
            st.code('[passwords]\nchris = "pbkdf2_sha256$240000$…"', language="toml")
            # A database only this computer can see has nothing to protect from
            # anyone else, so local work is not held up by unfinished setup.
            from . import db

            if not db.is_shared():
                st.caption("This app is using a database on this computer only.")
                if st.button("Continue without signing in", use_container_width=True):
                    st.session_state[SESSION_KEY] = "local"
                    st.rerun()
            return

        locked = _locked_for()
        with st.form("sign_in"):
            name = st.text_input("Name", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary",
                                              use_container_width=True, disabled=bool(locked))

        if locked:
            st.warning(f"Too many attempts. Try again in {locked} seconds.")
            return

        if submitted:
            stored = accounts().get(name.strip())
            if stored and verify(password, stored):
                st.session_state[SESSION_KEY] = name.strip()
                st.session_state[ATTEMPTS_KEY] = (0, 0.0)
                st.rerun()
            else:
                _record_failure()
                st.error("That name and password do not match an account.")


def require(logo: str | None = None) -> str:
    """Let a signed-in roaster through, or show the sign-in page and stop.

    Called once at the top of the app, before any page runs, so nothing behind
    it — not the data, not the pages, not the sidebar — is ever rendered to
    someone who has not signed in.
    """
    user = current_user()
    if user:
        return user
    sign_in_form(logo)
    st.stop()
