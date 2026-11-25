import numpy as np

from scripts.evaluate_paysim_history import GraphHistory, chronological_steps, temporal_split


def test_same_step_edges_are_absent_until_next_step_and_expire():
    history = GraphHistory(window=2)
    first = history.score_then_update(1, ["alice", "alice"], ["bob", "carol"])
    second = history.score_then_update(2, ["alice", "bob"], ["bob", "alice"])
    later = history.score_then_update(5, ["alice"], ["bob"])
    np.testing.assert_array_equal(first, np.zeros((2, 4)))
    np.testing.assert_allclose(np.expm1(second), [[2, 0, 0, 1], [0, 1, 2, 0]], atol=1e-6)
    np.testing.assert_array_equal(later, np.zeros((1, 4)))


def test_whole_step_spanning_csv_chunks_is_processed_once(tmp_path):
    source = tmp_path / "tiny.csv"
    source.write_text(
        "step,type,amount,nameOrig,oldbalanceOrg,nameDest,oldbalanceDest,isFraud\n"
        "1,TRANSFER,1,a,2,b,0,0\n"
        "1,TRANSFER,1,c,2,d,0,0\n"
        "1,TRANSFER,1,a,2,d,0,1\n"
        "2,TRANSFER,1,d,2,a,0,0\n"
        "4,CASH_OUT,1,c,2,a,0,1\n",
        encoding="utf-8",
    )
    result = [(step, len(payments)) for step, payments in chronological_steps(source, None, chunksize=2)]
    assert result == [(1, 3), (2, 1), (4, 1)]


def test_train_validation_and_test_share_no_hours():
    steps = np.repeat(np.arange(12), [9, 1, 25, 3, 4, 1, 2, 3, 4, 5, 6, 7])
    train, validation, test = temporal_split(steps)
    assert (train | validation | test).all()
    assert all(mask.any() for mask in (train, validation, test))
    assert steps[train].max() < steps[validation].min() <= steps[validation].max() < steps[test].min()
