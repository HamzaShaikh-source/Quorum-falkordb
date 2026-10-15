import asyncio
import threading
import time
from typing import Dict, List, Optional, Any
from app.models.events import SSEEventEnvelope


class TenantEventBuffer:
    def __init__(self, tenant: str, max_buffer_size: int = 500):
        self._lock = threading.Lock()
        self.tenant = tenant
        self.max_buffer_size = max_buffer_size
        self.current_seq = 0
        self.current_epoch = 1
        self.history: List[SSEEventEnvelope] = []
        self.subscribers: List[asyncio.Queue] = []

    def publish(self, event_type: str, data: Dict[str, Any], request_id: Optional[str] = None, epoch: Optional[int] = None) -> SSEEventEnvelope:
        with self._lock:
            self.current_seq += 1
            if epoch is None and isinstance(data, dict):
                epoch = data.get("epoch")
            if epoch is not None:
                try:
                    self.current_epoch = max(self.current_epoch, int(epoch))
                except (ValueError, TypeError):
                    pass

            envelope = SSEEventEnvelope(
                seq=self.current_seq,
                ts=int(time.time() * 1000),
                epoch=self.current_epoch,
                type=event_type,
                request_id=request_id,
                data=data,
            )

            self.history.append(envelope)
            if len(self.history) > self.max_buffer_size:
                self.history.pop(0)

            for q in list(self.subscribers):
                try:
                    q.put_nowait(envelope)
                except Exception:
                    pass

            return envelope

    def get_events_since(self, last_event_id: int) -> Optional[List[SSEEventEnvelope]]:
        with self._lock:
            if last_event_id > self.current_seq:
                return None  # The client is ahead of this server: the server restarted, so the client must reload.
            if not self.history:
                return []
            earliest_seq = self.history[0].seq
            if last_event_id < earliest_seq - 1:
                return None  # Client missed too many events, resync required

            return [e for e in self.history if e.seq > last_event_id]

    def subscribe(self) -> asyncio.Queue:
        with self._lock:
            q: asyncio.Queue = asyncio.Queue()
            self.subscribers.append(q)
            return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


class EventBusService:
    def __init__(self):
        self._lock = threading.Lock()
        self.tenants: Dict[str, TenantEventBuffer] = {}

    def get_tenant_buffer(self, tenant: str) -> TenantEventBuffer:
        with self._lock:
            if tenant not in self.tenants:
                self.tenants[tenant] = TenantEventBuffer(tenant)
            return self.tenants[tenant]

    def publish(self, tenant: str, event_type: str, data: Dict[str, Any], request_id: Optional[str] = None, epoch: Optional[int] = None) -> SSEEventEnvelope:
        buf = self.get_tenant_buffer(tenant)
        return buf.publish(event_type, data, request_id, epoch)


event_bus = EventBusService()
