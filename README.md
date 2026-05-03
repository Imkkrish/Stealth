# Stealth

A real-time interview-assistance overlay. Mac client captures audio + screen, transcribes locally with Whisper, and streams answers from a small server you operate. Bring your own LLM API key (Gemini / OpenAI / Anthropic).

## Repo layout

```
.
├── apps/
│   └── desktop/                # Mac Electron + Python client (stealthy overlay)
│       └── README.md
├── services/
│   └── stealth-server/         # FastAPI + Socket.IO backend on Cloud Run
│       └── README.md
├── .github/
│   └── workflows/
│       └── deploy-stealth-server.yml   # backend CI/CD
├── .env.example
├── .gitignore
└── README.md                   # this file
```

The desktop client and the server are independent: the server has no idea who the desktop user is beyond the shared secret, and the client only sends transcripts/screenshots up — keys live on the user's Mac.

## Architecture

```
┌─────────────────────────────────────────┐         ┌──────────────────────────────────────┐
│  apps/desktop  (Mac, Electron+Python)   │  ws    │  services/stealth-server (Cloud Run) │
│  - sounddevice → audio chunks           │  ───▶  │  - FastAPI + Socket.IO               │
│  - faster-whisper tiny.en (LOCAL STT)   │  ───▶  │  - LLM router (Gemini/OpenAI/Claude) │
│  - Quartz / mss → screenshot PNG        │        │  - Slot-based prompt composer        │
│  - Overlay UI                           │  ◀──   │  - Token streaming back              │
│  - Config in ~/.stealth/config.json     │        │  - Shared-secret auth                │
└─────────────────────────────────────────┘        └──────────────────────────────────────┘
```

Wire format: see [`services/stealth-server/README.md`](services/stealth-server/README.md).

## Run it locally

**Server:**
```bash
cd services/stealth-server
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export STEALTH_SHARED_SECRET=dev-secret-123
uvicorn app:app --reload --port 8080
```

**Client (separate terminal):**
```bash
cd apps/desktop
./start.sh                     # one-time: creates venv, installs deps
npm run start-electron
```

In the overlay's setup modal, paste:
- Server URL: `http://localhost:8080`
- License: `dev-secret-123`
- Your provider, model, API key
- (Optional) language, interview context, resume

## Deploy the server to Cloud Run (`asia-south1` Mumbai)

### One-time GCP setup

```bash
# 1. Pick / create a project
export PROJECT_ID=your-project
gcloud config set project $PROJECT_ID

# 2. Enable required services
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iamcredentials.googleapis.com

# 3. Create the Artifact Registry Docker repo in Mumbai
gcloud artifacts repositories create stealth-server \
  --repository-format=docker \
  --location=asia-south1 \
  --description="Stealth server Docker images"

# 4. Create the deploy service account
gcloud iam service-accounts create stealth-deployer \
  --display-name="Stealth GitHub Actions deployer"

SA="stealth-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

# 5. Grant minimum practical roles
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA}" --role="$role"
done

# 6. Create a Workload Identity Pool + GitHub provider, then bind to the SA.
#    (See Google's docs for current syntax — outline below.)
#    https://github.com/google-github-actions/auth#setting-up-workload-identity-federation
```

### GitHub repo configuration

Add **repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | your GCP project ID |
| `GCP_REGION` | `asia-south1` |
| `CLOUD_RUN_SERVICE` | `stealth-server` |
| `ARTIFACT_REGISTRY_REPO` | `stealth-server` |

Add **repository secrets**:

| Secret | Value |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | full resource name, e.g. `projects/123/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_SERVICE_ACCOUNT` | `stealth-deployer@<project>.iam.gserviceaccount.com` |
| `STEALTH_SHARED_SECRET` | the secret you'll give friends to paste into the client |

### Trigger a deploy

- **Auto** — push to `main` with changes under `services/stealth-server/**`.
- **Manual** — Actions → "Deploy stealth-server" → Run workflow.

The workflow ([`.github/workflows/deploy-stealth-server.yml`](.github/workflows/deploy-stealth-server.yml)) builds a tagged Docker image (`asia-south1-docker.pkg.dev/$PROJECT/stealth-server/stealth-server:$SHA`), pushes it, deploys to Cloud Run with `min-instances=1` and `timeout=3600`, then smoke-tests `/health`.

After the first deploy, give friends the printed URL + the `STEALTH_SHARED_SECRET` and they can paste both into the desktop client's setup modal.

## Why `asia-south1`?

Most users are in India. Mumbai cuts a ~150 ms round-trip vs. `us-central1`, which is the difference between "instant" and "noticeable" for streamed token rendering.

## Conventions

- **Generated artifacts are not tracked.** No `.app/`, no `bin/`, no `dist/`, no `node_modules/`, no `venv/` in git. Rebuild on every release.
- **Secrets are not tracked.** `.env` is gitignored; the client doesn't read it at runtime anyway. The server reads `STEALTH_SHARED_SECRET` from Cloud Run env.
- **Backend deploys are isolated.** Only `services/stealth-server/**` changes trigger a backend deploy. Desktop releases are built locally and shipped via download — separate from this CI/CD.
