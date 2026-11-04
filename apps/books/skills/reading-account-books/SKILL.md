---
name: account-books
description: How to find a book, read its text, search inside it and know where the owner stopped in the Books app (the SquirrelWisdom library, FB2). Use this when the user speaks of "my book", asks what a book says, to find a book, to continue reading, or to open a book on the screen.
---

# Books of the account

The Books app holds the public library of SquirrelWisdom and the owner's own books (FB2). The tools are in the group `app-books` and work **with the app closed**; a book read once stays on the device, so it also works offline.

Every book has an `id` and a scope: `public` (default) or `mine`.

## Finding

1. `find_books` with a few words of the title or author. Add `scope: "mine"` for the owner's own books.
2. `recent_books` shows what the owner is reading and where they stopped: use it for "continue my book".

## Reading

1. `book_contents` gives the chapters (`n`, title, size) and the place where the owner stopped.
2. `read_chapter` gives the text of chapter `n`; long chapters come in parts: go on from `next_block` with `from_block`.
3. `search_in_book` finds where words occur; then read that chapter.

Read the text before you retell it or answer about it; do not guess a plot from the title.

## Bookmarks and font size

Bookmarks belong to the owner and to one book; they are kept on the site as well, so the same list shows on the other devices and in the web reader.

- `list_bookmarks` gives them in reading order (each with a fragment of the text and the percent).
- `add_bookmark` needs a chapter and a block: take them from `search_in_book`, from `read_chapter` (the block you read) or from `book_contents`. Add one when the owner says "remember this place" / "bookmark that".
- `remove_bookmark` takes the id from `list_bookmarks`.
- `reading_settings` without arguments tells the font size (percent, 100 = normal) and the page theme; with `fontScale` (50-300) or `theme` it changes them ("make the letters bigger" = about +20).

## On the screen

`books_open_book` opens a book in the reader at the owner's place; `books_page_text` is the page the owner sees now, `books_where_am_i` the chapter and `books_bookmark_here` puts a bookmark on that page. Use them only when the owner is reading right now ("what does this mean?", "who is this character?").

## Do not

- Do not turn pages with the buttons or type into the app: use the tools above.
- Do not copy long passages out: summarise or quote a line.
