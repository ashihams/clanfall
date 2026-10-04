from types import SimpleNamespace

from config import config
from memory.vector_store import InMemoryBackend, PineconeBackend, VectorStore, _cosine


class FakeIndex:
    """Shared serverless index stand-in: namespaces, lagging reads, lagging deletes."""

    def __init__(self, lag_reads: int = 0):
        self.data = {}            # namespace -> {id: (values, metadata)}
        self.lag_reads = lag_reads
        self.pending = {}         # namespace -> reads remaining before writes become visible
        self.ghosts = {}          # namespace -> rows still visible after delete
        self.deleted = []

    def upsert(self, vectors, namespace):
        ns = self.data.setdefault(namespace, {})
        for v in vectors:
            ns[v["id"]] = (v["values"], v["metadata"])
        self.pending[namespace] = self.lag_reads

    def query(self, vector, top_k, namespace, include_metadata):
        if self.pending.get(namespace, 0) > 0:
            self.pending[namespace] -= 1
            rows = {}
        else:
            rows = dict(self.data.get(namespace, {}))
        rows.update(self.ghosts.get(namespace, {}))
        scored = sorted(((i, _cosine(vector, vals), meta) for i, (vals, meta) in rows.items()),
                        key=lambda r: r[1], reverse=True)[:top_k]
        return SimpleNamespace(matches=[SimpleNamespace(id=i, score=s, metadata=dict(m)) for i, s, m in scored])

    def delete(self, delete_all, namespace):
        self.deleted.append(namespace)
        self.ghosts[namespace] = self.data.pop(namespace, {})   # deletes take a while to apply

    def describe_index_stats(self):
        return SimpleNamespace(namespaces={ns: SimpleNamespace(vector_count=len(r)) for ns, r in self.data.items()})


def _settings(**kw):
    return config.memory.pinecone.model_copy(update={"freshness_wait_s": 0.5, **kw})


def _docs():
    return (["f1", "f2"],
            ["Blue claimed Reactor but was seen in Electrical", "Green was near the body in Reactor"],
            [{"match_id": "m1", "subject": "Blue"}, {"match_id": "m1", "subject": "Green"}])


def test_namespace_isolation_and_wipe_leaves_other_data_alone():
    index = FakeIndex()
    index.data["__default__"] = {"aero-1": ([1.0] + [0.0] * 63, {"text": "AeroCortex episode"})}
    backend = PineconeBackend(settings=_settings(), index=index, namespace="clanfall-test")
    backend.add_documents(*_docs())
    hits = backend.query("where was Blue", n_results=2)
    assert set(hits["ids"][0]) == {"f1", "f2"} and backend.count() == 2
    assert "AeroCortex episode" not in hits["documents"][0]

    backend.reset()
    assert index.deleted == ["clanfall-test"] and backend.count() == 0
    assert "aero-1" in index.data["__default__"]           # someone else's vectors untouched


def test_vectors_from_a_wiped_match_never_come_back():
    index = FakeIndex()
    backend = PineconeBackend(settings=_settings(), index=index, namespace="clanfall-test")
    backend.add_documents(*_docs())
    backend.reset()                                         # server-side delete still "in progress"
    backend.add_documents(["g1"], ["Yellow did a task in MedBay"], [{"match_id": "m2"}])
    hits = backend.query("Blue Reactor Electrical", n_results=5)
    assert hits["ids"][0] == ["g1"]


def test_waits_for_fresh_writes():
    backend = PineconeBackend(settings=_settings(), index=FakeIndex(lag_reads=2), namespace="clanfall-test")
    backend.add_documents(*_docs())
    assert len(backend.query("Blue", n_results=2)["ids"][0]) == 2


def test_store_answers_from_local_mirror_when_remote_lags():
    remote = PineconeBackend(settings=_settings(freshness_wait_s=0.0), index=FakeIndex(lag_reads=100),
                             namespace="clanfall-test")
    store = VectorStore(remote_backend=remote, local_backend=InMemoryBackend())
    assert store.engine == "pinecone"
    store.add_documents(*_docs())
    assert len(store.query("Blue Electrical", n_results=2)["ids"][0]) == 2
    assert store.health()["pinecone"] == "ok" and store.health()["namespace"] == "clanfall-test"
    store.reset()
    assert store.count() == 0
