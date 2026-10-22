# Corrective implementation validation

This report supersedes the stale reports attached to `62b6058` and `d3b90a4`.
The complete original rejection at `reconciled-review-result.txt` and the
user-supplied independent round-one rejection were read. Corrections remain
on `feature/live-transcription-20261021`, based on clean reviewed HEAD
`d3b90a455992740fa23cf5133fa965285b7fcddc`.

**Implementation candidate; release remains blocked pending independent PASS
and external FREE-tier verification.** A production-selectable transport is
now implemented and exercised offline with the installed SDK type and mocked
TLS sockets. No provider requests or paid STT occurred during those isolated test runs.
Subsequent deployment, Git push and SUMI records are tracked in their own
transactions; no account Free-tier certification is claimed here.

## Round-one corrections

- The ordinary Groq SDK client supplied by `WhisperEngine.groq_preview_client`
  now selects `preview_transport.py`. It reuses the existing key/model and
  standard HTTPS endpoint. Multipart WAV, word/segment timestamp requests and
  the installed SDK resource's `/openai/v1/audio/transcriptions` path are
  exercised. Real `http.client.HTTPResponse` parses mocked TLS byte streams;
  a fake response object no longer hides HTTPConnection state assumptions.
  Existing full-recording batch finals still use the unchanged SDK path.
- Preparation, DNS/TCP/TLS setup, readiness waits, response reads and callbacks
  occur outside lifecycle locks on the request worker. The first nonblocking
  TLS payload write attempt passes through the synchronized lifecycle gate.
  No new dispatcher thread or service is introduced.
- Shared-ledger schedulers capture both local and durable epochs. Final
  registration increments the durable epoch in a SQLite transaction. The
  transaction surrounding the first payload write checks that epoch and the
  shared final count, closing admission-to-dispatch races across current
  backend instances/processes sharing one host ledger. Old/new mixed backend
  versions and different ledgers are outside this coordination contract.
- No hardcoded `no`/`yes` exceptions remain. Shared suffix/prefix `no more` and
  `yes to that` align correctly. Absolute recording word intervals distinguish
  a shared boundary word from a later intentional repetition, retaining its
  punctuation. Without timestamps, isolated or repeated-token boundaries
  remain conservative; text alone cannot establish every speaker intention.
  Contained hypotheses, Chinese overlap and frozen-prefix tests remain.
- This document now records actual base revision and failed commit delivery,
  rather than claiming an obsolete HEAD or an earlier uncommitted file set.

## Explicit ordering and cost invariants

1. Stop and local dispatch share one scheduler lock. Stop never takes the
   core/VAD or ledger lock. A queued ticket revoked before the first TLS write
   attempt cannot initiate a transcription payload. TLS connection preparation
   alone may occur after revocation; it contains no transcription payload.
2. Across supported backend processes, the ledger transaction orders final
   registration and first payload write. A final that starts and finishes
   between admission and dispatch still invalidates the old ticket. Lock order
   is ledger -> local scheduler; finals never wait for a response or UI callback.
3. A TLS write can emit ciphertext before returning SSLWantWrite/SSLWantRead.
   Its first guarded attempt therefore counts conservatively as already in
   flight. Readiness retries and remaining writes complete that same request
   outside lifecycle locks. Stop/final cannot retract it; no unsent refund is
   claimed. A failed ledger commit after initiation poisons future previews
   and retains the possibly sent request's reservation. Readiness errors alone
   do not poison the ledger.
4. Existing identity-aware GTK/main_linux and native consumers retain atomic
   identity checks at actual UI mutation, including queued and in-flight old
   callbacks. A new binding prevents recording N from overwriting N+1.
5. Existing sample-position markers retain A/B before dropped C and mark the
   gap before D. Older request completion cannot erase that discontinuity.
6. Full numeric/string Retry-After deadlines, including 7200 seconds, survive
   sessions and ledger recreation. Bounded worker polling does not shorten
   cooldowns. A 129-tap Kaiser-windowed sinc FIR precedes 48/44.1 -> 16 kHz
   conversion on the worker, never the capture thread.
7. Previews remain default-off and require fresh explicit FREE-tier and quota
   partition verification plus warmed durable history. Assertions, hostname
   and model allowlists do not prove account billing eligibility. Mirai and
   Tachibana each retain half of supplied organization ceilings, with no
   borrowing. Unknown consumers or paid usage invalidate verification.
   Unrestricted existing finals can exceed allocations; no total-cost
   guarantee follows from these offline tests. Finals work with previews off.

