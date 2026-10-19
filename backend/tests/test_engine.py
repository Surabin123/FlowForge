import pytest
from app.engine import validate_dag, WorkflowError

def test_dependency_order_and_independent_nodes():
    order=validate_dag([{"id":"a"},{"id":"b"},{"id":"c"}],[{"source":"a","target":"c"},{"source":"b","target":"c"}])
    assert order.index("a")<order.index("c") and order.index("b")<order.index("c")

def test_cycle_is_rejected():
    with pytest.raises(WorkflowError,match="cycle"):
        validate_dag([{"id":"a"},{"id":"b"}],[{"source":"a","target":"b"},{"source":"b","target":"a"}])

def test_unknown_dependency_is_rejected():
    with pytest.raises(WorkflowError,match="existing node"):
        validate_dag([{"id":"a"}],[{"source":"a","target":"b"}])

def test_duplicate_node_ids_are_rejected():
    with pytest.raises(WorkflowError,match="unique id"):
        validate_dag([{"id":"a"},{"id":"a"}],[])
