# Frontend

Next.js operator console for the local agent orchestrator.

For the full stack setup, demo seed flow, and Docker instructions, use the root [README.md](../README.md).

## Local frontend-only dev

1. Start the backend on `http://localhost:8000`
2. Install dependencies:

```bash
npm install
```

3. Start the frontend:

```bash
npm run dev
```

The dashboard defaults to `http://localhost:8000` for HTTP and websocket traffic.

Optional env vars:
- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_WS_BASE_URL`
