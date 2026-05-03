# Stealth Server

Stateless Socket.IO + FastAPI server that brokers between the Stealth Mac client
and the user's chosen LLM provider (Gemini / OpenAI / Anthropic). Designed to
run on Google Cloud Run with `min-instances=1` so cold-start doesn't hurt
during interviews.

## Local dev

```bash
cd server
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export STEALTH_SHARED_SECRET=dev-secret-paste-into-client
uvicorn app:app --reload --port 8080
```

Health check: `curl http://localhost:8080/health` → `{"ok": true, "auth_configured": true}`.

## Deploy to Cloud Run

```bash
gcloud auth login
gcloud config set project <your-project>

# One-time: enable services
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# Build + deploy from this directory
gcloud run deploy stealth-server \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 5 \
  --concurrency 50 \
  --timeout 3600 \
  --set-env-vars STEALTH_SHARED_SECRET=<paste-the-secret-friends-will-use>
```

`--timeout 3600` matters — Cloud Run defaults to 5 minutes per request, but
WebSockets have to stay open for the duration of the interview. Bump to 1 h.

After deploy, the URL printed at the end (e.g. `https://stealth-server-xxxxx-uc.a.run.app`)
goes into the **Server URL** field in the Mac client's setup modal. Friends paste
the same shared secret into the **License key** field.

## Wire format (recap)

```
client → "hello"      {license, provider, api_key, model, resume_text, language, interview_context}
client → "ask"        {text}
client → "ask_vision" {prompt, image_b64, mime_type}
client → "test"       {}

server → "hello_ack"     {ok, error?, provider?, model?}
server → "answer_chunk"  {token}        # repeated, streaming
server → "answer_done"   {full_text}
server → "test_result"   {success, error?}
server → "error"         {message}
```

Auth is the `license` field on connect (`auth=` payload of Socket.IO's
`io(url, {auth: {license: ...}})`). Wrong / missing → connection refused.

## What lives where

- [auth.py](auth.py) — shared-secret check.
- [prompts.py](prompts.py) — slot-based system-prompt composer (plus per-provider XML wrap for Claude).
- [llm_router.py](llm_router.py) — Gemini / OpenAI / Anthropic abstraction with `chat_stream` + `vision_stream`.
- [app.py](app.py) — FastAPI host + Socket.IO event handlers.
- [Dockerfile](Dockerfile) — Cloud Run image.
