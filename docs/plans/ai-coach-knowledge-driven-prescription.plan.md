---
linear_issue: null
---
# Plan: AI 教練知識驅動處方（zone 計算 + curated 知識 + 處方 prompt）

> **For agentic workers:** `/prp-implement` 依 `Metadata.Type` 路由。Mode B（任務先測）。後端 pytest，前端 tsc+build。

## Summary

把 AI 教練升級為知識驅動處方：新增 curated 知識模組（Palladino 區間/間歇模板/CP/越野/使用者 profile）注入 system prompt、加 zone 計算（功率/心率邊界）與 `/ai/zones` 端點、把 zone 與越野負荷加進 `build_context`、改 system prompt 為處方導向（狀態判讀 + zone + 間歇），並在前端 chat 加 quick-prompt 按鈕。

## User Story
As 想要可執行訓練處方的越野跑者，
I want AI 教練依我的筆記知識與具體 zone 數值給出狀態判讀與間歇處方，
So that 我知道現在該練 zone 幾、間歇怎麼排（時間/瓦數）。

## Problem → Solution
通用 prompt + 無 zone 數值 + 無筆記知識 → curated 知識注入 + zone 計算 + 處方導向 prompt。

## Metadata
- **Module**: ai-coach
- **Parent Plan**: N/A
- **Source PRD**: docs/prd/wko5-trail-multipage-sync-coach.prd.md
- **Source Feature SRS**: docs/srs/ai-coach-knowledge-driven-prescription.srs.md
- **Source Module Spec**: docs/spec/ai-coach-training-analysis.spec.md
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: M
- **Complexity**: Medium
- **Rigor**: balanced
- **Mode**: B — 任務先測（前端 tsc+build）
- **TDD**: on（後端）/ off（前端）
- **Commit cadence**: per-task
- **Estimated Files**: ~8

---

## UX Design

### Before
```
AI 教練：通用越野教練 system prompt + FTP/PMC/近7天 context
回覆泛泛；不提具體 zone 數值、不引用個人化知識
```

### After
```
AI 教練：注入 Palladino 知識 + 使用者 profile；context 含 zone 邊界(W/bpm) + 越野負荷
回覆：目前狀態判讀 → 建議 zone 幾(附 W/bpm) → 間歇處方(時間×瓦數)
chat 上方 quick-prompt：[今天該練什麼] [我的間歇怎麼排] [現在該做 zone 幾]
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| chat 回覆 | 泛泛建議 | 狀態+zone+間歇處方 | prompt+context 強化 |
| zone 數值 | 無 | /ai/zones + context | 從設定計算 |
| 快速提問 | 無 | quick-prompt 按鈕 | AiChat 內 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/srs/ai-coach-knowledge-driven-prescription.srs.md` | all | AC、delta |
| P0 | `docs/spec/ai-coach-training-analysis.spec.md` | all | 既有 AI 架構 |
| P0 | `backend/engine/ai/context.py` | 1-88 | SYSTEM_PROMPT + build_context（要改） |
| P0 | `backend/api/ai.py` | 1-70 | chat 端點（zones 端點比照註冊） |
| P0 | `backend/api/workouts.py` | 20-38 | POWER_ZONES_DEF / HR_ZONES_DEF（zone 計算沿用） |
| P1 | `backend/db/models.py` | 22-38 | AthleteSettings（run_ftp_w/ftp_w/lthr） |
| P1 | `backend/tests/test_run_pmc.py` | 1-15 | 測試風格 |
| P1 | `frontend/src/components/AiChat.tsx` | 1-120 | input+send（quick-prompt 落點） |
| P2 | `backend/engine/ai/client.py` | all | ai_client.stream 簽章 |

## External Documentation
No external research needed — Palladino 區間知識由使用者 notes 筆記提供（已研究）；技術沿用既有 FastAPI/React 模式。

---

## Patterns to Mirror

