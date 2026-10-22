# BloodSignal

**Find blood banks. Review the evidence. Call to confirm.**

BloodSignal is an evidence-aware blood-bank discovery application built by **Team Pragmatrix**. It helps families and care teams organize a search by blood group, component, quantity, and location. Facility directory records and inventory evidence are shown as separate facts so users can see what is known, what is simulated, and what still needs confirmation.

## The problem

When someone needs blood, families often have to call multiple facilities and search across directories that may be incomplete or out of date. A facility listing does not prove that it currently has the requested blood component or quantity. Searchers also need a clear address and telephone number so they can confirm before travelling.

## How BloodSignal helps

- Searches facility directories using blood group, component, quantity, and district or consented device location.
- Accepts a written request and supports optional field suggestions, voice transcription, language selection, and prescription-assisted extraction. Suggestions must be reviewed before searching.
- Separates facility directory data from inventory evidence and reports unknown, stale, unavailable, insufficient, and demo evidence distinctly.
- Ranks results deterministically using evidence quality and freshness before distance. Any calculated distance is labelled as straight-line distance.
- Provides validated call-to-confirm and OpenStreetMap destination actions without implying that a call or navigation has started.
- Offers list and map views. Search remains available when the optional embedded Google Map is not configured.
- Uses the observed e-RaktKosh bulk endpoint as the default live stock source. It does not fall back to synthetic inventory when the source fails; failures remain explicit and are not treated as zero stock.

## What makes it different

BloodSignal treats provenance and uncertainty as part of the result. It does not infer stock from a directory listing, turn unknown stock into zero, or present synthetic demo inventory as hospital data. Users can inspect source and freshness details, then contact the facility directly. It is an information tool and does not provide medical advice, determine transfusion compatibility, reserve blood, or replace emergency services.

## Technology

- **Frontend:** React, TypeScript, TanStack Start and Router, Vite, Tailwind CSS.
- **Backend:** Python, FastAPI, Pydantic, deterministic workflow orchestration.

The main search UI uses the existing `/api/runs` workflow. The separate `/api/v1/agents/search` route is not used as the primary search flow.

## Run locally

Requirements: Python 3.11 or newer, Node.js 20 or newer, and npm. Redis is optional for a single-process local run.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

### Frontend

In another terminal:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Open the Vite URL, normally `http://localhost:5173`. Local Vite requests under `/backend` proxy to FastAPI on port 8000. The production TanStack server also proxies `/backend/*` to the private `BACKEND_INTERNAL_URL`, keeping API traffic same-origin. Set that server-only URL for the deployed backend.

**Device location in local development:** use `http://localhost:5173` or `http://127.0.0.1:5173` on the same computer, then allow Location in the browser’s site permissions. Browsers treat localhost as a trusted development origin. If you open the app from a phone or another computer using `http://192.168.x.x:5173`, geolocation is normally blocked because a LAN IP over plain HTTP is not a secure context. For testing on another device, serve the app through HTTPS (for example, a trusted HTTPS development tunnel or local HTTPS certificate). `localhost` on a phone refers to the phone itself, not the development computer. If location is denied or unavailable, BloodSignal provides an explanatory message and manual city/district search remains available.

To preview the Cloudflare Worker production build locally, run `npm run build` and then `npm run preview` from `frontend/`. This uses the same Wrangler runtime as the configured Cloudflare deployment target.

## Environment files

The repository includes `backend/.env.example` and `frontend/.env.example`. Copy them to `.env` in their respective directories and fill in only the credentials for services you enable. Local `.env` files are ignored by Git; never commit actual credentials.

### Backend settings

