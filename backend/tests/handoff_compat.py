"""Handoffs now live on the tenant's graph store. These proxies keep older tests readable: they act on the acme store."""
from app.graph.queries import get_graph_store


class _Handoffs:
    def __init__(self, tenant="acme"):
        self.tenant = tenant

    def _l(self):
        return get_graph_store(self.tenant).handoffs

    def __getattr__(self, name):
        return getattr(self._l(), name)

    def __iter__(self):
        return iter(self._l())

    def __len__(self):
        return len(self._l())

    def __getitem__(self, i):
        return self._l()[i]


class _Lock:
    def __init__(self, tenant="acme"):
        self.tenant = tenant

    def __enter__(self):
        return get_graph_store(self.tenant).handoff_lock.__enter__()

    def __exit__(self, *a):
        return get_graph_store(self.tenant).handoff_lock.__exit__(*a)


def make_proxies(tenant):
    return _Handoffs(tenant), _Lock(tenant)


_handoff_store, _handoff_lock = make_proxies("acme")