### ZONE_DEF (沿用既有，勿另立)
```python
# SOURCE: backend/api/workouts.py:20-38
POWER_ZONES_DEF = [
    (1, "Recovery", 0.00, 0.55), (2, "Endurance", 0.55, 0.75),
    (3, "Tempo", 0.75, 0.90), (4, "Threshold", 0.90, 1.05),
    (5, "VO2max", 1.05, 1.20), (6, "Anaerobic", 1.20, 1.50),
    (7, "Neuromuscular", 1.50, 99.0),
]
HR_ZONES_DEF = [
    (1, "Recovery", 0.00, 0.85), (2, "Aerobic", 0.85, 0.90),
    (3, "Tempo", 0.90, 0.95), (4, "Threshold", 0.95, 1.00),
    (5, "VO2max", 1.00, 99.0),
]
```

### CONTEXT_ASSEMBLY (build_context 既有風格)
```python
# SOURCE: backend/engine/ai/context.py:17-35
lines: list[str] = ["=== 運動員訓練數據 ==="]
settings = (await db.execute(select(AthleteSettings)
    .where(AthleteSettings.athlete_id == athlete_id)
    .order_by(AthleteSettings.effective_date.desc()))).scalars().first()
if settings:
    lines.append(f"FTP (跑步功率): {settings.run_ftp_w or settings.ftp_w} W")
```

### ROUTER_PATTERN
```python
# SOURCE: backend/api/ai.py:22-28
@router.get("/models")
async def list_models(): ...
@router.get("/status/{athlete_id}")
async def ai_status(athlete_id: int, db: AsyncSession = Depends(get_db)): ...
```

### SYSTEM_PROMPT_INJECTION
```python
# SOURCE: backend/engine/ai/context.py:10-14 + api/ai.py:66
SYSTEM_PROMPT = """你是一位專業的越野跑教練AI助理。..."""
# ai.py: async for chunk in ai_client.stream(system=SYSTEM_PROMPT, user=user_prompt)
```

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_run_pmc.py:1-15
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.algorithms.metrics import compute_run_pmc
def test_x(): assert ...
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/engine/ai/knowledge.py` | CREATE | curated 訓練知識常數 + 組裝函式 |
| `backend/engine/ai/zones.py` | CREATE | zone 計算（功率/心率邊界） |
| `backend/engine/ai/context.py` | UPDATE | SYSTEM_PROMPT 注入知識 + build_context 加 zone/越野負荷 |
| `backend/api/ai.py` | UPDATE | 加 GET /ai/zones |
| `backend/tests/test_ai_zones.py` | CREATE | zone 計算 |
| `backend/tests/test_ai_knowledge.py` | CREATE | 知識注入 |
| `backend/tests/test_ai_context.py` | CREATE | context 含 zone |
| `frontend/src/components/AiChat.tsx` | UPDATE | quick-prompt 按鈕 |

## NOT Building
- RAG / 向量檢索 vault（curated 知識取代）
- 對話歷史持久化
- 自動排課 / 行事曆
- chat UI 重設計

---

## Step-by-Step Tasks

### Task 1: zone 計算 + /ai/zones 端點
- **ACTION**: 建 `zones.py`，從 rFTP/LTHR 計算功率與心率 zone 邊界（沿用 ZONE_DEF %）；`ai.py` 加 `/ai/zones` 端點。
- **TEST FIRST**: `backend/tests/test_ai_zones.py`
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from backend.engine.ai.zones import compute_zones

  def test_zones_from_settings():
      z = compute_zones(run_ftp_w=192, lthr=182)
      power = {p["zone"]: p for p in z["power"]}
      hr = {h["zone"]: h for h in z["hr"]}
      assert len(z["power"]) == 7 and len(z["hr"]) == 5
      # zone4 threshold power: 0.90–1.05 × 192 = 172.8–201.6
      assert abs(power[4]["low_w"] - 172.8) < 0.5
      assert abs(power[4]["high_w"] - 201.6) < 0.5
      assert power[4]["name"] == "Threshold"
      # zone4 HR threshold: 0.95–1.00 × 182
      assert abs(hr[4]["low_bpm"] - 172.9) < 0.5

  def test_zones_missing_inputs():
      z = compute_zones(run_ftp_w=None, lthr=None)
      assert z["power"] == [] and z["hr"] == []
  ```
  Run: `/opt/homebrew/bin/pytest backend/tests/test_ai_zones.py -q` — expect FAIL
