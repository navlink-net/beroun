# Quidditch (single player)

You pick a team (Gryffindor or Slytherin), one of its seven players and the opponents' difficulty, and fly that player against the AI. The match ends when a Seeker catches the Snitch
(+150); goals are +10. No server, no login, no lobby: the app is made from the gamepool game (`D:\REPO\gamepool\Quidditch`, which stays as it is) for IntelliCat.

* `sim.js`: the rules, a pure module (no DOM): the AI, the balls, the Snitch (tired for 3 s every 14-26 s: only then can it be caught), the actions of every role, a seeded random generator,
  a state that is plain data (`snapshot` / `restore`). `tests/quidditch_sim_test.js` steps it in Node.
* `index.html`: the screens at 640x480 (title, how to play, setup, match, pause, end), the controls, the state (`Platform.state`: a match in progress comes back paused), the Caroline tools.
* **Controls:** left stick / D-pad fly; right stick aims; **A** the main action of the role; **X** pass; **Y** camera; **B** menu. Touch: the menus and the setup rows.
* **External screen:** the same page runs on the big screen (`web-shell/gameext.js`, manifest `external` with `"input": "game"`); the console shows the splash.
* **Caroline** (`manifest.json` "caroline", `skills/quidditch`): `status`, `start_match`, `pause`, `resume`.
* **Balance** (`tests/quidditch_balance.js`, bots that play like a careful human): the AI plays your slot too = the reference, about 69% / 47% / 24% won on easy / normal / hard. A seeker bot
  wins 82% / 77% / 61%; the other roles are the reference or a little below (a bot is a worse defender than the AI). The goals count: a seeker of the AI catches the Snitch more often when its team
  leads, less when it trails, so the Chaser, the Beater and the Keeper you fly decide matches too.
* The art is converted from the gamepool game (webp, 640 KB in all).