## Offline verification

Interpreter used:
`/home/qwqc/.local/state/loom-fleet/validation-venvs/protocol7/bin/python`.

```
PYTHONDONTWRITEBYTECODE=1 <interpreter> tests/run_offline_preview.py
```

The runner retains all 185 baseline scenarios and the prior 20 corrective
regressions, adding 16 round-one adversarial tests: **221 tests total**.
Sockets' outgoing connect/send methods and the Groq transcription SDK entry
point are blocked; production-path tests use an actual SDK instance with a
mock TLS socket. Home/config/history are redirected to scoped temporary state.
Local ledger persistence and process tests are included, not stubbed out.

Final fresh-process rerun results are recorded below after execution.

New cases cover installed-SDK/engine selection, real HTTP response parsing,
stop and completed-final barriers immediately before payload write, eight-way
production dispatch contention, already-in-flight response completion,
TLS readiness continuation, independent ledger instances, another process's
completed final, transport/ledger failure classification, and timestamped
shared versus intentionally repeated boundary words. Existing UI identity,
full-original-audio final, cancellation, focus, hotkey/config contracts,
7200-second restart and dropout/anti-alias regressions remain included.

A synthetic benchmark under socket/SDK guards returned zero forbidden attempts.
Seeds 7/11/23/42 produced preview WER 0.063/0.045/0.034/0.110, adjacent extra
duplicates 0/1/0/1, maximum revision depth 3 tokens, first virtual update
4.38/4.42/4.69/4.04 seconds, and mean virtual cadence
5.57/5.48/6.06/6.34 seconds. No silent window was sent; peak request rate
was 11/minute under the simulation budget. Acceptance limits remain WER <=14%,
extra duplicates <=3, revision depth <=8, first virtual update <=6 seconds,
mean cadence <=7 seconds, and rate <=12/minute. This simulation uses the
in-memory benchmark budget, not the stricter durable 4/minute host cap.

Local capture enqueue measured 0.99 microseconds/call and 16 kHz window/WAV
preparation 0.18 milliseconds. Six-second conversion at both device rates has
a one-second test ceiling. Alias tests require at least 60 dB suppression of
12 kHz relative to preserved 1 kHz speech-band audio. These are synthetic test
bounds/local samples, not production latency, cloud WER, billing, microphone
VAD, operating-system scheduling or visual/native rendering guarantees.

Earlier development runs: 215 tests exposed the adapter URL error (fixed);
216, 219 and 221 tests subsequently passed with zero forbidden attempts.
Those preceded the final real-HTTP-parser refinement and are not substituted
for the final reruns. Lead reruns are not independent acceptance review.

## Exact Git and delivery status

The earlier `d3b90a4` correction was committed before the independently
rejected review. The next Groq production-transport correction was committed
and pushed as `ac59669bd611b6d30bfdac7c0af969659f6142fb` on the existing
feature branch. An independent review of that revision rejected a remaining
RFC 7231 HTTP-date `Retry-After` cooldown defect. This follow-up corrects
that parser and adds bounded numeric/date-header and durable-restart tests.
The authoritative current delivery SHA is the Git HEAD/remote feature-branch
SHA and each per-device transaction receipt, not a hardcoded historical
checkpoint in this document. Production previews remain disabled until
provider Free plan/organization usage verification; synthetic tests do not
establish real microphone performance.

Relevant scoped implementation files include:

- `streaming_stt.py`
- `preview_quota.py`
- `preview_transport.py` (new)
- `tests/run_offline_preview.py`
- `tests/test_review_round1.py` (new)
- `docs/live-transcription.md`
- `docs/live-preview-corrective-validation.md`

The original development/test runs did not modify production user settings,
credentials or paid transcription state. A later separately authorized rollout
installed the `ac59669` candidate on Mirai (previews OFF), preserving its
previous release and rollback transaction. That installation is not proof that
this newer follow-up commit has been installed. Tachibana remained unreachable
at the latest documented check. Per-host release receipts and live IPC checks
are authoritative for current device deployment, not this historical report.
No verified Groq Free-tier eligibility or real microphone acceptance is claimed.
