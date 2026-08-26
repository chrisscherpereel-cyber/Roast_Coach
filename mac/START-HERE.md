# If macOS will not let you open these files

You have hit a change Apple made in **macOS Sequoia**: the old trick of
right-clicking a downloaded file and choosing **Open** no longer gets past
Gatekeeper. It was removed on purpose. That is why right-click → Open does
nothing for you.

There are two ways round it. The first avoids Gatekeeper entirely and is the one
to use.

---

## Do this — run it from Terminal

Gatekeeper stops the Finder from *launching* a downloaded file. It does not stop
you from handing that file to the shell yourself. So don't double-click it.

1. Open **Terminal**. (⌘-Space, type `Terminal`, Return.)
2. Type exactly this, **including the trailing space**:

   ```
   bash 
   ```

3. Now **drag `setup.command` from your Finder window into the Terminal window**
   and let go. Terminal fills in its full path for you — no typing, no matter
   where you unzipped it.
4. Press **Return**.

The setup runs exactly as it would have, and asks you the same one question.

That is the whole workaround. Nothing to approve, nothing to change in System
Settings.

---

## Or: let it through in System Settings

If you would rather double-click things:

1. Double-click `setup.command`. It gets blocked — that is expected.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to **Security**. There is a line saying *"setup.command" was
   blocked to protect your Mac*, with an **Open Anyway** button beside it.
4. Press **Open Anyway**, and confirm with your password or Touch ID.

The blocked-file message only appears for a while after the attempt, so do step 1
first, then go looking for it.

---

## If it opens but says "permission denied"

Unzipping can strip the flag that makes a file runnable. One line in Terminal
fixes every file in this folder — drag the **mac** folder in after `chmod +x`
rather than typing a path:

```
chmod +x /path/to/roast-coach/mac/*
```

Then try again. (The Terminal method above does not need this, because `bash`
reads the file rather than running it.)

---

## If the schedule still will not turn on

The setup does its job in two halves: it sends your roasts across, and then it
asks macOS to repeat that every fifteen minutes. The first half can succeed while
the second fails — and if it does, **your roasts are already in the database**;
only the repeating is missing.

It now tells you exactly what macOS complained about instead of shrugging, and if
the modern scheduler refuses it quietly sets up the older one instead, which asks
permission from nobody.

If both refuse, you have two perfectly good fallbacks:

- **Run it when you roast.** `bash /path/to/mac/setup.command`, same as above. It
  takes seconds and only reads what has changed.
- **Leave it running.** In a Terminal window you keep open:

  ```
  python3 /path/to/mac/sync_to_database.py --every 15
  ```

  It prints a line every fifteen minutes and keeps going until you close it.

---

## How to tell it is working

```
open ~/Library/Logs/roast-coach-sync.log
```

You should see a line per check — `1 new → Postgres…` or `nothing new in
roasts`. If that file does not exist at all, nothing has run yet.

And in the app itself: the **Data** page shows the roast count and where it is
storing things. The count going up without you uploading anything is the proof.
