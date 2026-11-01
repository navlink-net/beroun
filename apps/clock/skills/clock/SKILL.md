---
name: clock
description: How to set, change and cancel alarms and the timer on the device's Clock, and stop what rings. Use this when the user wants to be woken up, reminded after a time, or asks what alarms are set.
---

# Clock

The Clock keeps alarms and one timer in a service (`alarmd`) that runs all the time, also while the console sleeps. The app does not have to be open: an alarm that rings takes over the screen by itself.

Tools (the MCP server `alarms`, and two for the screen):

1. `list_alarms` first, when the user speaks of "the alarm" or asks what is set.
2. `set_alarm` with `hour` (0-23), `minute` and optionally `days` (0 = Monday .. 6 = Sunday; no days = rings once, the next time the clock shows that time) and `label`. To change an alarm pass its `id`.
3. `toggle_alarm` switches one on or off without deleting it; `delete_alarm` removes it.
4. `start_timer` with `seconds` ("in 10 minutes" is 600); `get_timer` says what is left (`end` minus `now`); `cancel_timer`.
5. `stop_ringing` / `snooze` when the user says it rings and they are not at the buttons.
6. `clock_show_tab` (`clock`, `alarms`, `timer`, `stopwatch`) shows it on the screen when the user asks to see it.

The time is the console's own, in its time zone. Say the time back in the form the user used. "Tomorrow at 7" is one alarm at 7:00 with no days. "Every weekday" is `days` `[0,1,2,3,4]`.
