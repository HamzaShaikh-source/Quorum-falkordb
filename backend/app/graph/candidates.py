"""Mutation-aware healthy fact index. Visibility is always evaluated from the current viewer."""
from app.models.common import NodeKind, Status


class NodeRegistry(dict):
    def __init__(self):
        super().__init__()
        self.facts: dict = {}
        self.order: dict = {}
        self.sequence = 0
        self.revision = 0

    def changed(self, node):
        self.revision += 1
        if self.get(node.id) is not node:
            return
        if node.kind == NodeKind.FACT and node.status == Status.HEALTHY and not node.stale:
            self.facts[node.id] = node
        else:
            self.facts.pop(node.id, None)

    def __setitem__(self, key, node):
        if key not in self:
            self.order[key] = self.sequence
            self.sequence += 1
        super().__setitem__(key, node)
        node._on_change = self.changed
        self.changed(node)

    def __delitem__(self, key):
        super().__delitem__(key)
        self.facts.pop(key, None)
        self.order.pop(key, None)
        self.revision += 1

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def clear(self):
        super().clear()
        self.facts.clear()
        self.order.clear()
        self.revision += 1

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def candidates(self, viewer):
        # Mutable scope lists and link changes never use a cached authorization decision.
        facts_snapshot = list(self.facts.values())
        return [n for n in sorted(facts_snapshot, key=lambda n: self.order.get(n.id, 0))
                if n.status == Status.HEALTHY and not n.stale and viewer.can_see(n)]
