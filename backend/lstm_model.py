import torch
import torch.nn as nn

# Behavior sequence length: last 10 seconds of behavior, sampled every 1s
SEQUENCE_LENGTH = 10

# Canonical feature order shared by scorer.py (feature extraction + SHAP labels)
# and train_model.py (synthetic dataset columns). Keeping this list in one place
# avoids the two modules drifting out of sync on feature count/order.
FEATURE_NAMES = [
    "scroll_hizi_varyansi",
    "tereddut_skoru",
    "etkilesim_entropisi",
    "ivme_degisimi",
    "tiklama_yogunlugu",
    "odak_degisimi",
]

# Per-timestep features fed into the LSTM
NUM_FEATURES = len(FEATURE_NAMES)


class BehaviorLSTM(nn.Module):
    """LSTM classifier over a short behavior time-series.

    Input shape:  (batch, SEQUENCE_LENGTH, NUM_FEATURES)
    Output shape: (batch, 1) -- fraud probability in [0, 1]
    """

    def __init__(
        self,
        input_size: int = NUM_FEATURES,
        hidden_size: int = 32,
        num_layers: int = 2,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size, 16),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        lstm_out, _ = self.lstm(x)
        last_step = lstm_out[:, -1, :]
        return self.classifier(last_step)


def build_sequence(rows: list[list[float]]) -> torch.Tensor:
    """Builds the LSTM input from a session's real flush history.

    `rows` runs oldest -> newest and its last entry is the flush being scored
    right now. Longer histories are truncated to the most recent
    SEQUENCE_LENGTH steps; shorter ones are left-padded with the current row,
    so a session's very first flush produces the same constant sequence the
    old tiling helper produced, and each additional flush replaces one pad
    step with a real earlier observation.

    This is the inference path. It is what lets the model react to a
    *trajectory* -- behavior drifting from human to robotic mid-session --
    rather than only to the level of the latest snapshot.
    """
    if not rows:
        raise ValueError("build_sequence() en az bir satir gerektirir")
    window = [list(row) for row in rows[-SEQUENCE_LENGTH:]]
    padding = [list(window[-1])] * (SEQUENCE_LENGTH - len(window))
    seq = torch.tensor(padding + window, dtype=torch.float32)
    return seq.unsqueeze(0)  # (1, SEQUENCE_LENGTH, NUM_FEATURES)


def build_sequence_from_features(feature_vector: list[float]) -> torch.Tensor:
    """Tiles one feature snapshot across the sequence length.

    NO LONGER THE INFERENCE PATH -- see build_sequence() above, which feeds
    the model a session's actual flush history. This helper survives only as
    the degenerate single-observation case (it is exactly what
    build_sequence() produces from a one-row history) and as a convenience
    for ad-hoc scripts that hold a single feature vector and no history.
    """
    return build_sequence([feature_vector])
