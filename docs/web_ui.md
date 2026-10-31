> **历史原型说明，不是当前操作指南。** 当前启动与真实浏览器审核见[环境入口](onboarding/README.md)和[V1 演示](onboarding/v1-demo.md)，实际范围及证据见[验收入口](onboarding/acceptance.md)。
>
> 旧 Trace Demo 的默认按钮和 `/trace-demo` Vue 路由已退役，`/api/demo-cases` 及原 case/trace 接口也已移除；对应 API 请求返回 JSON 404。FastAPI 通用 SPA shell 响应不能证明存在该 Vue 路由，不能把其 HTTP 状态当作旧页面仍可用的证据。当前实际浏览器复验以 [T08](https://github.com/init-wei/qgis-workflow-agent/issues/28) 为准。

<details>
<summary>原型时期的旧说明（归档，勿按当前版本操作）</summary>

# Historical Web UI

The prototype Web UI was a Vue frontend backed by FastAPI and part of its controlled-execution path.

## Prototype User Flow

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

The prototype backend could serve the built SPA when `ui/frontend/dist/` existed. A generic SPA fallback never establishes that a Vue route exists.

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

`/trace-demo` is a read-only inspection page for static demo cases. It does
not execute QGIS, call an LLM, or replace the main chat UI. The
`skill_draft_preview` case demonstrates demo-only Skill registration by showing
the extracted `SkillDraft`, the registered skill metadata, and the rendered
controlled execution plan that would still need to pass the normal policy and
review gates before live execution.

</details>
