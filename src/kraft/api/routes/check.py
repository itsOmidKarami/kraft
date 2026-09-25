"""`POST /templates/check`: one unsaved config file, checked as saving it
would be, with every issue positioned against the buffer (VS Code extension
spec, "Config files"). Reads; never writes."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft.api import api_router, config_check
from kraft.templates import positions


class CheckBody(BaseModel):
    #: Relative to the templates directory: `policy.yaml`, `chains/<id>.yaml`.
    file: str
    text: str


@api_router.post("/templates/check")
async def check_config(body: CheckBody, request: Request):
    st = request.app.state
    ctx = config_check.context(st)
    try:
        issues = await asyncio.to_thread(config_check.check, body.file, body.text, ctx)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    buffers = {st.templates_dir / body.file: body.text}
    return {"issues": [positions.issue_view(i, buffers) for i in issues]}
