# Mumbai relay for e-RaktKosh (free, on Vercel)

Render (Singapore) cannot connect to e-RaktKosh, but an Indian network can. This tiny Vercel
Function runs in **Mumbai (bom1)**, makes the same fixed request, and returns the response.
Your backend keeps all its logic (parsing, freshness, SOURCE_UNAVAILABLE handling).

## 1. Deploy the relay (Vercel, free Hobby)
1. Vercel > Add New > Project > import your BloodSignal repo.
2. **Root Directory: `relay`**, Framework Preset: Other. Do not change the build settings.
3. Environment Variable `RELAY_SECRET` = a long random value:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
4. Deploy. Note the URL, e.g. `https://bloodsignal-relay.vercel.app`.

## 2. Test it BEFORE touching Render
```bash
curl -i -H "x-relay-secret: YOUR_SECRET" \
  "https://bloodsignal-relay.vercel.app/api/stock?stateCode=16&componentId=12"
```
- `200` + header `x-relay-region: bom1` + a JSON list: it works. Continue.
- `504` / `403` / timeout: Mumbai datacenter addresses are also blocked or filtered. Stop here
  (see "If it fails" below). Do not try to disguise the request.

## 3. Switch Render to use it
In Render > Environment add (then redeploy):
```
ERAKTKOSH_RELAY_URL=https://bloodsignal-relay.vercel.app/api/stock
ERAKTKOSH_RELAY_SECRET=YOUR_SECRET
STOCK_SOURCE_TIMEOUT_S=25
```
**Rollback:** delete `ERAKTKOSH_RELAY_URL` and `ERAKTKOSH_RELAY_SECRET`; the backend calls the portal directly again.

## Safety notes
- Not an open proxy: fixed host and path, digits-only parameters, secret header required.
- The site URL is public; the secret is what protects it. Rotate it if it leaks.
- Keep traffic low (your backend's 20-minute cache already helps).

## If it fails
Use a host with an Indian region: Azure for Students (Central India, $100 credit, no card, college
email), Fly.io Mumbai (small paid VM), or ask C-DAC / e-RaktKosh for official access.
