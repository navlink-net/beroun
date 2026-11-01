---
name: music-player
description: How to play music on the device with its Music app. Use this when the user asks to play, pause or skip music, or to ask what is playing.
---

# Music

- `audioplayer_play` with `path` (from the SD card's root): a file starts it and queues the rest of its folder; a folder plays all of it with its subfolders in name order; a `.m3u` playlist plays its list. Find the path with your file tools or the Files app.
- `audioplayer_now_playing` says what plays; `pause`, `resume`, `next`, `previous`, `stop`, `seek` (seconds, negative goes back) are the transport.

If the result says the sound did not start, the device refused to start it without a touch; tell the user to press play or A once, do not retry in a loop.

One player at a time: starting music closes whatever other app is in the window; the music goes on only while the Music app is open.
