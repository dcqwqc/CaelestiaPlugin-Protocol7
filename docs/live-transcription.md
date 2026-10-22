# Protocol 7 Groq live preview

Development candidate on `feature/live-transcription-20261021`. **Release
blocked; no deployment or provider traffic has been performed.** Tests use
synthetic audio, fake clients and the installed SDK type with mocked TLS.
Real latency, accuracy, billing protection,
provider headers and microphone behavior remain unverified.

Previews are display-only overlapping Whisper windows. The complete original
recording still goes to the existing batch final path once per attempt;
preview text never enters paste, rewrite or history. Existing final model
selection and fallback behavior are retained. No account plan is upgraded.

## Fail-closed configuration

The default switch is off. Turning on the shell's experimental switch alone
is insufficient. Backend configuration requires all of the following:

| Key | Default | Meaning |
| --- | --- | --- |
| `streaming_stt_enabled` | `false` | Explicit opt-in |
| `streaming_stt_provider` | `groq-chunks` | Only supported provider |
| `streaming_stt_preview_interval_seconds` | `5.0` | Clamped to 4–6 seconds |
| `streaming_stt_free_tier_verified` | `false` | Operator assertion/consent: provider confirms free-tier organization and protection against charges/automatic paid upgrades |
| `streaming_stt_quota_partition_verified` | `false` | Operator assertion: exactly two organization producers, each with its own half allocation, and all their finals/previews accounted by their host ledger |
| `streaming_stt_verified_at` | `0` | Unix timestamp of verification; expires after 24 hours, including during a recording |
| `streaming_stt_quota_ledger` | empty | Absolute path to durable SQLite ledger, shared by every backend process on this host |

`use_groq: true`, a key, and a selected `groq_model` of
`whisper-large-v3` or `whisper-large-v3-turbo` are also required. The transport
checks the allowlist again. The production preview adapter accepts only the
standard `https://api.groq.com/` SDK base URL; custom bases fail closed. Unsupported
models remain usable by the existing batch path; they cannot create previews.
Old OpenAI Realtime settings do not enable this feature.

**Actual account billing tier is an EXTERNAL FACT that only Groq/provider can
confirm.** Neither a hostname, an API key, a local counter nor a boolean proves
zero cost. These assertions must not be set without independent evidence.
Unknown/stale protection means no previews. Billing and quota verification
remain unresolved external release gates; this implementation is not sign-off.
No credentials or configurations were changed during offline validation.

## Durable conservative quota policy

The policy ceiling supplied for Whisper is 20 RPM, 2000 RPD, 7200 audio
seconds/hour and 28800/day per organization. Provider limits must be checked
before rollout; no live documentation or endpoint was accessed in this work.
Every reservation counts at least 10 audio seconds, including finals. Timeout
and failure reservations are retained; only definitely unsent previews refund.

There is no cross-host service. Each of the two hosts gets half of every
ceiling: 10 RPM, 1000 RPD, 3600 seconds/hour, 14400/day. Previews may consume
only 4 RPM and 40% of the other host ceilings, **with local final usage included**.
Thus preview admission leaves at least 6 RPM, 600 RPD, 2160 seconds/hour and
8640/day of that host allocation for finals. With both hosts active, preview
admission is at most 8 RPM in aggregate. This lower cadence is intentional.
No unused allocation is borrowed from the other host. A third producer,
different ledgers for processes on one host, or unknown organization consumers
invalidates the partition assertion and must disable previews.

SQLite transactions serialize reservations across processes. Events,
cooldowns, strikes and final registrations survive restarts. A new/missing
ledger warms up for 24 hours while finals are recorded. A crash during a final
leaves a conservative active-final marker; it cannot silently restore capacity.
Corrupt state, clock rollback, disk errors or contention suppress previews.
An accounting failure creates a durable `.unsafe` flag, preventing another
process or restart from forgetting an unaccounted final. Recovery requires
operator investigation and a fresh 24-hour accounting window, not deleting
state to regain capacity. No permanent service is installed.

Finals always proceed, including when the ledger is unavailable or the host's
allocation is already exhausted. Bookkeeping waits at most 25 ms for SQLite;
there is no wait for a preview request or its UI callback. Therefore **unbounded
final traffic can still exceed provider limits**. The code bounds preview
admission under the verified two-producer policy; it cannot guarantee a total
organization bound while also preserving unrestricted finals. Other clients,
quota changes and missing provider counters remain external uncertainty.

Both final and preview responses feed throttling when headers are available.
429 retains the reservation, applies the full retry-after duration (minimum 60 seconds, with no one-hour truncation), doubles
cooldown on repeated strikes and durably disables previews after three strikes
in a day. Low request/audio counters reserve capacity for finals; unknown
headers never increase local capacity. Final request payloads, models and
success/failure semantics remain unchanged; raw SDK responses are used when
available solely to inspect headers. No full recording is shortened.

## Capture, speech and text