- `DATAGOV_API_KEY`: server-side credential for the configured public facility directory, when required.
- `STOCK_SOURCE_MODE=live`: default hackathon configuration; the backend calls the observed e-RaktKosh bulk endpoint server-side. No browser API key or e-RaktKosh API key is used by this adapter. Set `ERAKTKOSH_SOURCE_TIMEZONE=Asia/Kolkata` only after verifying the source timestamp timezone. `STOCK_SOURCE_MODE=demo` remains an explicit test-only option and is never an automatic fallback.
- `LOVABLE_AI_GATEWAY_KEY`: optional server-side voice transcription key. Without it, voice transcription reports that the service is unavailable; typed search still works.
- `TOKEN_HASH_SECRET`: set a unique random value with at least 32 bytes in production. It HMAC-hashes run tokens and idempotency keys and derives scoped run tokens. The local fallback is for development only.
- `RATE_LIMIT_PER_MINUTE` and `VOICE_RATE_LIMIT_PER_MINUTE`: POST request budgets.
- `RATE_LIMIT_REDIS_URL`: configure Redis for an atomic, shared rate limit across backend processes. Production reports unready and returns a service error if Redis is unhealthy rather than silently downgrading to per-process limits.
- `TRUST_PROXY_HEADERS`: enable only when a trusted ingress overwrites `CF-Connecting-IP` or `X-Real-IP`, the frontend proxy forwards that value, and direct backend access is blocked. Otherwise the API uses the socket peer address.
- OpenStreetMap map/directions do not require a Google Maps API key; distances are Haversine estimates only when both coordinates are validated.

### Frontend settings

- `VITE_API_BASE_URL=/backend`: same-origin backend proxy prefix for local development.
- `BACKEND_INTERNAL_URL`: server-only URL used by the frontend voice proxy. Do not rename it with a `VITE_` prefix.
- Basic map display and destination links use OpenStreetMap and do not require a browser API key. The community tile service has usage policies; select an appropriate tile provider for production traffic.
- `VITE_SITE_URL`: set to the deployed HTTPS origin before a production build to generate `sitemap.xml`. The project domain is deployment-specific and is not guessed.

No LLM is needed for the core deterministic search flow. AI chat has not been added because a conversational bot would not improve evidence quality or replace direct confirmation with the facility.

## Checks

From `backend/`:

```powershell
python -m pytest -q
```

From `frontend/`:

```powershell
npm test
npm run lint
npm run build
```

The backend tests use mocked providers and do not depend on external APIs. The frontend build generates a sitemap when `VITE_SITE_URL` is set. Automated tests and a local build are not a substitute for load testing the deployed infrastructure or validating real provider access.

## Production and scaling notes

The API includes request validation, bounded POST rate limits, HMAC-protected tokens and idempotency keys, restricted CORS, payload-size checks, safe errors, no-store API responses, security headers, and redacted request logs. HTTPS, secret rotation, deployment firewall rules, dependency scanning, backups, monitoring, and ingress protections must also be configured by the hosting environment.


For sitemap indexing, set `VITE_SITE_URL` to the final HTTPS domain, rebuild, and submit `/sitemap.xml` in Google Search Console and Bing Webmaster Tools. Search engines decide whether and when to index pages; the application cannot guarantee indexing.

The PWA service worker caches only its offline notice and app metadata/icons. It never caches API responses or search results. Facility search requires a connection.

## Data and safety

- Inventory source configuration: `STOCK_SOURCE_MODE=live` by default.
- The e-RaktKosh endpoint is observed but not a documented/stability-guaranteed API. Validate permitted use and representative state/component responses; stop on access denial, redirects, or rate limiting.
- The upstream response omits reliable coordinates and district IDs. Facility directions can open from a source address; distance and map pins remain unavailable unless separately verified coordinates are obtained.
- Missing inventory is unknown, not zero. A source error is not a no-stock report.
- The endpoint's `no-store` directive is honored: the live stock search bypasses the shared inventory cache, raw responses are not persisted, and API responses use `Cache-Control: no-store`.
- Do not claim live source access is guaranteed. This implementation was verified with mocked responses and has not been live-tested against every catalog selection.
- Always call the facility to confirm current availability and details before travelling.
- In an emergency, contact local emergency services or a hospital directly.

## Team and copyright

BloodSignal is developed by **Susanta Banik** and **Shreya Bhattacharjee** as **Team Pragmatrix**.

© 2026 Pragmatrix. All rights reserved.
