import asyncio
import json
import time
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from app import db
from app.auth import get_auth_context, AuthContext, resolve_token
from app.config import settings
from app.graph.queries import get_graph_store
from app.services import workspace as ws
from app.services.event_bus import event_bus
from app.services.event_scope import event_visible

router = APIRouter()


@router.get("/events")
async def sse_events(
    last_event_id: Optional[int] = Header(None, alias="Last-Event-ID"),
    query_last_id: Optional[int] = Query(None, alias="last_event_id"),
    auth: AuthContext = Depends(get_auth_context),
):
    effective_last_id = last_event_id if last_event_id is not None else query_last_id
    buf = event_bus.get_tenant_buffer(auth.tenant)
    store = get_graph_store(auth.tenant)
    def current_viewer():
        info = resolve_token(auth.token)
        if info is None or info.get("member_id") != auth.member_id:
            return None
        return ws.viewer_for(store, auth.member_id)

    async def event_generator():
        queue = buf.subscribe()
        last_replayed_seq = effective_last_id or 0
        try:
            # Flush the response headers right away so clients can tell the stream is open before the first real event.
            yield f"event: heartbeat\ndata: {{\"ts\": {int(time.time() * 1000)}}}\n: keep-alive\n\n"
            # Check re-sync
            if effective_last_id is not None:
                missed = buf.get_events_since(effective_last_id)
                if missed is None:
                    yield "event: resync_required\ndata: {}\n\n"
                    return
                for ev in missed:
                    viewer = current_viewer()
                    if viewer is None:
                        return
                    if not event_visible(store, viewer, ev):
                        continue
                    last_replayed_seq = max(last_replayed_seq, ev.seq)
                    yield f"id: {ev.seq}\nevent: {ev.type}\ndata: {json.dumps(ev.model_dump())}\n\n"

            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if ev.seq <= last_replayed_seq:
                        continue
                    viewer = current_viewer()
                    if viewer is None:
                        return
                    if not event_visible(store, viewer, ev):
                        continue
                    last_replayed_seq = max(last_replayed_seq, ev.seq)
                    yield f"id: {ev.seq}\nevent: {ev.type}\ndata: {json.dumps(ev.model_dump())}\n\n"
                except asyncio.TimeoutError:
                    if current_viewer() is None:
                        return
                    if settings.PERSISTENCE == "falkordb" and not db.check_health()["up"]:
                        # Tell the console why, then end the stream; it reconnects and is blocked until FalkorDB is back.
                        yield 'event: error\ndata: {"code": "FALKORDB_UNAVAILABLE"}\n\n'
                        return
                    # Heartbeat
                    now_ms = int(time.time() * 1000)
                    yield f"event: heartbeat\ndata: {{\"ts\": {now_ms}}}\n\n"
        finally:
            buf.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
