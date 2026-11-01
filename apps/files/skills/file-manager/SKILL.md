---
name: file-manager
description: When to use the Files app: showing the user a folder on the device's screen. Use this when the user asks to open or show a folder or to see what is in it on the device.
---

# The Files app

The Files app shows the SD card folder by folder; the user copies, moves, renames and deletes there by hand.

- `files_show_folder` with `path` (from the card's root, e.g. `Music/Albums`; empty for the root) puts that folder on the screen and returns its listing. The Files app is opened first if it is not open.
- `files_current_folder` returns the folder on the screen and its entries.
- `files_open` with `arg` set to a file's path opens the file in the app that handles its type (the Notepad for `.txt`, the players for music and video).

Reading, writing and moving files for the user is done with your own file tools, not through this app; use the app to **show** the user something.
