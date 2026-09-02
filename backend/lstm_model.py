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


def build_sequence_from_features(
    feature_vector: list[float], history: list[list[float]] | None = None
) -> torch.Tensor:
    """Build the LSTM's input window for one scoring call.

    The window is the session's most recent SEQUENCE_LENGTH flushes, oldest
    first, ending with the flush being scored. `history` is the prior
    per-flush feature vectors in chronological order -- BehaviorData persists
    exactly these six columns, so a live session's real window is already
    available and only needed reading.

    A session with less history than the window is left-padded with its oldest
    available observation. On a first flush that degenerates to tiling the
    current vector, which is correct there: no past exists to look at. Tiling
    on EVERY flush, which this did unconditionally before, left the model with
    no temporal signal at all -- an LSTM over a constant sequence is just an
    expensive MLP wearing a recurrent coat.
    """
    window = [list(row) for row in (history or [])][-(SEQUENCE_LENGTH - 1) :]
    window.append(list(feature_vector))

    if len(window) < SEQUENCE_LENGTH:
        window = [list(window[0])] * (SEQUENCE_LENGTH - len(window)) + window

    seq = torch.tensor(window, dtype=torch.float32)
    return seq.unsqueeze(0)  # (1, SEQUENCE_LENGTH, NUM_FEATURES)
