# Corrective implementation validation

Scope: local corrective iteration based on the independent interim review in
`pre-review-result.txt`, against committed candidate `8342a52`, on the same
`feature/live-transcription-20261021` branch. No deployment, merge or remote
push is authorized or performed. This is **not release sign-off**.

Implemented corrections:

- Default-off previews now require fresh explicit no-charge protection and
  two-producer quota assertions, an allowlisted Whisper model and a healthy
  warmed durable ledger. Those assertions are consent and externally verified
  prerequisites, not proof of provider account status.
- SQLite reservations serialize local processes. Two hosts each receive half
  of the supplied organization limits; preview admission uses only 4 RPM and
  40% of each host's other ceilings, counting finals and a 10-second minimum.
  Events, cooldowns, strikes and final markers survive backend restart. Missing
  history warms for 24 hours; corruption, clock rollback or contention fail
  closed. A durable unsafe flag covers accounting failure. No service added.
- Configured ledgers retain final accounting with the switch off. Nested
  ghost/engine priority scopes count one attempt. Final success/error headers
  update preview throttling. Full final audio, selected final model, JSON
  response, rewriting and fallback semantics are retained.
- Dispatch admission rechecks stop, final priority, cooldown and assertion
  expiry after expensive client lookup/WAV encoding. Callbacks execute outside
  lifecycle locks and the application rejects inactive session delivery.
- Speech detection handles initial/quiet sustained voiced signals without
  learning them as noise, and rejects tested energetic colored/broadband noise.
  Text reconciliation preserves nonoverlapping and ambiguous one-word repeats.

Validation interpreter:
`/home/qwqc/.local/state/loom-fleet/validation-venvs/protocol7/bin/python`.

Reproducible commands:

```
python -m py_compile config.py main_linux.py streaming_stt.py preview_quota.py whisper_engine.py \
  tests/test_groq_chunks.py tests/test_streaming_integration.py \
  tests/test_preview_corrections.py tests/run_offline_preview.py
python tests/run_offline_preview.py
python tests/test_groq_chunks.py --benchmark
git diff --check
```

The guarded runner covers all 110 original preview tests, 17 additional
corrective regressions and 58 compatible batch/focus/queue/Enter regressions:
**185 tests passed**. Its selected suites are listed in the runner. Temporary
state redirects configuration/history and home-derived paths; process-wide
socket guards fail the run on an unexpected connection attempt. Concurrent
process tests use local temporary SQLite files. Compilation and whitespace
checks pass. No real-provider accuracy, latency or microphone tests occurred.

Two further older suites (`test_hotkey_recognition`, `test_ghost_hotkey_cancel`)
could not import because this venv lacks `evdev`. They are not represented as
passed. No dependency or device installation was attempted.

An earlier broad run exposed the original test helper's module-isolation bug:
replacing `sys.modules['whisper_engine']` invalidated another suite's patch
and caused a failed real-SDK request attempt with its literal dummy key
(`APIConnectionError`). No user key was loaded for that test and no successful
provider response was observed. The helper now loads its engine privately;
the final guarded runner additionally prevents all connections. The earlier
run also attempted a history write that the filesystem rejected; the guarded
runner redirects all state to a temporary directory. These earlier attempts
are not counted as successful offline validation.

Synthetic benchmark, seeds 7/11/23/42: preview word error ratios
0.076/0.067/0.057/0.134 versus naive 0.253/0.281/0.205/0.244; adjacent duplicate
counts 1/3/2/3; virtual first updates 4.38/4.42/4.69/4.04 seconds. Original
single-word deletion expectations were updated to preserve intentional repeats;
benchmark bounds now allow 14% simulated word error and three ambiguous
boundary duplicates. All original test scenarios remain. These figures use an
in-memory simulation budget and mock latency, not production durable admission
or cloud measurements.

Unresolved blockers:

1. Only the provider can confirm actual organization billing tier and
   no-charge protection. No evidence was obtained here. Organization consumer
   completeness and two-host partition assertions also require verification.
   Unrestricted finals cannot be globally capped without changing the required
   always-proceed batch behavior; previews alone are bounded by this policy.
2. The synchronous SDK still has a gap between synchronized dispatch admission
   and actual HTTP transport. A previously admitted call may send after stop
   returns or final registration. Holding a lock through the request would
   violate nonblocking stop/final requirements. A cancellable transport design
   and independent adversarial review remain necessary.
3. An already executing UI callback cannot be withdrawn. Atomic epoch-aware
   publication at the UI consumer is not implemented. Session checks suppress
   ordinary late delivery but are not proof of atomic publication ordering.
4. No real microphones, provider limits/headers or Caelestia visual behavior
   were checked. Keep previews disabled pending these gates and independent
   final review. No Mirai/Tachibana rollout was performed.

## Local commit blocked by filesystem permissions

The requested local commit was attempted after validation. Both staging and
commit failed because this workspace exposes `.git` read-only:
`Unable to create .../.git/index.lock: Read-only file system`.
The branch remains `feature/live-transcription-20261021` at `8342a52` and all
corrective files remain in the working tree. No index, branch ref, remote or
deployed device was changed. A writable Git checkout is required to create
the authorized commit; no approval escalation is available in this session.
