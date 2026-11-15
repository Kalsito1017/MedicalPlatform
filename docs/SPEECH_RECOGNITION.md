# Speech recognition and voice scheduling

**Status:** implemented local path (browser → FastAPI → FFmpeg → private
whisper.cpp), with no claim of production readiness. The automated tests cover
the API, binding, conversion, cleanup, and a mocked speech-service boundary;
real-browser microphone behaviour and whisper.cpp accuracy/latency are **not**
verified by the test suite.

The whisper.cpp image is pinned to release **v1.9.5** (source tarball SHA-256
`ff1a9053…0dc1720`) and the **Base multilingual** model `ggml-base.bin`
(SHA-256 `60ed5bc3…fba2efe`; 142 MiB on disk, ~74M parameters). The model is
baked into the image and verified at build time. CPU only; `base.en` is
deliberately not used because Bulgarian is a target language.

## Why this shape

The browser Web Speech API is not the engine. It is not consistently available,
may send audio to a vendor cloud, and gives no control over retention. The
demo instead uses:

1. `navigator.mediaDevices.getUserMedia({audio})` after an explicit click,
2. `MediaRecorder` with feature-detected MIME types,
3. an authenticated FastAPI route that bounds, converts, and forwards the audio,
4. a private self-hosted whisper.cpp service,
5. an editable transcript, then an AI **proposal** that the doctor must review,
6. a normal appointment `POST` after explicit confirmation.

## Data flow

```text
Browser
  getUserMedia -> MediaRecorder (in-memory chunks)
    |
    | multipart POST /api/v1/voice/transcriptions
    | file + language=auto|en|bg, CSRF cookie/header, session cookie
    v
FastAPI (app/api/v1/voice.py, app/services/voice.py)
  authn session -> authz appointment:write -> per-user limiter
  BodySizeLimitMiddleware: exact-route allowance (default 4 MiB, hard cap 8 MiB)
  stream to private temp dir (0700) / source file (0600)
  FFprobe duration -> reject > VOICE_MAX_DURATION_SECONDS (default 60)
  FFmpeg -> 16 kHz mono PCM WAV, fixed args, timeout
  bound converted size; verify with Python wave
    |
    | POST {WHISPER_BASE_URL}/inference (multipart WAV + language)
    v
Private whisper.cpp container (CPU, Base multilingual)
    |
    v
{text, language, model, duration_seconds}
    |
    v
Editable transcript -> POST /api/v1/appointments/interpret (AI proposal only)
    |
    v
Doctor reviews -> POST /api/v1/appointments (explicit confirmation, revalidated)
```

With `language=auto` the browser sends no language preference: whisper.cpp
detects Bulgarian or English from the audio and `verbose_json` returns
`language`, `detected_language`, and `language_probabilities`. The backend
reports the detected code in the response and uses it for the optional
transcription-correction check. Only Bulgarian and English are accepted: if the
model detects any other language, the backend rejects the request with `400`
and no transcript is produced. A manual `bg`/`en` choice always overrides
detection, so a doctor can correct a noisy or ambiguous recording.

Audio never reaches the LLM. The interpreter receives text only.

## Browser capture rules

- Feature detection: if `getUserMedia` or `MediaRecorder` is missing, the UI
  says so and keeps the manual transcript editor.
- MIME candidates in order: `audio/webm;codecs=opus`, `audio/webm`,
  `audio/ogg;codecs=opus`, `audio/mp4`; `isTypeSupported` is used when present.
- The primary UI is a single circular listening orb with idle, requesting,
  listening, processing, success and error states. Tapping it starts recording;
  tapping again stops and automatically uploads to the existing endpoint.
  **Cancel recording** discards without uploading.
- A Web Audio `AnalyserNode` drives the small waveform from actual microphone
  RMS while listening. If Web Audio is unavailable, a bounded CSS animation is
  used instead. The waveform does not use inline styles.
- Recording stops automatically at 60 seconds. The final `dataavailable`/`stop`
  events are awaited before microphone tracks are released, with a 1.5-second
  safety timeout. Tracks and the AudioContext are also released on cancel,
  clear, and page hide.
- Audio chunks stay in a JavaScript `Blob` in memory. There is no
  `localStorage`/`sessionStorage`, no IndexedDB, no background listening, and
  no upload until the user stops the recording. The blob is released after the
  upload attempt.
