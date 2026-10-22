# Corrective implementation validation

This correction follows the complete rejected review in
`reconciled-review-result.txt`, which independently reviewed `62b6058` against
`56d68a1`. Work remains on `feature/live-transcription-20261021`. The earlier
report's claim that this branch was still at `8342a52` was stale.

**Release remains BLOCKED.** This change does not complete the approved
incremental-preview release: a production nonblocking transport integration
and independent acceptance are still missing. The ordinary synchronous Groq
SDK now fails closed for previews; full-recording batch finals still work.
Do not enable or deploy previews on the strength of these offline results.

## Implemented and tested

- A shared process-local dispatch scheduler serializes stop/final revocation
  with nonblocking transport initiation. Epochs are captured before reserving
  windows. Even a final that starts and finishes while a request is queued
  revokes that request. Stop never acquires the core/VAD or ledger lock.
- The scheduler requires `start_preview_transport(**request)` to initiate
  transport before returning a Future, without awaiting a response. Ordinary
  SDK `create` does not satisfy this contract and is never used as a fallback
  by a recording session. Only offline fakes implement the new primitive here.
  A test fake records its simulated HTTP send synchronously under the scheduler
  lock and completes its response on another thread. Merely putting SDK work
  on a thread would reproduce the rejected race and is not an implementation.
- UI publication carries session identity to the consumer. GTK queues a call
  that rechecks identity at consumption, then mutates UI under the same lock
  used to change identity. The native bridge uses the same gate. Native stdout
  publication takes its snapshot inside the write lock, preventing an older
  snapshot from following a newer snapshot. Main never routes cloud partials
  through the generic local-preview setter.
- Stable suffix/prefix overlap, including `ends here` + `here comes`, is
  deduplicated. Contained short hypotheses do not remove committed trailing
  words. Chinese characters align without inserted spaces. Emphatic `no`
  repeats and tested boundary punctuation remain. Text-only overlap cannot
  infer the timing of every possible intentional repetition.
- Queue entries carry absolute sample positions. A/B before dropped C are
  consumed before the discontinuity; D starts after the hole. Completion of
  an older in-flight window cannot clear a newer discontinuity.
- Numeric/string Retry-After values retain their entire duration, including
  7200 seconds across new sessions and ledger restart. The existing bounded
  worker polling interval does not truncate the persisted deadline.
- A 129-tap Kaiser-windowed sinc FIR precedes downsampling from 48/44.1 kHz.
  Filtering and interpolation run on the request worker. Both rates meet the
  test threshold of 60 dB rejection of a 12 kHz tone relative to a preserved
  1 kHz tone. Six-second conversion has a generous one-second test ceiling;
  it is a workload bound, not a real-time scheduling guarantee.
- Existing hotkeys, configuration keys, full final audio, batch models and
  explicit local selection remain. Test fixtures now drain ghost executors
  before removing temporary state, fixing an observed cleanup/write race.

## Offline evidence

Interpreter:
`/home/qwqc/.local/state/loom-fleet/validation-venvs/protocol7/bin/python`.

```
PYTHONDONTWRITEBYTECODE=1 <interpreter> tests/run_offline_preview.py
<interpreter> tests/test_groq_chunks.py --benchmark
git diff --check
```

The guarded runner contains all 185 baseline tests plus 20 new deterministic
adversarial tests. It redirects home/config/history into scoped temporary
state, blocks outgoing socket connection/sendto methods and mocks the real
Groq transcription SDK entry point. Local SQLite restart/process tests remain
included. It never loads user credentials or invokes a real provider.

Two separate guarded processes passed all **205 tests** (185 baseline scenarios
plus 20 added tests): 46.271 seconds after the Chinese-punctuation change, and
53.065 seconds after the final repeated-word punctuation refinement. Both
reported **zero forbidden network/SDK attempts**. Fourteen Python files compile
without writing bytecode, and `git diff --check` passes. These are lead-run
fresh-process checks, not an independent reviewer PASS.

A prior run failed on the stop-idempotence regression introduced during this
iteration; that was fixed. Another exposed a pre-existing ghost-test cleanup
race (`Directory not empty`) because status became visible before persistence
finished; executor cleanup now waits before deleting fixtures. Neither failed
run is counted as a successful suite.

Synthetic seeds 7/11/23/42 use virtual time and generated responses. They do
not measure cloud WER, latency, cost, VAD on microphones or device rendering.
The acceptance bounds remain preview WER <=14%, adjacent duplicates <=3,
revision depth <=8, first virtual preview <=6 s, mean updates <=7 s, zero
silent requests, and <=12 requests/minute under the simulation budget.
Synthetic preview WER for seeds 7/11/23/42 was 0.063/0.045/0.034/0.098,
with adjacent duplicate counts 0/1/0/0 and maximum revision depth 3 tokens.
Virtual first updates were 4.38/4.42/4.69/4.04 seconds. These measurements
precede the final punctuation-only refinement; the complete guarded suite
reruns and passes the same benchmark acceptance assertions afterward.
Measured capture enqueue overhead in this run was 1.04 microseconds/call;
16 kHz window/WAV preparation was 0.23 milliseconds. These are local samples,
not device or production guarantees.

## Explicit invariants and remaining blockers

1. In a supported transport, dispatch initiation and stop/final registration
   have one total order. Revocation first means no initiation. Initiation first
   means already in flight; it cannot be retracted or refunded merely because
   stop follows. Waiting on a response occurs outside lifecycle locks. Tests
   pause immediately before the scheduler boundary, exercise final completion,
   contention, core-lock contention, queued UI work and in-flight callbacks.
2. No production Groq adapter meeting that nonblocking initiation contract is
   implemented or verified here. Consequently the synchronous production SDK
   cannot send previews, even when assertions are set. Future adapters require
   an actual HTTP-boundary test; a callable attribute or fake Future alone is
   not production evidence. Multi-process transport/final serialization also
   needs independent verification beyond the process-local scheduler and
   durable ledger checks. This is a P1 release blocker, not a completed feature.
3. Actual Groq FREE organization status/no-charge protection is unverified.
   Endpoint/model allowlists and assertions do not prove billing protection.
   Mirai/Tachibana receive separate halves of the supplied organization limits;
   the ledger does not discover other consumers or coordinate host usage.
   Unknown producers, paid usage, stale assertions or missing history must
   keep previews disabled. Unrestricted existing finals can exceed a partition;
   no total-cost guarantee is claimed or proved by these offline tests.
4. No microphone, live provider/header/429, native QML rendering, GUI interaction,
   device deployment, push, merge or SUMI edits occurred. Loom task-card tools
   were unavailable. No independent review PASS is claimed.

## Exact Git and delivery status

The staging attempt failed with:
`Unable to create .../.git/index.lock: Read-only file system`.
No files were staged and no commit was created. HEAD remains
`62b60586bdbfb733f504979caacc8fa5cefb0223` on
`feature/live-transcription-20261021`; all fifteen files below are scoped
working-tree changes. No push, merge, device changes or deployment occurred.
The supervised pipeline must not treat these offline results as a release PASS.

Changed files:

- `streaming_stt.py`
- `preview_quota.py`
- `preview_publication.py` (new)
- `main_linux.py`
- `native_bridge.py`
- `ui_linux.py`
- `tests/run_offline_preview.py`
- `tests/test_release_corrections.py` (new)
- `tests/test_groq_chunks.py`
- `tests/test_streaming_stt.py`
- `tests/test_streaming_integration.py`
- `tests/test_ghost_jobs.py`
- `tests/test_queue_send.py`
- `docs/live-transcription.md`
- `docs/live-preview-corrective-validation.md`
