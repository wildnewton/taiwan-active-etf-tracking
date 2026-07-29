import manager_intent
import nightly_pipeline


def test_manager_intent_has_no_persistence_compatibility_api():
    assert not hasattr(manager_intent, "generate_manager_intent_rollups")
    assert not hasattr(nightly_pipeline, "generate_manager_intent_rollups")
