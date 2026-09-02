"""Tests for how the LSTM's input sequence is built.

The LSTM previously received the same feature vector tiled across all
SEQUENCE_LENGTH timesteps with +/-0.02 noise, both in training and at serve
time. On effectively constant input an LSTM has no temporal signal to learn
from -- it degenerates into an expensive MLP -- while CLAUDE.md describes it
as analysing behaviour as a time series.

The per-flush features are already persisted on BehaviorData, so a session's
real history is available; these tests pin the contract for using it.
"""

import numpy as np

from lstm_model import NUM_FEATURES, SEQUENCE_LENGTH, build_sequence_from_features

CURRENT = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]


def _flush(value: float) -> list[float]:
    return [value] * NUM_FEATURES


def test_shape_is_always_the_model_input_shape():
    for history in (None, [], [_flush(0.1)], [_flush(0.1)] * 50):
        seq = build_sequence_from_features(CURRENT, history)
        assert tuple(seq.shape) == (1, SEQUENCE_LENGTH, NUM_FEATURES)


def test_current_flush_is_always_last():
    """The newest observation is what the rest of the pipeline scores."""
    seq = build_sequence_from_features(CURRENT, [_flush(0.1), _flush(0.2)])
    assert np.allclose(seq[0, -1].tolist(), CURRENT)


def test_history_is_used_in_chronological_order():
    history = [_flush(0.1), _flush(0.2), _flush(0.3)]
    seq = build_sequence_from_features(CURRENT, history)[0].tolist()

    # Oldest history entry must appear before the newest, and both before current.
    tail = seq[-4:]
    assert np.allclose(tail[0], _flush(0.1))
    assert np.allclose(tail[1], _flush(0.2))
    assert np.allclose(tail[2], _flush(0.3))
    assert np.allclose(tail[3], CURRENT)


def test_only_the_most_recent_window_is_kept():
    """A long-running session must not feed the model ancient behaviour."""
    history = [_flush(i / 100) for i in range(40)]
    seq = build_sequence_from_features(CURRENT, history)[0].tolist()

    # The window holds SEQUENCE_LENGTH-1 history entries plus current.
    expected_oldest = _flush((40 - (SEQUENCE_LENGTH - 1)) / 100)
    assert np.allclose(seq[0], expected_oldest)


def test_a_varying_session_produces_varying_timesteps():
    """The whole point: real history must make the timesteps differ.

    Tiling produced a constant sequence, which is what left the LSTM with no
    temporal signal.
    """
    history = [_flush(0.1 * i) for i in range(1, SEQUENCE_LENGTH)]
    seq = build_sequence_from_features(CURRENT, history)[0].numpy()

    per_timestep_variance = seq.var(axis=0).mean()
    assert per_timestep_variance > 0.01, (
        "timesteps are effectively constant -- the LSTM has no sequence to learn"
    )


def test_new_session_with_no_history_still_works():
    """A first flush has nothing to look back on; tiling is correct there."""
    seq = build_sequence_from_features(CURRENT, None)[0].numpy()
    assert np.allclose(seq.var(axis=0), 0.0)
    assert np.allclose(seq[-1], CURRENT)