- **IMPLEMENT**: `zones.py`
  ```python
  """Compute power/HR training zone boundaries from settings (Coggan/Palladino %)."""
  from typing import Optional

  POWER_ZONES = [
      (1, "Recovery", 0.00, 0.55), (2, "Endurance", 0.55, 0.75),
      (3, "Tempo", 0.75, 0.90), (4, "Threshold", 0.90, 1.05),
      (5, "VO2max", 1.05, 1.20), (6, "Anaerobic", 1.20, 1.50),
      (7, "Neuromuscular", 1.50, 99.0),
  ]
  HR_ZONES = [
      (1, "Recovery", 0.00, 0.85), (2, "Aerobic", 0.85, 0.90),
      (3, "Tempo", 0.90, 0.95), (4, "Threshold", 0.95, 1.00),
      (5, "VO2max", 1.00, 99.0),
  ]

  def compute_zones(run_ftp_w: Optional[float], lthr: Optional[int]) -> dict:
      power = []
      if run_ftp_w and run_ftp_w > 0:
          power = [{"zone": z, "name": n,
                    "low_w": round(run_ftp_w * lo, 1),
                    "high_w": round(run_ftp_w * hi, 1) if hi < 90 else None}
                   for z, n, lo, hi in POWER_ZONES]
      hr = []
      if lthr and lthr > 0:
          hr = [{"zone": z, "name": n,
                 "low_bpm": round(lthr * lo, 1),
                 "high_bpm": round(lthr * hi, 1) if hi < 90 else None}
                for z, n, lo, hi in HR_ZONES]
      return {"power": power, "hr": hr}
  ```
  `ai.py` 加：
  ```python
  from backend.engine.ai.zones import compute_zones
  from backend.db.models import AthleteSettings
  from sqlalchemy import select

  @router.get("/zones")
  async def ai_zones(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      s = (await db.execute(select(AthleteSettings)
          .where(AthleteSettings.athlete_id == athlete_id)
          .order_by(AthleteSettings.effective_date.desc()))).scalars().first()
      ftp = (s.run_ftp_w or s.ftp_w) if s else None
      lthr = s.lthr if s else None
      return {"athlete_id": athlete_id, "run_ftp_w": ftp, "lthr": lthr,
              **compute_zones(ftp, lthr)}
  ```
- **MIRROR**: ZONE_DEF, ROUTER_PATTERN
- **GOTCHA**: 最後一區 high 為開放（99.0）→ high_w/high_bpm 回 None。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(ai-coach): zone computation + /ai/zones endpoint`

### Task 2: curated 知識模組
- **ACTION**: 建 `knowledge.py`，內含 Palladino 區間訓練目的、間歇模板、CP 測試、越野/爬升概念、使用者 profile，組成 `KNOWLEDGE_BLOCK` 文字 + `build_knowledge()` 函式。
- **TEST FIRST**: `backend/tests/test_ai_knowledge.py`
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from backend.engine.ai.knowledge import build_knowledge

  def test_knowledge_includes_key_concepts():
      kb = build_knowledge()
      for term in ["Palladino", "間歇", "CP", "無氧", "爬升"]:
          assert term in kb
  ```
  Run: `/opt/homebrew/bin/pytest backend/tests/test_ai_knowledge.py -q` — expect FAIL
- **IMPLEMENT**: `knowledge.py`（內容萃取自使用者 notes 筆記，curated 常數）
  ```python
  """Curated training knowledge from the athlete's notes power-training notes.
  Static (no RAG) — update here when the notes' framework changes."""

  KNOWLEDGE_BLOCK = """=== 訓練知識庫（Palladino 個人化功率訓練） ===
  - 功率區間（Palladino，依個人化 rFTP）：Z1 恢復 / Z2 耐力 / Z3 節奏 / Z4 閾值 / Z5 VO2max / Z6 無氧 / Z7 神經肌肉。
  - CP（臨界功率）由 3–30 分鐘 MMP 曲線擬合；TTE（耐受時間）約 30 分鐘為閾值參考。
  - 間歇模板：VO2max 用 3–5 分鐘 @ Z5、組間等量恢復；無氧/RWC 用 30s–2min @ Z6+、充分恢復；
    閾值用 8–20 分鐘 @ Z4。處方應給「時間 × 目標瓦數（或 zone）× 組數 × 恢復」。
  - 越野/爬升：技術地形以心率（hrTSS）為主負荷，rTSS（配速）低估；爬升用 VAM（垂直速度 m/hr）評估；
    GAP（坡度調整配速）比對平路強度。
  - 使用者特性：偏無氧型（RWC 充足），間歇可略高於典型 Palladino 上緣；越野 PI 屬中階。
  - 訓練狀態判讀：TSB>5 新鮮（可高強度/比賽）、-10~5 最佳、-25~-10 疲勞（注意恢復）、<-25 過度（減量）。
  """

  def build_knowledge() -> str:
      return KNOWLEDGE_BLOCK
  ```
