---
name: video-recorder
description: How to record the console's screen into a video with the Video Recorder. Use this when the user asks to record, film or capture what is on the screen, to stop that, or to find the recordings.
---

# Video Recorder

The recording is made by a service (`screenrecd`) that keeps running with the app closed, so the screen that is recorded is whatever app the user is in.

1. `recording_status` first: `can_record` false means this device has no screen to grab, say so and stop. `state` says whether it already records.
2. `start_recording` with `delay` (seconds, 0 to 10) when the user needs time to open the app to be filmed; `audio` is on by default and records the sound the console plays.
3. `stop_recording` ends it and saves `Videos/Screen-<date>.mp4` on the SD card; a recording also ends by itself after 30 minutes.
4. `list_recordings` shows the files. They open in the Video app (`videoplayer_play` with the path).

Your own window is on the screen while you work: a recording started with no delay shows the assistant's view for the first moments.
