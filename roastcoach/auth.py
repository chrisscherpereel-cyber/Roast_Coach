"""
Who is allowed in.

The app is public — the repository is public and anyone can open the URL — but
the roasts behind it are not. Everything is gated on a sign-in.

Accounts come from two places, and the difference between them matters.

**Streamlit secrets** hold the founding account. Secrets cannot be written from
inside the app — on Streamlit Cloud they are read-only, and that is the point of
them — so an account there is the one nobody can lock themselves out of and
nobody inside the app can touch. Anyone in secrets is an admin::

    [passwords]
    admin = "pbkdf2_sha256$240000$9f3c…$1a2b…"

**The database** holds everybody else. An admin adds a person on the People tab
and the account lands here, so the admin can give somebody a login without going
near the app's settings. The first time an admin signs in from secrets, a
matching ``admin`` row is written to the database carrying the same hash, so the
name on the door becomes ``admin`` whatever the secrets line happens to say.

Passwords are never stored anywhere. What is stored is a PBKDF2-SHA256 hash with
a per-account salt, which is what ``make_login.py`` prints. A hash gives nothing
away: it cannot be turned back into the password.

Two roles. An **admin** can add, suspend and remove people and reset passwords.
A **roaster** can do everything else the app does — import, edit, plan, grade.
There is deliberately no read-only role: everyone signed in is trusted with the
roasts, and their name is recorded against what they change.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from base64 import b64decode, b64encode
from datetime import datetime, timezone

import streamlit as st

ALGORITHM = "pbkdf2_sha256"
ROUNDS = 240_000
SESSION_KEY = "roast_coach_user"
ATTEMPTS_KEY = "roast_coach_attempts"
MAX_ATTEMPTS = 6
LOCKOUT_SECONDS = 60

# What this file can do — see the note in store.py.
#   1  accounts in Streamlit secrets
#   2  accounts in the database too, with roles, added by an admin from inside
#      the app; the founding secrets account is seeded as `admin`
VERSION = 2

# The name the founding account is given in the database. Everything else about
# an account can change; this one name is what the app looks for when it asks
# whether anybody is in charge yet.
ADMIN = os.environ.get("ROAST_COACH_ADMIN", "admin")

ROLES = ("admin", "roaster")
MIN_PASSWORD = 8


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


def founders() -> dict:
    """``{name: stored hash}`` from secrets, or from the environment for tests.

    These are the accounts the app cannot edit, and everyone in here is an admin.
    """
    from_env = os.environ.get("ROAST_COACH_PASSWORDS")
    if from_env:
        pairs = [entry.split(":", 1) for entry in from_env.split(",") if ":" in entry]
        return {name.strip(): value.strip() for name, value in pairs}
    try:
        return {str(name): str(value) for name, value in dict(st.secrets["passwords"]).items()}
    except Exception:
        return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def people(include_suspended: bool = True) -> list[dict]:
    """Every account in the database, newest names last.

    A database that cannot be reached is not a reason to refuse everybody at the
    door: the founding accounts still work, so this returns nothing and says
    nothing rather than raising.
    """
    from . import db

    try:
        rows = db.frame("SELECT username, password, role, display_name, active, "
                        "created_at, created_by, changed_at, last_seen "
                        "FROM accounts ORDER BY username")
    except Exception:
        return []
    found = rows.to_dict("records") if rows is not None and not rows.empty else []
    if include_suspended:
        return found
    return [row for row in found if int(row.get("active") or 0)]


def stored_accounts() -> dict:
    """``{name: row}`` for the accounts the app itself keeps."""
    return {str(row["username"]): row for row in people()}


def accounts() -> dict:
    """``{name: stored hash}`` for everybody who may sign in.

    A database row of the same name wins over secrets, so an admin who changes
    their own password inside the app is not overruled by an old secrets line.
    Suspended accounts are absent, which is what suspension means.
    """
    merged = dict(founders())
    for name, row in stored_accounts().items():
        if int(row.get("active") or 0):
            merged[name] = str(row.get("password") or "")
        else:
            merged.pop(name, None)
    return {name: value for name, value in merged.items() if value}


def role_of(name: str | None) -> str:
    """``admin`` or ``roaster``. Anybody in secrets is an admin."""
    if not name:
        return ""
    clean = str(name).strip()
    row = stored_accounts().get(clean)
    if row and int(row.get("active") or 0):
        return str(row.get("role") or "roaster")
    if clean in founders() or clean == "local":
        return "admin"
    return "roaster"


def is_admin(name: str | None = None) -> bool:
    return role_of(name if name is not None else current_user()) == "admin"


def configured() -> bool:
    return bool(accounts())


def weak_accounts() -> list[str]:
    """Accounts whose password is sitting somewhere in the clear."""
    return sorted(name for name, stored in accounts().items()
                  if not str(stored).startswith(ALGORITHM + "$"))


def current_user() -> str | None:
    return st.session_state.get(SESSION_KEY)


def sign_out() -> None:
    st.session_state.pop(SESSION_KEY, None)


# ---------------------------------------------------------------------------
# Managing people
# ---------------------------------------------------------------------------


def trouble_with(password: str, again: str | None = None) -> str:
    """Why a password will not do, or an empty string if it will."""
    if not password:
        return "A password, please."
    if again is not None and password != again:
        return "Those two passwords are not the same."
    if len(password) < MIN_PASSWORD:
        return (f"{MIN_PASSWORD} characters or more — this is the only thing between "
                "the internet and your roasts.")
    return ""


def add_account(name: str, password: str, role: str = "roaster",
                display_name: str = "", by: str = "") -> str:
    """Make an account. Returns an empty string, or why it could not be made."""
    from . import db

    clean = str(name).strip()
    if not clean:
        return "A name to sign in with, please."
    if any(character.isspace() for character in clean):
        return "No spaces in a sign-in name — use the full name field for that."
    if clean in accounts() or clean in stored_accounts():
        return f"There is already an account called {clean}."
    wrong = trouble_with(password)
    if wrong:
        return wrong
    db.upsert("accounts", "username", {
        "username": clean, "password": hash_password(password),
        "role": role if role in ROLES else "roaster",
        "display_name": str(display_name or "").strip() or None,
        "active": 1, "created_at": _now(), "created_by": by or None,
        "changed_at": _now(), "last_seen": None})
    return ""


def set_password(name: str, password: str, by: str = "") -> str:
    """Change somebody's password, including your own."""
    from . import db

    clean = str(name).strip()
    wrong = trouble_with(password)
    if wrong:
        return wrong
    if clean not in stored_accounts():
        # An account that only exists in secrets cannot be edited from here, so
        # give it a database row of its own carrying the new password.
        if clean not in founders():
            return f"There is no account called {clean}."
        db.upsert("accounts", "username", {
            "username": clean, "password": hash_password(password), "role": "admin",
            "display_name": None, "active": 1, "created_at": _now(),
            "created_by": by or None, "changed_at": _now(), "last_seen": None})
        return ""
    db.run("UPDATE accounts SET password = :password, changed_at = :when "
           "WHERE username = :username",
           {"password": hash_password(password), "when": _now(), "username": clean})
    return ""