- The transcript is a normal editable `<textarea maxlength="2000">`. Manual
  typing works with no microphone at all.
- No audio object URL is retained; a new recording replaces the in-memory blob.

## Server limits and cleanup

| Control | Value | Notes |
|---|---|---|
| Global request body | 1 MiB | unchanged for every route |
| Voice route body | `VOICE_MAX_UPLOAD_BYTES` default **4 MiB** | exact `(POST, /api/v1/voice/transcriptions)` match; middleware clamps to a hard **8 MiB** ceiling |
| Audio duration | `VOICE_MAX_DURATION_SECONDS` default **60 s** | FFprobe before conversion and PCM frame count after conversion |
| Converted WAV | 16 kHz, mono, 16-bit PCM | bounded size; re-read with Python `wave` |
| FFmpeg/FFprobe timeout | `VOICE_FFMPEG_TIMEOUT_SECONDS` default 30 s | argument lists, never a shell; no user-controlled filenames/extensions |
| Temp storage | per-request `TemporaryDirectory` (`0700`), source `0600` | removed in `finally`; never a user-named path |
| Request rate | 12/min per user | in-process sliding window (same limitation as the AI limiter) |
| Speech call | `WHISPER_TIMEOUT_SECONDS` default 45 s | controlled `503` on timeout/unreachable/malformed |
| Transcript/audio retention | **none** | not logged, not audited, not stored in PostgreSQL or web storage |

Accepted media types are `audio/*` plus the practical browser containers
`video/webm`, `video/mp4`, `application/ogg`, and `application/octet-stream`.
The type header is only a fast check: FFprobe/FFmpeg must decode the bytes, and
the converted file is verified before it is sent onward.

**MediaRecorder WebM without a duration tag.** Browser `MediaRecorder` writes a
streamed WebM and often omits the container/stream duration (`N/A`). This is
valid audio. The server tries the format duration, then the audio-stream
duration, then the last packet timestamp. If all metadata is absent, it decodes
at most the accepted duration plus a small margin and rejects the recording from
the converted PCM frame count if it is too long. This prevents both the old
false “could not be read as audio” error and silent truncation of over-limit
recordings.

## Private speech service

`docker-compose.yml` defines an optional `speech` service with the `speech`
profile and **no published port**. The image:

- builds whisper.cpp v1.9.5 from the checksum-verified release tarball,
- downloads `ggml-base.bin` and verifies its SHA-256 before copying it into the
  runtime image,
- runs as a non-root user, CPU only (`--no-gpu`), 2 threads by default
  (`WHISPER_THREADS`),
- serves `/inference` on port 8080 inside the compose network.

Enable it locally:

```bash
# 1. Build/start the private speech service (first build downloads ~142 MiB model
#    and compiles whisper.cpp; this takes several minutes).
docker compose --profile speech up --build

# 2. Compose already points the app at http://speech:8080 by default (override
#    WHISPER_BASE_URL in the repo-root .env only for another private origin).
```

The backend must be restarted after changing `WHISPER_BASE_URL`. Without the
speech profile the app still starts; the transcription endpoint returns 503.
Verify the service from inside the network (the port is intentionally not
published):

```bash
docker compose exec speech curl -fsS http://127.0.0.1:8080/ >/dev/null && echo speech-ok
docker compose exec app python -c "import os; print(os.environ.get('WHISPER_BASE_URL'))"
```

If the speech service is absent or `WHISPER_BASE_URL` is unset, the endpoint
returns `503 dependency_unavailable`; manual appointment scheduling and the
editable transcript path keep working.

### Authentication modes

| `WHISPER_AUTH_MODE` | Behaviour | Intended use |
|---|---|---|
| `none` | no Authorization header | local compose private network |
| `bearer` | `Authorization: Bearer ${WHISPER_BEARER_TOKEN}` | a private service fronted by a shared-secret proxy |
| `gcp_iam` | fetches a Cloud Run ID token from the GCE metadata server with `WHISPER_AUDIENCE`; cached in-process | Cloud Run service-to-service (supported in code/docs, **not verified here**) |

Bearer tokens and ID tokens are secrets: never put them in the frontend, query
strings, logs, or audit rows. The backend redacts sensitive config fields.

