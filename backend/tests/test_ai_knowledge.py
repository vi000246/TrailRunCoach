import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.ai.knowledge import build_knowledge


def test_knowledge_includes_key_concepts():
    kb = build_knowledge()
    for term in ["Palladino", "間歇", "CP", "無氧", "爬升"]:
        assert term in kb, f"missing concept: {term}"


def test_knowledge_is_nonempty_string():
    kb = build_knowledge()
    assert isinstance(kb, str) and len(kb) > 100
