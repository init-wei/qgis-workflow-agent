# Web UI

The Web UI is a Vue frontend backed by FastAPI. It is part of the current controlled-execution path, not a standalone agent.

## Current User Flow

1. The user submits a GIS request through the chat page.
2. The backend streams planner output over SSE.
3. The UI shows a plan-review modal when the backend emits `human_review_required`.
4. Approved plans continue to controlled execution.
5. High-risk calls emit `risk_review_required` and wait for approve, reject, or modified arguments.
6. Successful execution emits `result_review_required`.
7. After result confirmation, the graph resumes and emits the final summary.

## Local Development

Start the backend first:

```bash
uv run python ui/backend/main.py
```

Start the frontend dev server:

```bash
cd ui/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` and `/shared` to `http://localhost:9000`.

## Built Frontend

Build the frontend:

```bash
cd ui/frontend
npm run build
```

The backend can serve the built SPA when `ui/frontend/dist/` exists. Direct routes such as `/trace-demo` are handled by the FastAPI SPA fallback.

## Main Backend Endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /api/chat/stream` | Start or continue a chat request and stream graph events. |
| `POST /api/review` | Approve or reject a generated plan. |
| `POST /api/risk-review` | Submit high-risk tool-call review feedback. |
| `POST /api/confirm-result` | Confirm whether the observed execution result is complete. |
| `POST /api/resume/{thread_id}` | Resume a checkpointed graph after review. |
| `GET /api/state/{thread_id}` | Inspect checkpoint state for debugging. |
| `GET /api/demo-cases` | List static trace demo cases. |
| `GET /api/demo-cases/{case_id}/trace` | Return one static demo trace. |

## Trace Demo

`/trace-demo` is a read-only inspection page for static demo cases. It does not execute QGIS, call an LLM, register skills, or replace the main chat UI.
