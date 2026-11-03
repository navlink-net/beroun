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

## On the screen

`books_open_book` opens a book in the reader at the owner's place; `books_page_text` is the page the owner sees now and `books_where_am_i` the chapter. Use them only when the owner is reading right now ("what does this mean?", "who is this character?").

## Do not

- Do not turn pages with the buttons or type into the app: use the tools above.
- Do not copy long passages out: summarise or quote a line.
