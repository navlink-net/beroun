---
name: account-notes
description: How to find, read, write, move and delete the owner's notes in the Notes app (their SquirrelWisdom notes, the same as on the website). Use this when the user speaks of "my notes", asks you to note something down, find a note, add to a note, or to keep a list in a folder. Not for plain .txt files (that is the Notepad).
---

# Notes of the account

The Notes app holds the owner's notes of their SquirrelWisdom account; the website shows the same ones. The tools are in the group `app-notes` and work **with the app closed**; the device keeps a copy, so they work offline and the changes go up when the network is back.

A note is text; its **first line is its title**. Notes live in folders (`Work/Projects`); `''` is the top.

## Finding and reading

1. `list_notes` with a folder shows its notes and sub-folders (newest first). Start at the top (`folder: ""`).
2. `search_notes` with a few words finds notes whose text or folder holds every word, in any folder.
3. `get_note` gives the whole text by `id`. Read a note before you change it.

## Writing

- `create_note`: text (title on the first line) and optionally a folder. Use it for "make a note", "write down…".
- `append_to_note`: adds text at the end. Use it for "add this to my shopping list": it keeps what is there.
- `replace_note`: only when asked to rewrite or clear a note; call `get_note` first and keep what must stay.
- `move_note`, `create_folder`, `rename_folder`: tidying.

## Deleting

`delete_note` and `delete_folder` (a folder takes every note in it with it) can be undone only on the website's side, not here. Ask the owner first unless they asked for exactly that.

## On the screen

`notes_open_note` and `notes_open_folder` show a note or a folder in the app; only when the owner wants to see it.

## Do not

- Do not type into the app with `beroun_type`: write with the tools above.
- Do not create a second note for something that has a note already: search first.
