# Live Preview Acceptance Note

Status: **draft, pending review and hardware verification.** This note is
not a release approval.

## Scope

This note covers the opt-in `groq-chunks` live preview described in
[live-transcription.md](live-transcription.md), on the development branch
`feature/live-transcription-20261021`. It replaces the earlier OpenAI
Realtime implementation, which was removed together with its key, billing
consent and settings. Earlier acceptance notes about that design (41 mock
tests, paid-endpoint gates) no longer apply.

## What has been validated (offline only)

- Syntax: `python -m py_compile` on every changed Python module.
- Unit and integration tests in the private validation venv
  (`~/.local/state/loom-fleet/validation-venvs/protocol7`, Python 3.13,
  NumPy 2.5.3 only), with `socket.socket.connect` patched to fail:
  `tests.test_groq_chunks`, `tests.test_streaming_stt`,
  `tests.test_streaming_integration`, `tests.test_streaming_plugin_config`.
  The exact count and outcome are recorded in the commit report.
- Deterministic synthetic benchmark (`python tests/test_groq_chunks.py
  --benchmark`) with mocked Whisper responses and virtual time.

All of this used fake Groq clients and synthesized audio. **No Groq or other
speech API was called, no real microphone was used, and no latency or
accuracy of the real service was measured.**

## Behavior covered by the tests

1. **Final always on the full recording.** With or without a preview, and
   whatever the preview did (text, failure, exception), the ghost job sends
   the complete original audio to the final path exactly once per attempt.
   Preview text is never inserted or saved.
2. **Opt-in and Groq-only.** Default config creates nothing. Previews need
   the switch, provider `groq-chunks`, `use_groq` and a Groq key. Legacy
   `openai-realtime` configs stay off. Feature sources contain no OpenAI,
   Realtime or websocket route; non-Groq or non-HTTPS clients are refused.
3. **Capture safety.** The capture callback only enqueues; no request runs
   on it; queue pressure only degrades the preview.
4. **Silence and noise.** Silence, broadband noise, hum, typing/clicks and
   short blips trigger no request; no window is sent without new speech;
   known Whisper artifacts are not shown.
5. **Windows.** First window at 4 s, then 6 s windows overlapping by exactly
   1 s; gaps are marked, never merged.
6. **Budget.** RPM cap with finals counted, hourly/daily audio caps with
   10 s minimum billing, daily request cap, single flight, finals pre-empt
   previews, 429 retry-after cooldown with escalation to disable, auth
   disable, rate-limit header reserve, refund when nothing was sent.
7. **Lifecycle.** Stop/cancel never wait for a request and suppress late
   results; idle or stopped sessions never call Groq; request cap per
   recording; cancel, dismiss, final timeout, focus change and queued Enter
   keep their existing semantics.
8. **Stable text.** Overlap de-duplication, cut-word completion, edge
   punctuation repair, frozen prefix, bounded length.
9. **Privacy.** No key or transcript in logs, reprs or stats.
10. **Shell contract.** Settings property types match their `SettingMeta`
    input types, Main.qml only reads existing settings and forwards only the
    switch and provider.

## Acceptance gates

Record evidence (command, host, commit, outcome) for each gate. Do not mark
a gate from this note alone.

- [ ] **G1 Reproducible tests** on an independently configured host at this
      commit or a descendant.
- [ ] **G2 Default safety:** a fresh install with default configuration
      makes no preview request (debug log has no `[LIVE_STT]` lines).
- [ ] **G3 Real Groq preview:** one deliberate, approved test with the
      free-tier key: previews appear, the inserted text equals the final,
      preview latency and rate-limit headers recorded.
- [ ] **G4 Rate-limit behavior** observed or safely provoked: a 429 stops
      the preview and the final still completes.
- [ ] **G5 Mirai hardware check** (probe with
      `ssh -o ConnectTimeout=5 mirai hostname`; do not build there): built-in
      microphone, silence/typing produce no requests, insertion lands in the
      focused window.
- [ ] **G6 Tachibana hardware check** (`ssh tachibana uname -a` first; Linux
      required): same checks under Hyprland, plus a screenshot of the hover
      panel showing a preview.
- [ ] **G7 Reviewer sign-off** on the recorded evidence for G1–G6.

## Not verified

- any real Groq request from this branch, its latency or accuracy;
- real free-tier rate-limit responses and headers;
- VAD thresholds on real microphones and rooms;
- the preview's appearance in the Caelestia shell (no QML linter or shell
  run was available; QML was checked by contract tests only);
- any deployment. Nothing was deployed or enabled on Mirai or Tachibana.
