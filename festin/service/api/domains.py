"""festin/service/api/domains.py -- Domain CRUD FastAPI router."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/list")
async def list_domains(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    db = await get_db()
    result = await db.list_domains(offset=offset, limit=limit)
    return {"domains": result["domains"], "total": result["total"]}


@router.post("/add")
async def add_domain(domain_name: str) -> dict[str, Any]:
    db = await get_db()
    try:
        id_ = await db.create_domain(domain_name)
        return {"id": id_, "domain": domain_name}
    except Exception:
        raise HTTPException(status_code=409, detail="Domain already exists")


@router.delete("/remove/{domain_id}")
async def remove_domain(domain_id: int) -> dict[str, Any]:
    db = await get_db()
    success = await db.delete_domain(domain_id)
    if not success:
        raise HTTPException(status_code=404, detail="Domain not found")
    return {"deleted": True}


@router.post("/rescan/{domain_id}")
async def rescan_domain(domain_id: int) -> dict[str, Any]:
    db = await get_db()
    dom = await db.get_domain(domain_id)
    if dom is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    scheduler = get_scheduler()
    result = await scheduler.trigger_scan(domain_id, dom["domain_name"])
    return {"scan_result": result}
