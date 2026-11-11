# Speech recognition and voice scheduling

**Status:** implemented local path (browser → FastAPI → FFmpeg → private
whisper.cpp), with no claim of production readiness. The automated tests cover
the API, binding, conversion, cleanup, and a mocked speech-service boundary;
real-browser microphone behaviour and whisper.cpp accuracy/latency are **not**
verified by the test suite.

The whisper.cpp image is pinned to release **v1.9.5** (source tarball SHA-256
`ff1a9053…0dc1720`) and the **Tiny multilingual** model `ggml-tiny.bin`
(SHA-256 `be07e048…c6e1b21`). The model is baked into the image and verified at
build time. CPU only; `tiny.en` is deliberately not used because Bulgarian is a
target language.

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
    | file + language=en|bg, CSRF cookie/header, session cookie
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
Private whisper.cpp container (CPU, Tiny multilingual)
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

Audio never reaches the LLM. The interpreter receives text only.

## Browser capture rules

- Feature detection: if `getUserMedia` or `MediaRecorder` is missing, the UI
  says so and keeps the manual transcript editor.
- MIME candidates in order: `audio/webm;codecs=opus`, `audio/webm`,
  `audio/ogg;codecs=opus`, `audio/mp4`; `isTypeSupported` is used when present.
- Recording stops automatically at 60 seconds. The microphone tracks are
  stopped immediately on stop, on clear, and on page hide.
- Audio chunks stay in a JavaScript `Blob` in memory. There is no
  `localStorage`/`sessionStorage`, no IndexedDB, no background listening, and
  no upload until the user presses **Transcribe**.
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

## Private speech service

`docker-compose.yml` defines an optional `speech` service with the `speech`
profile and **no published port**. The image:

- builds whisper.cpp v1.9.5 from the checksum-verified release tarball,
- downloads `ggml-tiny.bin` and verifies its SHA-256 before copying it into the
  runtime image,
- runs as a non-root user, CPU only (`--no-gpu`), 2 threads by default
  (`WHISPER_THREADS`),
- serves `/inference` on port 8080 inside the compose network.

Enable it locally:

```bash
# 1. Build/start the private speech service (first build downloads ~78 MB model
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
- [x] Global 1 MiB body limit preserved; voice route allowance is exact and
      hard-clamped.
- [x] AI interpreter `tools=[]`, patient resolution through the existing MCP
      tool, DST/conflict warnings, proposal-only behaviour, and explicit create.
- [x] Speech image builds from the checksum-verified release/model, reports
      healthy, and answers `/inference`.
- [x] Live local path: authenticated multipart upload → backend FFmpeg →
      private whisper.cpp Tiny → HTTP 200 with `{text, language, model,
      duration_seconds}`. The generated English smoke sample was transcribed
      (content accuracy is not claimed; see below).
- [x] The live smoke transcript did **not** appear in the app or speech-service
      logs, and it is not written to audit or PostgreSQL.
- [ ] Real browser microphone/`MediaRecorder` behaviour (no browser in the
      automated environment).
- [ ] whisper.cpp Tiny accuracy/latency/memory benchmark on the target host and
      on Bulgarian/English clinical vocabulary.
- [ ] Cloud Run private-service IAM / metadata-token path.