def set_role(name: str, role: str) -> str:
    from . import db

    clean = str(name).strip()
    if role not in ROLES:
        return "That is not a role this app has."
    if clean not in stored_accounts():
        return "That account lives in the app's settings and cannot be changed here."
    if role != "admin" and _last_admin(clean):
        return "That is the only admin left — make somebody else an admin first."
    db.run("UPDATE accounts SET role = :role, changed_at = :when WHERE username = :username",
           {"role": role, "when": _now(), "username": clean})
    return ""


def set_active(name: str, active: bool) -> str:
    """Suspend or restore an account without losing what it has recorded."""
    from . import db

    clean = str(name).strip()
    if clean not in stored_accounts():
        return "That account lives in the app's settings and cannot be changed here."
    if not active and _last_admin(clean):
        return "That is the only admin left — make somebody else an admin first."
    db.run("UPDATE accounts SET active = :active, changed_at = :when "
           "WHERE username = :username",
           {"active": 1 if active else 0, "when": _now(), "username": clean})
    return ""


def remove_account(name: str) -> str:
    """Delete an account. What it recorded stays; its name stays on that record."""
    from . import db

    clean = str(name).strip()
    if clean not in stored_accounts():
        return "That account lives in the app's settings and cannot be removed here."
    if _last_admin(clean):
        return "That is the only admin left — make somebody else an admin first."
    db.run("DELETE FROM accounts WHERE username = :username", {"username": clean})
    return ""


def _last_admin(name: str) -> bool:
    """True when losing this account's admin would leave nobody in charge.

    An account in secrets always counts, because that one cannot be taken away
    from inside the app — it is the way back in when everything else is wrong.
    """
    if founders():
        return False
    others = [account for account, row in stored_accounts().items()
              if account != name and int(row.get("active") or 0)
              and str(row.get("role")) == "admin"]
    return not others


def seen(name: str) -> None:
    """Note that somebody signed in, quietly — this must never block a sign-in."""
    from . import db

    try:
        if str(name).strip() in stored_accounts():
            db.run("UPDATE accounts SET last_seen = :when WHERE username = :username",
                   {"when": _now(), "username": str(name).strip()})
    except Exception:
        pass


