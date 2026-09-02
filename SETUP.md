# Setting up the shared version

Two things turn Roast Coach from a single-computer app into one your group shares:
a database everybody's copy talks to, and a sign-in so nobody else can.

Both are configured entirely in **secrets**, never in the code — so the repository
and the Streamlit app can stay public without exposing anything.

Fifteen minutes, once. Getting roasts *in* needs none of this — see the README for the
watched folder — but a group sharing one set of roasts needs both of the below.

---

## 1. A database everyone can reach

Streamlit Community Cloud gives your app a container it wipes on every restart.
Anything written to a file there is gone — that includes a SQLite database. For
roasts to survive a restart, and for two computers to see the same ones, the data
has to live somewhere else.

Supabase gives away a Postgres database that is plenty for this: 500 MB, which at
roughly 20 KB per roast is tens of thousands of roasts.

1. Sign up at [supabase.com](https://supabase.com) and **New project**.
2. Give it a name and a **database password**. Copy that password somewhere safe
   now — Supabase will not show it again.
3. Pick the region closest to you. Wait a minute or two while it builds.
4. Press **Connect** at the top of the project dashboard.
5. Find **Session pooler** and copy that URI. It looks like:

   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-1-us-east-2.pooler.supabase.com:5432/postgres
   ```

6. Replace `[YOUR-PASSWORD]` with the password from step 2.

> **Take the Session pooler, not the Direct connection.** Supabase's direct
> connection is IPv6-only unless you pay for the IPv4 add-on, and Streamlit
> Community Cloud cannot make IPv6 connections — psycopg2 fails with *"Cannot
> assign requested address"*, which tells you nothing about the real cause. The
> session pooler is IPv4 on every tier and behaves like an ordinary Postgres
> connection. If your password contains `@`, `/`, `:` or `#`, percent-encode it
> (`@` → `%40`) or change it to something alphanumeric.

Roast Coach creates its own tables the first time it connects. There is no
migration step and nothing to run by hand.

---

## 2. Accounts

Roast Coach never stores a password. It stores a PBKDF2-SHA256 hash of one, which
cannot be turned back into the password. Three ways to make them — pick any.

### Easiest: the app itself

Open the app with no accounts set up. Instead of a sign-in it offers to make the
first one: type the name and password you want, press **Make the line to paste**,
and it prints the two lines for step 3. The password is not in them and cannot be
worked back out of them.

That is only the *making* of the line. Secrets are read-only from inside the app —
which is the point of them — so pasting is still step 3, and still yours.

### Or: the browser tool (nothing to install)

Open **`password_tool.html`** — double-click the file, or use the hosted copy if you
have the link. Type a name and a password, press **Add to the list**, repeat for each
person, then copy the block it builds.

The hashing happens in your own browser using its built-in crypto. Nothing is sent
anywhere, nothing is saved, and the page works with no internet connection. Close the
tab and the passwords are gone — so copy the block first.

### Or: the script

On your own computer, in the project folder:

```bash
pip install -r requirements.txt
python3 make_login.py
```

It asks for a name and password for each person and prints the same block.

All three produce identical output; use whichever is less trouble.

---

## 3. Put both into secrets

**On Streamlit Community Cloud** — open your app, **⋮ → Settings → Secrets**,
paste, **Save**. The app restarts by itself.

**Running it yourself** — save the same text as `.streamlit/secrets.toml` beside
`app.py`. That path is already in `.gitignore`; never commit it.

```toml
[database]
url = "postgresql://postgres.abcdefghijklmnop:your-password@aws-1-us-east-2.pooler.supabase.com:5432/postgres"

[passwords]
admin = "pbkdf2_sha256$240000$k9Fs…$Qm1a…"
```

One line is all you need here. This is the founding account — the app cannot edit it,
which is what makes it the way back in if anything else goes wrong. Everybody else is
added from inside the app, in step 4.

Open the app. It should ask you to sign in, and the **Data** page should say
*Postgres* under "Where this is stored". If it still says SQLite, the `[database]`
section did not take — check for a typo in the section name.

The first time you sign in, the app writes an **`admin`** account carrying the same
password as the line above. Sign in as `admin` from then on. Once you are happy it
works, delete the old line from secrets — leaving it there is a second door with the
same key.

---

## 4. Everyone else

On **Setup → People**, give each person a name to sign in with and a password, and
choose what they can do:

- **roaster** — everything the app does with roasts: import, edit, plan, grade.
- **admin** — that, and the making and unmaking of people.

Send them the app's URL and the name and password you set. Nothing to install. Each
person signs in on their own computer and sees the same roasts; whoever imports a
roast is recorded against it. They can change their own password once they are in;
the app never shows you theirs, because it never keeps it.

---

## Adding and removing people

All on **Setup → People**, as an admin:

- **Reset a password** if somebody forgets theirs, or if one gets out. The old one
  stops working immediately.
- **Suspend** an account to stop it signing in while keeping everything it recorded.
  It can be turned back on.
- **Remove** an account for good. Suspending is almost always the better answer:
  a removed person's name still sits on every roast and note they recorded.

The app will not let the last admin demote, suspend or remove themselves — somebody
has to be able to get in. An account in secrets always counts as that somebody, which
is why keeping one there is not a bad idea even after you stop using it.

Anyone signed in can import roasts and edit them, so only hand out accounts you would
trust with the data.

---

## Moving the roasts you already have

If you have been using the app on your own and want to bring that history into
the shared database, point the app at the old file and the new database in turn:

```bash
# read from the old SQLite file, write to Supabase
ROAST_COACH_DB=roast_coach.db \
ROAST_COACH_DATABASE_URL="postgresql://…" \
python3 - <<'PY'
from roastcoach import db, store

old = "roast_coach.db"                       # the file
new = db.database_url()                      # the shared database
roasts = store.load_roasts(old)
print(f"{len(roasts)} roasts to move")
for uid in roasts["uid"]:
    curve = store.load_curve(uid, old)
    print(uid, len(curve), "samples")
PY
```

In practice the simpler route is to re-import the original RoasTime files into
the shared database — the app skips anything it already has, so you can select
the whole folder and let it sort itself out.

---

## Costs

Supabase's free tier is free, and pauses a project after a week with no
connections; opening the app wakes it, which takes a few seconds the first time.
Streamlit Community Cloud is free for public apps. Nothing here needs a card.

---

## If something is wrong

**"Cannot assign requested address"** — the direct connection string, which needs
IPv6. Use the Session pooler URI instead.

**"password authentication failed"** — the `[YOUR-PASSWORD]` placeholder is still
in the URL, or the password has a character that needs percent-encoding.

**The Data page says SQLite** — the `[database]` section is missing or misspelled
in secrets. It must be exactly `[database]` with a `url` key.

**"No accounts are set up yet"** — the `[passwords]` section is missing, or it is
there but the app has not restarted since. The screen that says so also makes the
first account for you: fill in a name and password, press **Make the line to
paste**, and put the result in secrets as in step 3. On a local database only, it
additionally offers "Continue without signing in"; on a shared one it does not, on
purpose.

**Somebody you added cannot sign in** — check **Setup → People**. An account
showing *suspended* is switched off; press **Let back in**. If they are not listed
at all, the app is on a different database from the one you added them to, which
the **Data** page will tell you.

**No People tab** — the deploy is running an older `roastcoach/auth.py` than
`app.py`. The **Data** page names every file it actually read and which are behind.

**Everything is slow the first time each morning** — Supabase pausing an idle
free project. It wakes on the first connection.
