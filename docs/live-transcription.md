# Protocol 7 live transcription (incremental STT)

Status: implemented behind an opt-in flag on an isolated development branch.
Not merged, not deployed, no paid API connected, no billing authorized. No
physical hardware test has been performed; everything below marked
"verification plan" is still to be done.

## Goals and non-goals

- Show genuine incremental text while the user is still speaking.
- Keep Groq Whisper (or explicitly selected local Whisper) as the default
  batch path and as the fallback for every live failure.
- Never treat a completed-file upload with `stream=true` as live
  recognition. That only streams the response for audio that was already
  fully recorded; it is not supported as a "live" provider here.
- No new required dependency. The transport uses the optional
  `websocket-client` package (`import websocket`); without it the feature
  reports itself unavailable and nothing else changes.

## Configuration (`~/.config/protocol-7/config.json`)

| key | default | meaning |
| --- | --- | --- |
| `streaming_stt_enabled` | `false` | master switch |
| `streaming_stt_provider` | `""` | must be exactly `openai-realtime` |
| `streaming_stt_api_key` | `""` | provider key; never logged |
| `streaming_stt_model` | `gpt-live-transcribe` | transcription model |
| `streaming_stt_billing_authorized` | `false` | separate, config-file-only consent for paid usage |
| `streaming_stt_finalize_timeout_seconds` | `8.0` | wait for the verified final after stop (clamped 1..30) |

All five gates (enabled, provider, key, billing authorization, installed
`websocket-client`) must pass before a session is created
(`streaming_stt.availability`). Settings.qml exposes the switch, provider,
key and model; billing authorization is intentionally not a UI toggle.

## Architecture

```
PortAudio callback --(put_nowait)--> StreamingSession queue --> I/O thread --> transport
        |                                                         |
        +--> audio_queue (complete recording, unchanged)          +--> partials -> ui_manager.set_live_text
                                                                  +--> final -> GhostJobs._run
```

- `audio.AudioRecorder.attach_stream_listener` replays already captured
  chunks and registers the listener under the callback lock, so every chunk
  is delivered exactly once in capture order. The callback never blocks; a
  listener exception is swallowed. The full recording is still collected
  for the batch path.
- `streaming_stt.StreamingSession` owns one I/O thread that connects, sends
  `session.update`, appends audio (resampled to 24 kHz PCM16 with exact
  cumulative sample accounting), receives events, reconnects at most once,
  and finalizes. `push_audio` is `put_nowait` on a bounded queue; overflow
  marks the session unverifiable instead of blocking.
- `main_linux._toggle_ghost` starts a session after the recorder is live
  (when opted in) and hands it to the ghost job on stop. Partials are shown
  only while recording and only for the current session.
- `ghost_jobs.GhostJobs._run` calls `stream.finish()`; a verified final
  replaces the Groq request, otherwise the retained full recording is
  transcribed in batch. Exactly one of the two results reaches history and
  insertion.

## Wire protocol (Realtime transcription session)

1. Connect `wss://api.openai.com/v1/realtime?intent=transcription` with an
   `Authorization: Bearer` header.
2. Send `session.update` with `session.type = "transcription"`,
   `audio.input.format = {type: audio/pcm, rate: 24000}`,
   `audio.input.transcription.model = gpt-live-transcribe` and server VAD.
3. Stream `input_audio_buffer.append` (base64 PCM16).
4. Handle `input_audio_buffer.committed` (`item_id`, `previous_item_id`),
   `conversation.item.input_audio_transcription.delta`,
   `conversation.item.input_audio_transcription.completed`,
   `conversation.item.input_audio_transcription.failed` and `error`.
5. On user stop: drain the queue, send `input_audio_buffer.commit`; an
   `input_audio_buffer_commit_empty` error counts as acknowledgement
   (server VAD already committed everything).

Items are ordered by `previous_item_id`, independent of event arrival order;
items without that field keep arrival order. Completed transcripts replace
deltas; deltas after completion are ignored.

## When a live final is trusted

`finish()` returns `verified=True` only if all hold: not cancelled; finished
within the timeout; no provider/item error; no dropped audio; no
reconnection gap; the final commit was acknowledged; no committed item is
still pending after a short settle window; the text is non-empty; and the
existing digit-only non-speech guard does not reject it. Anything else returns
`verified=False` with a non-secret reason and the job falls back to batch.

| situation | result |
| --- | --- |
| disabled / missing key, provider, billing consent or dependency | no session; batch path unchanged |
| connect failure | batch fallback; no partials |
| connection drop mid-recording | one reconnect keeps partials; final is batch |
| timeout waiting for final | batch fallback; late events ignored |
| no audio | empty transcript, batch path decides (too short / no speech) |
| cancel or Double-Control dismiss | session cancelled; no batch request, no insertion |
| focus changed | existing ready-chip behaviour; no injection |
| Enter during finalize | existing queued-send behaviour; one Return after paste |

## Privacy

API keys never appear in logs, reprs or exception text (transport errors keep
only the exception type). Transcripts and partials are never logged; provider
error messages are reduced to their error code.

## Tests (offline)

```
python -m unittest discover -s tests -p 'test_streaming*.py' -v
```

Sockets are patched to fail for both modules, so an accidental network call
fails the suite. History and ghost-job state are redirected to temporary
locations.

## Deployment and rollback verification plan (not yet executed)

Philipedia is the source of truth; test machines check out a known commit
with git rather than file sync. Each step needs separate approval.

1. Philipedia: run the unit tests above and `python -m py_compile` on the
   changed modules.
2. Tachibana (`ssh tachibana uname -a` first): check out the reviewed commit
   with streaming left disabled. Verify Double-Control dictation, Groq
   insertion, Enter-to-send, history and dismiss behave exactly as before,
   and that the debug log contains no `[LIVE_STT]` lines.
3. Only after explicit billing authorization: install `websocket-client` in
   the Protocol 7 environment, set the five config keys, and verify
   partials while speaking, a verified final with no Groq call, and
   fallback when the network is cut mid-recording. Check the log contains
   no key or transcript text.
4. Mirai (probe with `ssh -o ConnectTimeout=5 mirai hostname`; never build
   there): repeat step 2 only, as the user's daily machine.
5. Rollback: set `streaming_stt_enabled` to `false` (takes effect on the
   next recording after restart), or check out the previous commit
   (`34b768d`) and restart the backend. No data migration is involved; the
   added config keys are ignored by older versions.
