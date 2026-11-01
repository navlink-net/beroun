---
name: notepad-notes
description: How to read, write and open plain-text notes in the device's Notepad app. Use this whenever the user asks you to write something down, add to a note, read a note back or open a text file in Notepad.
---

# Notes in the Notepad

The Notepad edits one text file at a time (`.txt` on the SD card, new notes go to `Notepad/Note-<date>-<time>.txt`). Its tools are in the tool group `app-notepad`.

## Reading and writing the open note

1. `notepad_get_text` returns the file that is open and its text. If the Notepad is not open it is opened first (at the note it was closed on).
2. `notepad_append_text` adds text at the end. Use it for "add this to my note": it never loses what is there.
3. `notepad_set_text` replaces the whole text. Use it only when asked to rewrite or clear the note, and read the note with `notepad_get_text` first so you can keep what must stay.

The Notepad saves by itself half a second after the text changes.

## Another note

`notepad_open` with `arg` set to the file's path (relative to the SD card, e.g. `Notepad/shopping.txt`) opens that file. To create a note with a name, write the file with your own file tools first, then open it.

## Do not

- Do not use the on-screen keyboard tools (`beroun_type`, `beroun_key`) for text you can put in with `notepad_set_text` or `notepad_append_text`.
- Do not close the Notepad to "save" it: it saves itself, and closing only returns the launcher to the screen.
