---
name: quidditch
description: How to start and follow a single-player Quidditch match on the device and what the controls do. Use this when the user wants to play Quidditch or asks how it is going.
---

# Quidditch

A single-player match: the user flies one of the seven players of Gryffindor or Slytherin against the AI. The match ends when a Seeker catches the Snitch (+150); goals are +10.

1. `quidditch_status` says what the app shows now (the title, the setup, a match, the pause menu, the end) and, in a match, the score, the time and what the player holds.
2. `quidditch_start_match` starts a match at once: `team` (gryffindor / slytherin), `player` (a slot 0-6, a role such as `seeker`, or a name), `difficulty` (easy / normal / hard; it changes the opponents only). Without arguments it uses the last setup.
3. `quidditch_pause` and `quidditch_resume` stop and go on. The user plays with the console's buttons: the left stick flies, the right stick aims, A is the main action of the role, X passes, Y changes the camera, B opens the menu.

With an external screen connected the match is on it (the console shows the splash); the tools work the same. Do not play for the user unless asked: you start, pause and report.