Capture enqueues into a bounded queue. Overflow drops preview samples only.
The worker keeps a bounded audio ring, initially proposes a window after
4 seconds, then up to 6-second windows with 1-second overlap. Each needs
0.6 seconds of speech and 0.5 seconds of new speech. Quota policy can reduce
cadence or suppress all previews. Idle/session/request limits remain bounded.

The dependency-free detector combines a -60 dBFS absolute threshold,
speech-band energy (90–4000 Hz), normalized periodicity, zero-crossing rate
and dense sustained runs of at least 210 ms. Startup no longer learns initial
speech as background. Synthetic tests cover immediate and quiet sustained
vowels, hum, energetic broadband/colored noise, taps and blips. **This heuristic
is not a validated real-microphone VAD**: tonal noise may pass and unvoiced
speech may be missed. It gates previews only.

Segment confidence and known decoder-artifact filtering apply to previews.
Stable suffix/prefix words and Chinese character overlaps are aligned;
contained short results never remove later committed words. Emphatic boundary
repeats use absolute word timestamps when supplied: a disjoint later word
is preserved, while a shared word is aligned. No word-specific exceptions
remain. Without timestamps, ambiguous isolated/repeated-token boundaries
remain conservative; arbitrary intentional repetition cannot be proved from
text alone. Only the last 8 tokens can change and
text is bounded to 300 tokens. Chinese characters count as individual tokens.
Gaps are marked with an ellipsis. A worker-side 129-tap windowed-sinc low-pass
filter precedes 48/44.1 kHz downsampling to 16 kHz; capture only queues audio.

## Dispatch and UI consumers

Stop/final registration changes a synchronized scheduler epoch. Queued windows
retain their original ticket and cannot initiate transport after revocation,
even if a final has already completed. Each current-version backend sharing
the host ledger also checks a persisted epoch and final count under the same
SQLite transaction as its first payload write. Final registration increments
that epoch in a transaction. Ledger -> local scheduler is the lock order;
stop takes only the local scheduler lock, never the ledger or VAD lock.
Different ledgers or mixed old/new backend versions are not coordinated.

The production adapter reuses the selected Groq SDK client's credentials,
model and standard endpoint. It prepares multipart WAV and performs TLS setup
on the existing request worker. The first **nonblocking TLS payload write
attempt** is the dispatch boundary. Encoding, DNS/TCP/TLS setup, readiness
waits, response reads and callbacks never hold the lifecycle lock. No thread
launch masquerades as HTTP initiation. TLS setup may occur after revocation,
but no transcription payload can start then. No automatic retry or redirect
is performed. Word and segment timestamps are requested in verbose JSON.

TLS can emit ciphertext before returning SSLWantWrite/SSLWantRead. Therefore
the first guarded write attempt conservatively makes the request already in
flight, even if no byte count is returned. Subsequent writes finish that same
request outside lifecycle locks; they are not new requests. Stop/final cannot
retract already started traffic, and its quota reservation remains charged.
A ledger commit failure after initiation poisons future previews and never
refunds the possibly sent request. Obsolete responses cannot update the UI.

The existing full-recording final SDK path remains available with previews off
or unavailable. Tests use the installed SDK type, mocked TLS writes and
responses, lifecycle barriers and separate ledger instances/processes. These
prove offline wiring and ordering, not live Groq acceptance, billing status,
production scheduling latency or microphone accuracy. Independent acceptance
and verified FREE organization eligibility are still release gates.

Partials carry session identity through main_linux to the actual UI consumer.
The GTK consumer queues to its main loop and checks identity atomically with
UI mutation; the native bridge applies the same identity gate. Binding another
recording invalidates old queued callbacks. Native state snapshots serialize
with stdout writes. These are offline consumer checks, not visual QML evidence.

## Offline validation

Use the private validation interpreter requested for this task:

```
/home/qwqc/.local/state/loom-fleet/validation-venvs/protocol7/bin/python -m unittest \
  tests.test_groq_chunks tests.test_streaming_stt tests.test_streaming_integration \
  tests.test_streaming_plugin_config tests.test_preview_corrections tests.test_release_corrections
# Broader regressions with process-wide network guards and temporary state:
/home/qwqc/.local/state/loom-fleet/validation-venvs/protocol7/bin/python tests/run_offline_preview.py
```

The corrective tests add restart, simultaneous processes, two-host partition,
ledger failures, final 429/audio headers, safety assertions, VAD, repetition,
encoding races and slow callback cases. They use temporary local ledgers and
fake SDK clients. Network is blocked in these suites.

`tests/test_groq_chunks.py --benchmark` is a virtual-time simulation with
mocked latency/responses, not real cloud accuracy or latency. Its original
3.4–9.8% word-error figures predate conservative repetition handling and do
not describe this revision. New results belong in the local validation report.
The benchmark exercises the algorithm with an in-memory budget, not the
production durable two-host admission policy.

Real free-tier protection, actual shared organization usage, provider behavior,
microphones and Caelestia visual delivery have not been verified. There has
been no device rollout. Release requires resolving the blockers above and
independent review. Keep previews disabled in the meantime.