- **MIRROR**: CONTEXT_ASSEMBLY 文字風格
- **GOTCHA**: 知識為靜態；筆記框架變動需手動更新此檔（SRS Open Question）。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(ai-coach): curated training knowledge module`

### Task 3: build_context 加 zone + 越野負荷；SYSTEM_PROMPT 注入知識 + 處方指示
- **ACTION**: 改 `context.py`：build_context 末尾加 zone 邊界與越野負荷摘要；SYSTEM_PROMPT 併入 `build_knowledge()` 並加處方輸出指示。
- **TEST FIRST**: `backend/tests/test_ai_context.py`
  ```python
  import sys, os, asyncio
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from datetime import date
  from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

  def _run(c): return asyncio.get_event_loop().run_until_complete(c)

  def test_context_includes_zones():
      async def _i():
          engine = create_async_engine("sqlite+aiosqlite:///:memory:")
          from backend.db.models import Base, Athlete, AthleteSettings
          async with engine.begin() as conn:
              await conn.run_sync(Base.metadata.create_all)
          S = async_sessionmaker(engine, expire_on_commit=False); s = S()
          s.add(Athlete(id=1, name="a", data_dir="/tmp")); await s.flush()
          s.add(AthleteSettings(athlete_id=1, effective_date=date(2025,1,1),
                                run_ftp_w=192, lthr=182))
          await s.commit()
          from backend.engine.ai.context import build_context, SYSTEM_PROMPT
          ctx = await build_context(s, 1)
          assert "區間" in ctx or "zone" in ctx.lower()
          assert "Threshold" in ctx or "閾值" in ctx
          # system prompt has knowledge + prescription instruction
          assert "Palladino" in SYSTEM_PROMPT
          assert "間歇" in SYSTEM_PROMPT
      _run(_i())
  ```
  Run: `/opt/homebrew/bin/pytest backend/tests/test_ai_context.py -q` — expect FAIL
- **IMPLEMENT**: `context.py`
  - SYSTEM_PROMPT 改為：
    ```python
    from backend.engine.ai.knowledge import build_knowledge
    SYSTEM_PROMPT = f"""你是一位專業的越野跑教練AI助理。請一律用繁體中文回覆。

    {build_knowledge()}

    根據運動員的訓練數據與上述知識，提供具體、可執行的建議。
    回覆結構：(1) 目前訓練狀態判讀 (2) 建議今天/近期該做的 zone（附功率W或心率bpm範圍）
    (3) 若需間歇，給出處方（時間 × 目標瓦數或zone × 組數 × 恢復）。
    使用條列式、簡潔清晰。"""
    ```
  - build_context 末尾（return 前）加：
    ```python
    from backend.engine.ai.zones import compute_zones
    if settings:
        z = compute_zones(settings.run_ftp_w or settings.ftp_w, settings.lthr)
        if z["power"]:
            lines.append("\n=== 功率區間 (W) ===")
            for p in z["power"]:
                hi = f"{p['high_w']:.0f}" if p["high_w"] else "+"
                lines.append(f"Z{p['zone']} {p['name']}: {p['low_w']:.0f}–{hi}")
        if z["hr"]:
            lines.append("\n=== 心率區間 (bpm) ===")
            for h in z["hr"]:
                hi = f"{h['high_bpm']:.0f}" if h["high_bpm"] else "+"
                lines.append(f"Z{h['zone']} {h['name']}: {h['low_bpm']:.0f}–{hi}")
    ```
  （越野負荷摘要可選：若有 trail hr_tss metric 可加總，初版以 zone 為主。）
- **MIRROR**: CONTEXT_ASSEMBLY, SYSTEM_PROMPT_INJECTION
- **GOTCHA**: `settings` 變數在既有 build_context 已定義（:27）；zone 區塊放同一 if 內或重用該變數。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(ai-coach): inject knowledge + zones into prompt/context`

