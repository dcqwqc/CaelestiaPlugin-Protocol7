# Protocol 7 live preview (Groq chunked incremental transcription)

Status: **draft implementation on the development branch
`feature/live-transcription-20261021`. Not merged, not deployed, not enabled
anywhere.** All evidence so far is offline: mocked Groq clients, synthetic
audio, sockets blocked. No real Groq request was made, so free-tier latency,
accuracy and real-microphone behavior are **not yet measured**.

This replaces the earlier OpenAI Realtime design (paid endpoint, separate
key, billing consent). That code, its keys and its UI are gone. Old
`streaming_stt_*` keys left in an existing `config.json`
(`streaming_stt_api_key`, `streaming_stt_model`,
`streaming_stt_billing_authorized`, `streaming_stt_finalize_timeout_seconds`,
provider `openai-realtime`) are ignored; `openai-realtime` reports
"unsupported provider" and creates nothing.

## What it does, and what it never does

- While the user speaks, a preview line appears in the recording pill's
  existing hover area (`P7STATE.liveText`), updated about every 5–6 s.
- The preview is **display-only**. It is never inserted, saved to history,
  logged, or used as the dictation result.
- On stop, the ghost job **always** transcribes the complete original
  recording with the existing Groq final path, exactly once per attempt
  (Retry is a new attempt). A failed, throttled or disabled preview never
  prevents or delays that final.
- Previews use the Groq client and model already selected for final
  dictation (`use_groq`, `groq_api_key`, `groq_model`). There is no other
  provider, credential or billing route. The client must point at
  `https://*.groq.com`; anything else is refused before any request.

## Configuration (`~/.config/protocol-7/config.json`)

| key | default | meaning |
| --- | --- | --- |
| `streaming_stt_enabled` | `false` | master switch (must be JSON `true`) |
| `streaming_stt_provider` | `"groq-chunks"` | the only supported value |
| `streaming_stt_preview_interval_seconds` | `5.0` | cadence, clamped to 4–6 s |

