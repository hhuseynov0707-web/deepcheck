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
    # --- Evasion-resistant kinematics and timing --------------------------
    # The six features above are marginal statistics -- variances, entropies,
    # means -- and an attacker reproduces them by emitting independent
    # per-step gaussian noise. Measured: a straight-line bot with two pixels
    # of jitter halved its risk score and was approved. Those features answer
    # "does this look noisy like a person?", and noise is free.
    #
    # These measure STRUCTURE that independent noise does not have:
    #
    #   hiz_otokorelasyonu  real pointer motion carries momentum, so speed
    #                       correlates with its own previous value. IID
    #                       jitter has ~zero autocorrelation.
    #   yon_tutarliligi     real motion is target-directed, so consecutive
    #                       move vectors point roughly the same way. IID
    #                       jitter re-rolls direction every step (~0.5 after
    #                       mapping); a linear script never turns (~1).
    #   zaman_kuantasyonu   scripted timers repeat the SAME millisecond gap
    #                       over and over. Human input, dispatched on real
    #                       hardware timing, effectively never does.
    #   duraklama_dagilimi  human gaps are heavy-tailed (mostly quick,
    #                       occasionally long), so their coefficient of
    #                       variation is high. A fixed delay gives ~0 and a
    #                       uniform(a,b) delay is capped well below human.
    #
    # Reproducing these requires modelling human motor control rather than
    # adding noise, which is a materially higher bar.
    "hiz_otokorelasyonu",
    "yon_tutarliligi",
    "zaman_kuantasyonu",
    "duraklama_dagilimi",
    # --- Cross-channel structure ------------------------------------------
    # Within-channel structure can be faked one channel at a time. These
    # measure how one person's channels relate to each other: the cursor
    # arrives before the click, and a hand takes real time to move between
    # keyboard and mouse. Count and ratio statistics by design, so they stay
    # valid on thin flushes where variance estimates would be noise.
    "tiklama_oncesi_hareket",
    "kanal_gecis_gecikmesi",
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
