from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

websocket_router = APIRouter()


@websocket_router.websocket("/ws/projects/{project_id}/events")
async def project_events(project_id: UUID, websocket: WebSocket) -> None:
    await websocket.accept()
    broker = websocket.app.state.container.event_broker
    subscription = broker.subscribe(project_id=project_id)
    try:
        while True:
            event = await subscription.queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    finally:
        subscription.close()


@websocket_router.websocket("/ws/sessions/{session_id}/output")
async def session_output(session_id: UUID, websocket: WebSocket) -> None:
    await websocket.accept()
    broker = websocket.app.state.container.event_broker
    subscription = broker.subscribe(session_id=session_id)
    try:
        while True:
            event = await subscription.queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    finally:
        subscription.close()


@websocket_router.websocket("/ws/sessions/{session_id}/events")
async def session_events(session_id: UUID, websocket: WebSocket) -> None:
    await websocket.accept()
    broker = websocket.app.state.container.event_broker
    subscription = broker.subscribe(session_id=session_id)
    try:
        while True:
            event = await subscription.queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    finally:
        subscription.close()