Previews also require `use_groq: true` and a non-empty `groq_api_key`. The
native Caelestia settings expose only the switch ("Live preview
(experimental)") and the provider (`groq-chunks` as the single option).
`entry/Main.qml` forwards just those two keys.

## Pipeline

```
PortAudio callback ── put_nowait ──> bounded queue ──> preview worker thread
   │                                                   │  VAD + window planner (ring buffer)
   └─> audio_queue (complete recording)                │  process-wide budget (single flight)
            │                                          └─> request thread ── Groq (timeout 4 s)
            │                                                   └─> reconciler ─> set_live_text
            └─ on stop ─> ghost job ─> final Groq transcription of the full audio (once)
```

- **Capture thread** (`audio.AudioRecorder._audio_callback`): only
  `put_nowait`. A full queue drops the chunk *for the preview only* and
  counts the lost samples; the final recording is unaffected.
- **Worker** (`protocol7-preview`): drains chunks, runs voice detection,
  proposes windows, reserves budget, starts at most one request thread.
  It stops itself when no audio arrives for 3 s (dead recorder) or after
  15 min, and the main loop stops it the moment recording stops.
- **Request thread** (`protocol7-preview-request`): encodes a 16 kHz mono
  PCM16 WAV, re-checks that the session is still live, then calls
  `client.audio.transcriptions` (`verbose_json`, `temperature=0`, per-request
  `timeout=4.0`, language hint only when auto-detect is off). Rate-limit
  headers are read through `with_raw_response` when the SDK offers it.
- `stop()` / `cancel()` never wait for an in-flight request. After they
  return, no partial is published and no new request starts. A late result
  only updates the budget.

### Windows

- First preview after 4 s of audio, then one window per 5 s of new audio.
  Each window is the latest 6 s (interval + 1 s overlap with the previous
  window). The ring buffer holds window + 1 s, so memory per recording is
  bounded (~450 KB at 16 kHz).
- A window is sent only if it holds at least 0.6 s of detected speech *and*
  at least 0.5 s of speech is new since the last window. Silence is used up
  without a request, so the same speech is never sent twice without new
  voiced samples.
- If unsent speech was skipped (throttling, queue overflow, failed request),
  the next window is marked as a gap and joined with `…` instead of being
  merged.

### Voice activity

`SpeechDetector` is deterministic and dependency-free. Each 30 ms frame is
speech-like if it is ≥ −48 dBFS, ≥ 12 dB above an adaptive noise floor
(minimum tracking, rising 1 dB/s) and has a zero-crossing rate ≤ 0.4. A run
counts as speech only when it is ≥ 210 ms long and ≥ 60 % dense (gaps ≤ 90 ms
before confirmation, 200 ms after). In synthetic tests this rejects silence,
low and loud broadband noise, mains hum, typing/clicks and 150 ms blips, and
it detects syllable-modulated harmonic signals down to about −40 dBFS. These
thresholds are **not yet tuned on real microphones**.

### Hallucination filtering

Segments with `no_speech_prob > 0.85`, `no_speech_prob > 0.6` and
`avg_logprob < -0.7`, or `compression_ratio > 2.4` are dropped. Text without
letters, single-phrase Whisper artifacts ("Thank you.", "Thanks for
watching", "you", …) and decoder loops are not shown. This affects the
preview only; the final has its own existing safeguards.

### Stable text

`TextReconciler` aligns the start of each overlapping window with the end of
the current preview, position by position (so one misrecognized word inside
the overlap does not break the match, and a cut-off last word may match its
completed form). The newer window's wording wins inside the overlap, which
fixes edge punctuation such as a premature full stop. Only the last 8 words
can ever change; everything earlier is frozen, so text never blinks or jumps
back. The preview keeps at most 300 words.

## Free-tier budget and rate limits

Groq's free tier for `whisper-large-v3` / `-turbo` is 20 requests/min, 2000
requests/day, 7200 audio-seconds/hour and 28800 audio-seconds/day per
organization. Those limits are shared with final dictation and with any
other device using the same key. `PreviewBudget` (one per backend process)
therefore:

- counts every request as at least 10 s of audio (Groq's minimum billed
  length), and counts **finals as well as previews**;
- allows previews only while total usage stays within 12 requests/min and
  40 % of the daily request, hourly audio and daily audio limits;
- never starts a preview while a final is in flight (`final_priority`);
- keeps exactly one preview request in flight process-wide;
- on HTTP 429 disables previews for that recording and cools down all
  previews for `max(retry-after, 60 s)`, doubling per strike; the third
  429 within 24 h disables previews until the backend restarts;
- on 401/403 disables previews until restart; on timeout/5xx/connection
  errors cools down 10 s and disables the recording's previews after two
  consecutive failures;
- when `x-ratelimit-remaining-requests` falls to 5 % (≥ 20 requests) of
  the daily limit, pauses previews until `x-ratelimit-reset-requests`.

At the default cadence a continuous minute of speech costs about 11–12
preview requests (≈ 110–120 billed audio-seconds) plus the final. The
hourly preview share runs out after roughly 24 minutes of continuous
speech, after which only finals are sent.

Final dictation itself is unchanged: one request, `max_retries=0`, 10 s
client timeout. Previews leave headroom for it but cannot guarantee that
another device has not exhausted the organization's limits.

## Privacy

The API key is never held by the preview session. Logs contain status words
only (`preview deferred (rpm)`, `preview request failed (timeout)`, …):
never transcript text, audio, exception messages or credentials. Provider
errors are reduced to a category. No recordings are stored or committed;
all test audio is synthesized from seeded generators.

## Tests and benchmark (offline)

```
python -m unittest tests.test_groq_chunks tests.test_streaming_stt \
    tests.test_streaming_integration tests.test_streaming_plugin_config -v
python tests/test_groq_chunks.py --benchmark
```

Every module patches `socket.socket.connect` to fail, history and ghost-job
persistence are redirected to temporary directories, and Groq is an
in-memory fake. On the development host the system Python lacks NumPy, so
the suites run in the private validation venv
(`~/.local/state/loom-fleet/validation-venvs/protocol7`, NumPy only).

The benchmark replays 60 s synthetic scripts (seeds 7, 11, 23, 42) in
virtual time through the real scheduling, budget, VAD and reconciliation
code, with a mocked Whisper that cuts edge words, adds window-edge full
stops and injects 4 % recognition errors, and 0.35–1.1 s mocked latency.
Results from the commit that introduced this design:

| metric | result |
| --- | --- |
| preview word error rate vs. script | 3.4–9.8 % (naive concatenation: 20.5–28.1 %) |
| first preview after first word | 4.0–4.7 s (virtual) |
| mean update interval | 5.5–6.3 s |
| peak requests in any minute | 10–11 (cap 12) |
| windows sent over silence | 0 |
| max words revised per update | 3 (limit 8) |
| extra adjacent duplicate words | 0–1 |
| `push_audio` cost on capture thread | < 1 µs per call (wall clock, dev host) |

These are properties of the algorithm against a simulator. They are not
Groq latency or accuracy measurements.

## Not yet verified

- Any real Groq request (preview or final) from this branch.
- Real free-tier latency, 429 behavior and rate-limit headers.
- VAD thresholds and preview quality on real microphones (Mirai, Tachibana).
- Visual check of the preview line in the Caelestia hover panel.
- QML was checked by contract tests only; no QML linter or shell run here.

## Rollout and rollback plan (not executed; each step needs approval)

1. Review this branch; run the offline suites above on an independent host.
2. Install on a test machine with `streaming_stt_enabled` left `false`.
   Verify Double-Control dictation, Groq insertion, Enter-to-send, history,
   cancel and dismiss behave exactly as before, and the debug log has no
   `[LIVE_STT]` lines.
3. Enable the switch on one machine. Speak 10–60 s dictations; confirm the
   preview appears, the inserted text always equals the final, silence and
   typing trigger no requests, and the log holds no text or key. Record
   observed preview latency and any 429s.
4. Rollback: switch the preview off (takes effect after the backend
   restart triggered by the setting) or check out the previous commit. No
   data migration is involved.
