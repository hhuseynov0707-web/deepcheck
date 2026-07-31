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


def build_sequence_from_features(feature_vector: list[float]) -> torch.Tensor:
    """Repeats a single feature snapshot across the sequence length.

    Real deployments would keep a rolling window of per-second snapshots;
    for a single /api/analyze call we only have the latest aggregate, so we
    tile it across the window to still exercise the temporal model.
    """
    seq = torch.tensor([feature_vector] * SEQUENCE_LENGTH, dtype=torch.float32)
    return seq.unsqueeze(0)  # (1, SEQUENCE_LENGTH, NUM_FEATURES)
