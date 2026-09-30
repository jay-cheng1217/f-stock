from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.services.agent_arena_service import load_agent_arena_detail, load_agent_arena_status

router = APIRouter(tags=["agent-arena"])


@router.get("/api/agent-arena/status")
def get_agent_arena_status():
    return JSONResponse(content=load_agent_arena_status())


@router.get("/api/agent-arena/agents/{agent_id}")
def get_agent_arena_agent_detail(agent_id: str):
    return JSONResponse(content=load_agent_arena_detail(agent_id))
