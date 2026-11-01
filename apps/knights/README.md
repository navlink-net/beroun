# Knights & Ladies (single player)

The tournament of the multiplayer game (`gamepool/KnightsAndPrincesses`) without a server: you play one
knight or one lady, the other fifteen are NPCs. `index.html` is the screens (640x480, gamepad);
`engine.js` is the rules, built from `src/` (a copy of the shared types, the NPC roster and the battle
engine of the multiplayer game, with the timers taken out, and `tournament.ts`):

    esbuild src/tournament.ts --bundle --format=iife --global-name=Engine --target=es2020 --outfile=engine.js

Two things differ from the multiplayer rules: there are no timers (the player prepares as long as they like),
and the hidden +2 points the server gave to human players is gone.

## The bout bars and the luck (2026-10-29)

* **The bars are health, and both move.** The outcome is rolled at the start of a bout (one die), but the bars no longer give it away: the winner takes 30-60 points of damage
  (more when the loser's attack goes through his armour), the loser ends at 4, and each follows one of a few scripts picked at random (`WINNER_SCRIPTS`, `LOSER_SCRIPTS` in
  `src/battle.ts`), so in the first two ticks the winner's bar is the lower one in about half the bouts. A bar never grows back (the new winner goes on from where its bar
  is when the spells change the outcome). The bar says "Health", and a strip under the ladies shows the **chance to win** as it stands now (the spells move it).
* **Luck.** After each lost bout in a row the human's fighter (his knight; for a lady the first knight she backs) has 5 points more chance to win (`LUCK_STEP`), at most 20
  (`LUCK_MAX`), never lifted above 85% (`LUCK_CEILING`); a win resets it. The tournament counts it (`state.luckStreak`, saved with it). A knight with the basic gear and no
  ladies used to win 32% of his bouts, and 6% of the tournaments had no win at all (400 tournaments, headless); now 39% and 1% (12 of 1200).
* The NPC ladies read the bars to decide a spell, so their casting follows the new bars.
* `tests/knights_engine_test.js` (node, no browser) checks all of it. `engine.js` and `src/*.ts` carry the same change (no esbuild here: the bundle was edited alongside).
