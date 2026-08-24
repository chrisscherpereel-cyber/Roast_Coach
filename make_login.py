"""
Make the accounts for Roast Coach.

    python3 make_login.py

Ask it for a name and a password, and it prints the line to paste into the app's
secrets. Run it once per person. The password itself is never written anywhere —
what is stored is a PBKDF2-SHA256 hash, which cannot be turned back into the
password, so the file is safe to keep in a password manager and the repository
stays publishable.

Where the secrets go:

* **Streamlit Community Cloud** — your app → ⋮ → Settings → Secrets → paste → Save
* **Running it yourself** — a file at `.streamlit/secrets.toml` beside `app.py`
  (already in `.gitignore`; never commit it)
"""

from getpass import getpass

from roastcoach.auth import hash_password


def main() -> None:
    print(__doc__.strip())
    print("\n" + "-" * 68)

    lines = []
    while True:
        name = input("\nName (blank when you are done): ").strip()
        if not name:
            break
        if " " in name:
            print("  Use one word — it is what they type to sign in.")
            continue
        password = getpass("Password: ")
        if len(password) < 8:
            print("  Eight characters at least, please.")
            continue
        if getpass("Again: ") != password:
            print("  Those did not match.")
            continue
        lines.append(f'{name} = "{hash_password(password)}"')
        print(f"  Added {name}.")

    if not lines:
        print("\nNothing to write.")
        return

    print("\n" + "=" * 68)
    print("Paste this into your secrets:\n")
    print("[passwords]")
    for line in lines:
        print(line)
    print("\n" + "=" * 68)
    print("If you are also using a shared database, keep its section too:\n")
    print("[database]")
    print('url = "postgresql://…"')


if __name__ == "__main__":
    main()
