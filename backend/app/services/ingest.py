import uuid
from typing import Optional
from app.graph.queries import GraphStore
from app.models.common import NodeKind, Status, SourceKind
from app.models.graph import GraphNode, SourceInfo
from app.services.event_bus import event_bus


class IngestGatewayService:
    """
    Ingest Gateway:
    Sets Source.origin ONLY from the authenticated channel or credential, NEVER from a payload or agent.
    """
    def ingest_source(
        self,
        store: GraphStore,
        kind: SourceKind,
        title: str,
        url: str,
        version: str,
        auth_channel: str,  # "doc:policy-repo", "webhook:partner-feed", "chat:support-slack"
        tenant: str = "acme",
        request_id: Optional[str] = None,
    ) -> GraphNode:
        if not auth_channel or not isinstance(auth_channel, str) or not auth_channel.strip():
            raise ValueError("auth_channel must be a non-empty authenticated string")

        title_clean = str(title or "").strip()[:120]
        url_clean = str(url or "").strip()[:500]
        version_clean = str(version or "").strip()[:60]
        source_id = f"src_{uuid.uuid4().hex[:6]}"

        source_node = GraphNode(
            id=source_id,
            kind=NodeKind.SOURCE,
            label=title_clean[:60] or "Source",
            status=Status.HEALTHY,
            stale=False,
            origin=auth_channel.strip(),  # Injected securely from gateway auth context
            source=SourceInfo(kind=kind, title=title_clean, url=url_clean, version=version_clean),
        )

        store.add_node(source_node, request_id)
        event_bus.publish(tenant, "node_added", {"node": source_node.model_dump()}, request_id)
        return source_node


ingest_gateway = IngestGatewayService()
