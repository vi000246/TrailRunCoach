# Deploy Guide: ai-coach-training-analysis

> ⛔ **CANCELED（2026-10-04）**：5 月版的設計已作廢——React SPA 與 5 月的舊程式移除，相關功能之後另做新版。本文件只留作歷史紀錄；現行實作以 `docs/spec/` 為準。

## Metadata
- **Source Plan**: `docs/plans/ai-coach-training-analysis.plan.md`
- **Source Report**: `docs/reports/ai-coach-training-analysis-report.md`
- **Source Linear Issue**: N/A
- **Target Environment**: local
- **Deploy Type**: one-shot
- **Rollback Strategy**: `git revert HEAD` then `npm run build && ./start.sh`
- **Expected Downtime**: brief (~10s restart)
- **Created**: 2026-05-16

## Pre-Deploy Checklist
- [ ] Python imports verified (`python3.12 -c "from backend.main import app"`)
- [ ] Frontend build clean (zero TypeScript errors)
- [ ] API smoke test: `/api/v1/ai/models` returns model list
- [ ] API smoke test: `/api/v1/analytics/dashboard-summary` returns data
- [ ] AI Key not yet set (expected — user sets it via Config tab)

## Deployment Steps

### Step 1: Install Python dependencies
- **Risk Level**: SAFE
- **Command**: `pip3 install -r requirements.txt -q`
- **Verify**: `/opt/homebrew/bin/python3.12 -c "import anthropic, openai; print('OK')"`
- **Rollback**: N/A

### Step 2: Build frontend
- **Risk Level**: SAFE
- **Command**: `cd frontend && npm run build`
- **Verify**: `test -f frontend/dist/index.html && echo OK`
- **Rollback**: N/A

### Step 3: Commit all changes
- **Risk Level**: MEDIUM
- **Command**: `git add -A && git commit -m "feat: AI Coach chat, Smart Dashboard, Trail Analysis"`
- **Verify**: `git log --oneline -1`
- **Rollback**: `git revert HEAD --no-edit`

### Step 4: Restart local server
- **Risk Level**: SAFE
- **Command**: `pkill -f "uvicorn backend.main" 2>/dev/null; sleep 1; ./start.sh &`
- **Verify**: `sleep 4 && curl -sf http://localhost:8000/api/v1/ai/models && echo OK`
- **Rollback**: N/A (SAFE — just restart)

## Post-Deploy Verification
- [ ] Open http://localhost:5173 — Season page shows Smart Dashboard section
- [ ] Open http://localhost:5173/ai — shows AI chat UI (not the old MCP page)
- [ ] Open Config tab — shows AI Coach Settings block with provider/model/key fields
- [ ] Open any running workout detail — trail section appears if elevation > 100m

## Rollback Procedure
If post-deploy verification fails:
1. `git revert HEAD --no-edit`
2. `cd frontend && npm run build`
3. Restart server: `./start.sh`

## Notes
- Start.sh uses `/opt/homebrew/bin/python3.12` — the venv Python 3.9 has a pre-existing SQLAlchemy/Mapped type incompatibility that doesn't affect Python 3.12.
- AI features require the user to set an API key via Config tab before the chat is usable.
- Trail analysis only appears on workouts with elevation_gain_m > 100m OR sport = trail_running.
