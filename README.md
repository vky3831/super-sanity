# SuperSanity — Integration Tester (Streamlit)

This project is a minimal, ready-to-run Streamlit webapp to create **Flows** (sequence of API calls), group them into **Jobs**, run Jobs (flows run in parallel up to configurable concurrency; steps inside a flow run sequentially), and produce downloadable HTML reports. Data (flows, jobs, run results) are persisted to MongoDB.

## Features implemented (minimal viable)
- Create Flows with multiple sequential steps (method, URL, headers, expected status).
- Define **body field rules** per step as JSON to auto-generate payloads:
  - `static` — fixed value.
  - `auto_uuid` — auto-generated UUID.
  - `regex` — generate string matching regex (uses `rstr`).
  - `choice` — choose from a list.
  - `map` — map value from previous responses using dotted path (basic).
- Create Jobs (collection of flows).
- Run Jobs: flows run in parallel (configurable max), each flow's steps run sequentially.
- Persist flows, jobs, and run records in MongoDB.
- Download HTML report after a job runs.

## Docker (recommended)
This repository includes a `Dockerfile` for the Streamlit app and a `docker-compose.yml` that brings up both:
- `web` (the Streamlit app)
- `mongo` (MongoDB) — so you do **not** need to install MongoDB locally.

### Run with Docker Compose
```bash
docker compose up --build
```
Open http://localhost:8502 in your browser.

### If you don't want Docker
Install Python 3.10+, create a virtualenv, install requirements:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run main.py
```
Make sure MongoDB is reachable (default expects `mongodb://mongo:27017` — you can override via Streamlit secrets or environment).

## Usage notes
- When creating a Flow step, provide "Headers JSON" and "Body rules JSON" as JSON strings.
- Example body rules:
```json
{
  "username": {"type":"regex","pattern":"[a-z]{6}"},
  "id": {"type":"auto_uuid"},
  "role": {"type":"choice","choices":["admin","user"]},
  "ref": {"type":"map","path":"last_response.json.id"}
}
```
- The `map` type supports basic mapping from `last_response` or other context keys (dotted path).

## Limitations / To improve (future)
- UI is minimal; better form validation and richer mapping (JSONPath) would help.
- Authentication and secure storage of secrets/headers.
- More generator types, better error handling and retries.
- UI for viewing/editing flows/jobs persisted in DB.

## Files
- `main.py` — Streamlit app
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`

## Troubleshooting
- If Streamlit can't connect to Mongo when running locally, adjust `MONGO_URI` in `st.secrets` or environment variables to point to your MongoDB instance.

Enjoy — fork and extend for your hackathon!

### Fix for `StreamlitSecretNotFoundError`
If you see `StreamlitSecretNotFoundError: No secrets found`, either set `MONGO_URI` as an environment variable or create a `.streamlit/secrets.toml` file in the project root with:

```
MONGO_URI = "mongodb://mongo:27017"
```
This project now ships a default `.streamlit/secrets.toml` so the error should not occur inside the Docker container.
