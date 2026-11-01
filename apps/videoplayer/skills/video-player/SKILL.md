---
name: video-player
description: How to play video on the device with its Video app. Use this when the user asks to play, pause or skip a video, or what is playing.
---

# Video

- `videoplayer_play` with `path` (from the SD card's root): a file starts it and queues the rest of its folder; a folder plays all its videos with subfolders.
- `videoplayer_now_playing` says what plays; `pause`, `resume`, `next`, `previous`, `stop`, `seek` (seconds, negative goes back) are the transport.

If the result says the picture did not start, the device refused to start it without a touch; tell the user to press play or A once, do not retry in a loop.

The video plays full screen only while the Video app is open.