### Cloud Run notes (not a deployment claim)

The current demo deployment target is Render + Cloudflare + Neon. Cloud Run
support for the speech image is documented for a later move only:

1. Build/push `speech/Dockerfile` to Artifact Registry.
2. Deploy it with **no public invoker** (private service).
3. Set `WHISPER_BASE_URL` to the service URL, `WHISPER_AUTH_MODE=gcp_iam`, and
   `WHISPER_AUDIENCE` to that URL.
4. Grant the backend runtime service account `roles/run.invoker` on the speech
   service.
5. Verify metadata-token fetch and IAM from the actual runtime before claiming
   it works. Cross-cloud IAM (Render → Cloud Run) is a known blocker; it is not
   claimed to work.

## Manual real-audio verification

Use a **synthetic/demo recording only**; this repository is not cleared for
real patient data. The checker exercises the same path as the browser: login,
CSRF, multipart upload, FFmpeg conversion, private whisper.cpp, and the returned
editable transcript. It never saves the audio or transcript.

```bash
# 1. Ensure the private speech profile and app are running.
docker compose --profile speech up -d
curl -fsS http://localhost:8000/health/ready >/dev/null && echo app-ready

# 2. Record a real sample (Linux/PulseAudio or PipeWire; macOS uses avfoundation).
ffmpeg -f pulse -i default -t 20 -ac 1 -ar 16000 /tmp/voice-sample.wav

# 3. Run the checker from the backend virtualenv.
cd backend
uv run python ../scripts/check_voice_transcription.py \
  --audio /tmp/voice-sample.wav --language bg --expect "Елена"

# Optional: also exercise the AI proposal (503 is a soft warning because
# transcription already succeeded).
uv run python ../scripts/check_voice_transcription.py \
  --audio /tmp/voice-sample.wav --language en --interpret
```

The checker reads `DEMO_DOCTOR_EMAIL` / `DEMO_DOCTOR_PASSWORD` from
`backend/.env`; it never prints or accepts the password on the command line.
Use `--email` / `--password-env` to override the account. `--expect` makes the
command fail when a phrase is missing, which is useful for repeatable checks.

A public whisper.cpp speech sample can validate the plumbing without a
microphone:

```bash
curl -fsSL -o /tmp/jfk.wav \
  https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav
cd backend
uv run python ../scripts/check_voice_transcription.py \
  --audio /tmp/jfk.wav --language en --expect "ask not"
```

The expected transcript is the JFK line beginning “And so my fellow Americans
ask not what your country can do for you…”. This checks real speech audio and
the private speech service; accuracy on Bulgarian/clinical vocabulary still
needs its own benchmark.

### Example request in the doctor UI

The voice card now has a **What to say** block with a complete example and an
**Insert example request** button. It is deterministic local copy (not an
LLM-generated suggestion), so it works with or without an LLM key. The example
switches between Bulgarian and English with the transcript-language selector.
A complete request names the patient (or MRN), the day/time, the duration or
visit type, and a short reason. Example:

> Book a 30-minute follow-up visit for Elena Georgieva, MRN-0001, next Monday
> at 10:00. Reason: blood pressure review.

The inserted text remains editable; nothing is created until the doctor reviews
the proposal and explicitly creates the appointment.

## AI proposal boundary

`POST /api/v1/appointments/interpret` calls the LLM with **`tools=[]`**. The
model receives the doctor's text and a clinic-local anchor date/time, and
returns strict JSON with a patient search phrase and local date/time. It never
receives a patient id, doctor id, or UTC timestamp as a choice.

The server then:

- resolves the patient only through the existing read-only MCP tool
  `search_authorized_patients` (the MCP tool count stays **seven**),
- converts Europe/Sofia wall time to UTC and rejects DST holes/folds,
- checks working hours, time off, doctor overlap, and patient overlap,
- returns a non-persisted proposal with `can_confirm` and warnings.

The proposal cannot create, reschedule, or cancel anything. Confirmation is a
separate `POST/PATCH /api/v1/appointments` that repeats authorization and all
validation under advisory locks. The AI is never the author of a scheduling
mutation.

