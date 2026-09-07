"""festin/service/api/scans.py -- Scan execution + status FastAPI router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import get_db, get_scheduler

router = APIRouter()


@router.get("/list")
async def list_scans(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    db = await get_db()
    result = await db.get_all_scans(offset=offset, limit=limit)
    return {"scans": result["scans"], "total": result["total"]}


@router.get("/domain/{domain_id}")
async def domain_scans(domain_id: int, limit: int = 50) -> list[dict[str, Any]]:
    db = await get_db()
    rows = await db.get_domain_scans(domain_id, limit=limit)
    return rows


@router.post("/trigger/{domain_id}")
async def trigger_scan(domain_id: int) -> dict[str, Any]:
    db = await get_db()
    dom = await db.get_domain(domain_id)
    if dom is None:
        raise HTTPException(status_code=404, detail="Domain not found") from None
    scheduler = get_scheduler()
    result = await scheduler.trigger_scan(domain_id, dom["domain_name"])
    return {"scan_result": result}


@router.get("/overview")
async def dashboard_overview() -> dict[str, Any]:
    db = await get_db()
    return await db.get_dashboard_overview()
