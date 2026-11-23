# WorksFr frontend

Local report preview for UI-1. Requires Node compatible with Vite 8.

```powershell
npm.cmd install --cache ../.scratch/npm-cache
npm.cmd run dev
```

Open `/preview/report?state=done`, `?state=running`, or `?state=failed`.
Preview routes, controls, and sample data are development-only. Production routes
and live API/SSE wiring belong to the next task.

```powershell
npm.cmd run build
npm.cmd run lint
```

Generate API types from the real backend on port 8000:

```powershell
npm.cmd run gen:api
```

`src/api/schema.d.ts` is generated; do not edit it by hand. `ReportView` accepts
a generated `CheckReport` and optional `liveState` with `runningStepIndex` and
`pendingBlocks` (generated `EventBlock`s). Its components do no fetching.

UI primitives follow shadcn's Radix Badge and Collapsible patterns. The
`new-york` registry configuration is in `components.json`; report styling uses
the task's tokens in `src/index.css`.
