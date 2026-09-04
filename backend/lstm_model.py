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
    # --- Evasion-resistant kinematics/timing features -------------------
    # The four features above (variance/entropy style) are cheap for an
    # attacker to fake: a script that emits `x += gauss(6, 6)` per step
    # reproduces a "natural-looking" variance and entropy almost exactly,
    # and previously scored 9/100 ("Gercek Kullanici") in an end-to-end
    # replay against /api/analyze. The features below instead measure
    # *structure* that independent per-step noise does not have:
    #
    #   hiz_otokorelasyonu  real pointer motion carries momentum, so speed
    #                       is correlated with its own previous value.
    #                       IID jitter has ~zero autocorrelation.
    #   yon_tutarliligi     real motion is target-directed, so consecutive
    #                       move vectors point roughly the same way. IID
    #                       jitter picks a new direction every step (~0);
    #                       a linear script never turns at all (~1).
    #   zaman_kuantasyonu   scripted timers repeat the *same* millisecond
    #                       gap over and over; human input effectively
    #                       never does.
    #   duraklama_dagilimi  human inter-event gaps are heavy-tailed
    #                       (lognormal-ish, high spread); uniform-random
    #                       or fixed script delays are not.
    #
    # Reproducing these requires modelling human motor control, not adding
    # noise -- a materially higher bar. See scorer.py for the estimators
    # and train_model.py for the personas they are trained against.
    "hiz_otokorelasyonu",
    "yon_tutarliligi",
    "zaman_kuantasyonu",
    "duraklama_dagilimi",
    # --- Cross-channel synchronization -----------------------------------
    # Within-channel structure can be faked one channel at a time. These
    # measure how a single person's channels relate to each other: the cursor
    # arrives before the click, and the hand takes real time to move between
    # keyboard and mouse. Count/ratio statistics by design, so they remain
    # valid on thin flushes.
    #
    # A third candidate, channel simultaneity (typing and pointing at once),
    # was measured and dropped: it separated human from bot 0.07 vs 0.07 on
    # the synthetic personas -- neither emits overlapping channels -- so the
    # forest would never learn to weight it. A feature that measures nothing
    # is noise, not signal.
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


def build_sequence_from_features(feature_vector: list[float]) -> torch.Tensor:
    """Repeats a single feature snapshot across the sequence length.

    Real deployments would keep a rolling window of per-second snapshots;
    for a single /api/analyze call we only have the latest aggregate, so we
    tile it across the window to still exercise the temporal model.
    """
    seq = torch.tensor([feature_vector] * SEQUENCE_LENGTH, dtype=torch.float32)
    return seq.unsqueeze(0)  # (1, SEQUENCE_LENGTH, NUM_FEATURES)