def seed_admin(from_name: str) -> bool:
    """Give the founding secrets account a database row called ``admin``.

    The account in secrets was named by whoever first set the app up, and the
    app cannot rename it — secrets are read-only from in here. What it can do is
    write an ``admin`` row carrying the same password hash, so from the next
    sign-in the name on the door is ``admin`` and the password has not changed.
    The old name keeps working until its line is deleted from secrets, which is
    the safe order to do this in.
    """
    from . import db

    stored = founders().get(str(from_name).strip())
    if not stored or ADMIN in stored_accounts():
        return False
    try:
        db.upsert("accounts", "username", {
            "username": ADMIN, "password": str(stored), "role": "admin",
            "display_name": None, "active": 1, "created_at": _now(),
            "created_by": str(from_name).strip(), "changed_at": _now(),
            "last_seen": None})
    except Exception:
        return False
    return True


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


def where_secrets_live() -> str:
    """Where to paste an account line, said for wherever this app is running."""
    if os.environ.get("HOSTNAME", "").startswith("streamlit") or "/mount/src" in os.getcwd():
        return ("**On Streamlit Cloud:** open the app's menu (top right) → **Settings** → "
                "**Secrets**, paste, and **Save**. The app restarts by itself.")
    return ("**On this computer:** put it in `.streamlit/secrets.toml` beside `app.py` "
            "(create the folder if it is not there), then restart the app.")


def first_account() -> None:
    """What to show when nobody has set up an account yet.

    Secrets cannot be written from inside the app — on Streamlit Cloud they are
    read-only, and that is the point of them. But the tedious part is making the
    hash, and the app can do that here rather than sending someone to a terminal
    they may not have. Making a hash gives nothing away: it is only worth
    anything once it is pasted into secrets, which needs the app's own settings.
    """
    st.error("No accounts are set up yet, so there is nothing to check a password against. "
             "Make the first one here — it takes about a minute.")

    with st.form("first_account"):
        name = st.text_input("Name to sign in with", placeholder="admin")
        password = st.text_input("Password", type="password")
        again = st.text_input("Password again", type="password")
        made = st.form_submit_button("Make the line to paste", type="primary",
                                     use_container_width=True)

    if made:
        clean = name.strip()
        if not clean or not password:
            st.warning("A name and a password, please.")
        elif password != again:
            st.warning("Those two passwords are not the same.")
        elif len(password) < 8:
            st.warning("Eight characters or more — this is the only thing between the "
                       "internet and your roasts.")
        else:
            st.success("Copy these two lines into the app's secrets.")
            st.code(f'[passwords]\n{clean} = "{hash_password(password)}"', language="toml")
            st.markdown(where_secrets_live())
            st.caption("The password itself is not in that line and cannot be worked "
                       "back out of it. Sign in with the name and password you just "
                       "chose — you will be an admin, and everybody after you can be "
                       "added from inside the app on **Setup → People**, with no more "
                       "editing of settings.")

    with st.expander("Other ways to make the line"):
        st.markdown(
            "- **`password_tool.html`** — open it in any browser, no install, nothing "
            "leaves the page.\n"
            "- **`python3 make_login.py`** — the same thing in a terminal.\n\n"
            "All three produce the same kind of line; use whichever is at hand.")

    # A database only this computer can see has nothing to protect from anyone
    # else, so local work is not held up by unfinished setup.
    from . import db

    if not db.is_shared():
        st.caption("This app is using a database on this computer only, so there is "
                   "nothing here anyone else could reach.")
        if st.button("Continue without signing in", use_container_width=True):
            st.session_state[SESSION_KEY] = "local"
            st.rerun()


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
            first_account()
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
            who = name.strip()
            stored = accounts().get(who)
            if stored and verify(password, stored):
                # The founding account gets an `admin` row on its way in, so the
                # name on the door becomes `admin` without anybody editing
                # secrets first. Same password; nothing to remember.
                if who in founders():
                    seed_admin(who)
                st.session_state[SESSION_KEY] = who
                st.session_state[ATTEMPTS_KEY] = (0, 0.0)
                seen(who)
                st.rerun()
            elif who and who in {account for account, row in stored_accounts().items()
                                 if not int(row.get("active") or 0)}:
                st.error("That account has been suspended. Ask an admin to turn it "
                         "back on.")
            else:
                _record_failure()
                st.error("That name and password do not match an account.")

        st.caption("No account? Somebody with an admin sign-in can make you one on the "
                   "app's **Setup → People** tab.")


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