**External LLM boundary.** Transcription is local to the private speech
service; the AI proposal step is separate. When `LLM_API_KEY` is configured
(or `LLM_ENABLED=true`), pressing **Propose appointment** sends the edited
transcript (which may contain a patient name) to the configured external LLM
provider. When no key is configured or `LLM_ENABLED=false`, the endpoint fails
closed with `503` and no external call is made. Do not enter real patient data
in any configuration.

## Threat notes

- **Prompt injection through a transcript**: the transcript is untrusted input;
  it is labelled as such in the prompt. The model has no tools and cannot emit a
  write. Deterministic input guardrails still apply.
- **SSRF**: `WHISPER_BASE_URL` is server-side configuration, validated to be an
  http(s) origin without credentials/path/query; it is never derived from a
  request.
- **Decompression/size abuse**: the upload cap applies to the compressed bytes,
  but duration and converted PCM size are separately bounded so a small,
  long-duration file cannot bypass the intent of the limit.
- **Path traversal**: uploaded filenames are ignored; the server uses fixed
  names inside a private temp directory.
- **Audio/transcript leakage**: no audio or text is logged or audited. Error
  messages to clients are generic; subprocess stderr is not echoed.
- **Microphone abuse**: the browser requests the microphone only after a click;
  the server sends `Permissions-Policy: microphone=(self)` and keeps camera,
  geolocation, and other sensors disabled.

## Verification checklist

- [x] `POST /voice/transcriptions` auth/permission/CSRF and empty/bad/oversized
      audio handling.
- [x] Real FFmpeg/FFprobe conversion and 16 kHz mono PCM verification in tests
      on the development host.
- [x] Temp directory cleanup after success and failure.
- [x] Mocked whisper.cpp success, 429, unreachable, malformed JSON, and
      not-configured `503` paths.
- [x] `language=auto` detection returns the detected `en`/`bg` code; a manual
      language always overrides detection, and any other detected language is
      rejected with `400`.
- [x] Global 1 MiB body limit preserved; voice route allowance is exact and
      hard-clamped.
- [x] AI interpreter `tools=[]`, patient resolution through the existing MCP
      tool, DST/conflict warnings, proposal-only behaviour, and explicit create.
- [x] Speech image builds from the checksum-verified release/model, reports
      healthy, and answers `/inference`.
- [x] Live local path: authenticated multipart upload → backend FFmpeg →
      private whisper.cpp Base → HTTP 200 with `{text, language, model,
      duration_seconds}`.
- [x] Real-speech checker `scripts/check_voice_transcription.py` ran the public
      whisper.cpp JFK sample through the live stack and returned the expected
      “ask not…” transcript; `--expect` and the optional `--interpret` proposal
      path both executed. Accuracy on Bulgarian/clinical vocabulary still needs
      its own benchmark.
- [x] A MediaRecorder-style WebM with `format=duration: N/A` (created by piping
      the JFK sample through FFmpeg) transcribed successfully; packet-timestamp
      fallback and no-metadata conversion duration checks are covered by tests.
- [x] The live smoke transcript did **not** appear in the app or speech-service
      logs, and it is not written to audit or PostgreSQL.
- [x] The doctor UI includes a deterministic Bulgarian/English “What to say”
      example and an **Insert example request** action; the inserted transcript
      remains editable and is not auto-submitted.
- [x] “Did you mean?” transcript suggestions: the live endpoint proposed
      `Елена Георгиева / MRN-0001` from `Elena Georgievah` plus
      `M-R-N-0-0-0-1`; applying the suggestion corrected both spans in the
      editable transcript without submitting anything.
- [x] The speech image was rebuilt and runs the Base multilingual model
      (`type = 2 (base)`, 147 MB model load); the live checker now reports
      `model=base`.
- [x] Single-orb listener state machine covered by the frontend render suite:
      idle, requesting, listening (with a stub AnalyserNode driving the live
      level), processing/auto-upload, success, permission denial, cancel,
      microphone-track release and unsupported-browser error. Real browser
      microphone level/layout and screen-reader output still need browser
      verification (documented limitation).
- [ ] Real browser microphone/`MediaRecorder` behaviour (no browser in the
      automated environment).
- [ ] whisper.cpp Base accuracy/latency/memory benchmark on the target host and
      on Bulgarian/English clinical vocabulary.
- [ ] Cloud Run private-service IAM / metadata-token path.
