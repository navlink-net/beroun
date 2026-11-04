---
name: account-audiobooks
description: How to find an audiobook, know and set where the owner stopped listening, and play or pause it in the Audiobooks app (the SquirrelWisdom audiobooks, MP3). Use this when the user speaks of "my audiobook", asks to continue listening, to play a book aloud, or where they stopped.
---

# Audiobooks of the account

The Audiobooks app holds the public audiobooks of SquirrelWisdom and the owner's own. An audiobook is a list of numbered MP3 files; the place where the owner stopped is a file and a second in it. The tools are in the group `app-audiobooks` and work **with the app closed**. The place is kept on the site, so it is the same on every device and in the web player.

Every book has an `id` and a scope: `public` (default) or `mine`.

## Finding and continuing

1. `find_audiobooks` with a few words of the title, author or narrator. Add `scope: "mine"` for the owner's own books.
2. `recent_audiobooks` shows what the owner is listening to and where they stopped: use it for "continue my audiobook".
3. `audiobook_info` gives the annotation, the number of files and the place (`listening.index` counts files from 0).

## The place

`set_listening_place` sets the file (counted from 1) and the second; use it when the owner says "I listened up to file 12" or "start from the beginning" (file 1, second 0). `listening_settings` tells the playback speed (1 = normal) and changes it ("a bit faster" = +0.2).

## Bookmarks

Bookmarks belong to the owner and to one book, and are kept on the site, so the same list shows on the other devices and in the web player. `list_bookmarks` gives them (the file counted from 0 as `index`, and `time`); `add_bookmark` takes a file (from 1) and seconds - add one when the owner says "remember this place"; `remove_bookmark` takes the id from the list.

## On the screen

`audiobooks_play_audiobook` opens a book and plays it from the owner's place; `audiobooks_pause`, `audiobooks_resume`, `audiobooks_seek` (seconds, negative = back), `audiobooks_bookmark_here`, `audiobooks_next_file` and `audiobooks_now_playing` drive the player. The sound stops when the app is closed.

## Do not

- Do not press the buttons or type into the app: use the tools above.
- Do not guess what a book says from its title: the app has no text of audiobooks.