### Task 4: 前端 quick-prompt 按鈕
- **ACTION**: 在 `AiChat` 輸入框上方加 quick-prompt chips，點擊即填入並送出。
- **TEST FIRST (gate)**: `cd frontend && npx tsc -b` 綠 + 瀏覽器點擊送出。
- **IMPLEMENT**: AiChat 內，輸入區上方加：
  ```tsx
  const QUICK_PROMPTS = ['今天該練什麼？', '我的間歇怎麼排？', '現在該做 zone 幾？']
  // 在 input 上方渲染：
  <div className="flex flex-wrap gap-2 px-3 pb-2">
    {QUICK_PROMPTS.map(q => (
      <button key={q} type="button"
        onClick={() => { setInput(q); setTimeout(send, 0) }}
        disabled={isStreaming}
        className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 transition">
        {q}
      </button>
    ))}
  </div>
  ```
  （`setInput`/`send`/`isStreaming` 為 AiChat 既有；`setTimeout(send,0)` 確保 input state 已更新。若 send 讀 input 閉包過舊，改為 `send(q)` 重載——見 GOTCHA。）
- **MIRROR**: AiChat 既有 input+send
- **GOTCHA**: `send` 讀 `input` 閉包；`setInput`+`setTimeout` 可能讀到舊值。穩妥做法：把 `send` 改成可選參數 `send(override?: string)`，quick-prompt 傳 `send(q)`。
- **VALIDATE**: `npx tsc -b && npm run build` 綠 + 手測
- **COMMIT**: `feat(ai-coach): quick-prompt buttons in chat`

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected | Edge? |
|---|---|---|---|
| compute_zones | ftp 192, lthr 182 | 7 power + 5 hr 區，邊界正確 | N |
| compute_zones | None/None | 空 | Y |
| build_knowledge | — | 含關鍵概念 | N |
| build_context | settings | 含 zone 區塊 | N |

### Edge Cases Checklist
- [ ] 無 settings → zone 空、context 不崩
- [ ] 只有 lthr 無 ftp → 只回 HR 區
- [ ] 最後一區開放上限 → high None

---

## Validation Commands

### Backend
```bash
cd "/Users/<user>/Projects/Archive Project/WKO5reverse"
/opt/homebrew/bin/pytest backend/tests/ -q
```
EXPECT: 全 pass（含新 3 個 ai 測試）

### Frontend
```bash
cd "/Users/<user>/Projects/Archive Project/WKO5reverse/frontend"
npx tsc -b && npm run build
```
EXPECT: 型別零錯誤、build 成功

### Integration (smoke)
```bash
cd "/Users/<user>/Projects/Archive Project/WKO5reverse"
/opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --port 8022 &
sleep 3
curl -s "http://localhost:8022/api/v1/ai/zones?athlete_id=1" | head -c 400
```
EXPECT: 回功率/心率 zone 邊界 JSON

### Manual
- [ ] AI 頁 quick-prompt 按鈕可點且送出
- [ ] 回覆含狀態判讀 + zone + 間歇處方（需 AI key；無 key 則僅驗證 context/prompt）

---

## Acceptance Criteria
- [ ] AC-1 zone 端點（test_ai_zones）
- [ ] AC-2 知識注入（test_ai_knowledge）
- [ ] AC-3 context 含 zone（test_ai_context）
- [ ] AC-4 前端 quick-prompt（build + 手測）
- [ ] 後端測試 + tsc + build 全綠

## Completion Checklist
- [ ] zone 沿用既有 def，無兩套
- [ ] 知識為靜態常數、無 API 成本
- [ ] 無 schema 變更
- [ ] 自足

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 知識與筆記脫節 | M | L | 標註手動同步點；集中於單一 knowledge.py |
| quick-prompt 閉包讀舊 input | M | M | send 改可選參數 send(override) |
| 處方品質依賴 LLM | M | M | 提供具體 zone 數值 + 知識降低幻覺 |

## Notes
zone % 沿用 `workouts.py` 既有 Coggan-style 定義；筆記若有 Palladino 個人化特例可後續覆寫（SRS Open Question）。使用者 profile 事實（CP≈192W 等）放 knowledge.py 常數，未來可改讀 settings。
