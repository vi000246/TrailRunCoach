from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/v1/expr", tags=["expr"])


class ExprRequest(BaseModel):
    expr: str
    athlete_id: int = 1
    date_range_days: Optional[int] = 90


@router.post("/evaluate")
async def evaluate_expr(req: ExprRequest):
    return {
        "error": "Expression parser not yet implemented",
        "expr": req.expr,
        "hint": "Use dedicated endpoints: /workouts/{id}/mmp, /pmc",
    }
