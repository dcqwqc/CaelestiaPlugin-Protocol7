# Live Transcription Acceptance Note

Status: **pending hardware verification**. This note is not a release approval.

## Scope

This note covers the live (streaming) transcription feature in commit
`7c4ca4f`, which is already on the feature branch. It adds no new
implementation. It records what has been validated so far, what has not, and
which gates remain before release.

This document alone does not approve a release. Acceptance is decided by
independently configured host checks and the hardware verification described
below.

## Validation history

- **Previous auto-review: rejected.** The test suite could not run because the
  system Python interpreter lacked NumPy. This was an environment failure. It
  was not a test failure, but it also produced no evidence that the feature
  works.
- **Re-run in a private sandbox: passed.** NumPy 2.5.3 was installed into an
  isolated validation virtual environment. System Python was not modified.
  The streaming test suite then ran, and **41 tests passed**. All of these
  tests use mock or offline audio and transcription backends.

No live paid speech-to-text service was called during validation. No timing,
latency, or accuracy measurements were collected. This note claims none.

## Behavior covered by the passing tests

1. **Incremental partial transcripts.** While capture is active, partial
   transcript text is emitted as audio arrives. Partial results may be revised
   as more audio is processed.
2. **Stop-time completed transcript.** When capture stops, the pipeline
   produces one completed transcript for the session. Downstream consumers
   should treat this as the authoritative result, not the last partial.
3. **Groq fallback.** When the primary path is unavailable, transcription falls
   back to the Groq backend. Tests check this using mocked responses only.
4. **Opt-in paid endpoint disabled by default.** The paid streaming endpoint
   requires explicit opt-in and is off in the default configuration. Tests
   confirm the disabled default. The live endpoint itself was not exercised.

## Deployment status

- No production deployment has been made.
- No paid STT provider has been billed as part of this validation.
- The feature remains on the feature branch
  (`feat/native-caelestia-panel-v2` lineage) pending the gates below.

## Acceptance gates

All gates must pass before release. Record evidence for each one, such as a
command, host, commit, and outcome. Do not mark a gate based on this note.

- [ ] **G1 Reproducible tests:** The 41 streaming tests pass on an
      independently configured host, at commit `7c4ca4f` or a descendant, in a
      documented environment with NumPy declared as a dependency, not assumed
      from system Python.
- [ ] **G2 Dependency declaration:** NumPy is listed in the project's
      dependency or test requirements, so a clean environment installs it.
- [ ] **G3 Default safety:** A fresh install with default configuration makes no
      requests to the paid endpoint.
- [ ] **G4 Fallback on real network:** Groq fallback is exercised against the
      real service by a deliberate, authorized test. Any cost is acknowledged
      in advance.
- [ ] **G5 Mirai hardware verification:** See below.
- [ ] **G6 Tachibana hardware verification:** See below.
- [ ] **G7 Reviewer sign-off:** A human reviewer accepts the recorded evidence
      for G1 through G6.

## Remaining hardware verification

### Mirai (portable laptop, CachyOS)

- Probe reachability first:
  `ssh -o ConnectTimeout=5 mirai hostname`. Mirai may be asleep or offline.
- Do not build on Mirai. Install a build produced elsewhere.
- With the real built-in microphone, confirm that:
  - partial text appears during speech;
  - the completed transcript appears after stop;
  - text injection lands in the focused window.
- Confirm that silent or unvoiced input does not inject spurious numeric text.

### Tachibana (desktop, CachyOS / Windows dual-boot)

- Inspect state first: `ssh tachibana uname -a`. Linux is required for this
  check.
- Under Hyprland/Wayland, repeat the Mirai checks with Tachibana's audio
  input. Confirm that the history and enter injection paths behave correctly.
- Capture a screenshot (`machine tachibana screenshot`) as evidence of the
  panel during a partial transcript and after the completed transcript.

## Not verified

The following have not been verified:

- live paid endpoint behavior;
- real-network Groq fallback;
- latency and accuracy;
- behavior on physical audio hardware;
- production deployment.

Treat each of these as unknown until its gate is recorded as passed.
