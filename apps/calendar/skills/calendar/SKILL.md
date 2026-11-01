---
name: calendar
description: How to add, list and remove events and tasks on the device's Calendar and task list. Use this when the user wants something put in the calendar, asks what is planned, or keeps a to-do list.
---

# Calendar and tasks

The Calendar keeps events (a date, maybe a time) and a task list (maybe with a due date) in a service (`calendard`). The app does not have to be open.

1. `list_events` with `from` / `to` (YYYY-MM-DD) answers "what do I have tomorrow / this week". Work the dates out from the console's time, not from memory.
2. `add_event` with `title`, `date`, and `time` (HH:MM) when there is one. To change an event pass its `id`; `delete_event` removes it.
3. `list_tasks` (`done: false` for what is still open), `add_task` with `title` and `due`, `complete_task` with the task's `id`, `delete_task`.
4. An alarm is not an event: for "wake me at 7" use the Clock. For "remind me" on a day, add a task with that due date.
5. `calendar_show_date` and `calendar_show_tasks` put it on the screen when the user asks to see it.

Confirm what was added in a short sentence with the date written out ("Friday 30 October, 14:00").
