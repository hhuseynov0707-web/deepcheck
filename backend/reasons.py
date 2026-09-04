"""Human-readable reason codes and evidence state.

The models produce a number and a SHAP vector; a fraud analyst -- and a
competition judge -- needs a sentence. This module turns the ranked SHAP
contributions into short Turkish explanations of *why* a session was flagged
or allowed, and classifies how much the verdict can be trusted.

Two design rules make these defensible rather than decorative:

  * Direction comes from the SHAP sign, never from a hardcoded guess about
    which way a feature "should" point. A positive fraud-class SHAP value
    means this feature pushed the prediction toward automation for THIS
    session; the phrase is chosen from that sign. So the explanation cannot
    silently disagree with the model.
  * "Not enough evidence" is a first-class outcome, not a quiet lean toward
    either class. A sparse or keyboard-only session gets an explicit
    insufficient-signal reason instead of a fabricated automation accusation
    -- the exact failure (a 5-keystroke session scored 89.2 "bot") this
    surfaces and guards against.
"""

# Bumped whenever the reasoning or policy semantics change, so a stored or
# signed decision can be traced back to the logic that produced it.
POLICY_VERSION = "2026.09"

# Keep in sync with main.MIN_SIGNAL_FOR_AUTO_APPROVE: below this a flush is
# mostly neutral fallbacks rather than measurement, so its verdict is treated
# as "unobserved", not "clean".
SIGNAL_FLOOR = 0.35

# Per-feature phrases in both directions. The "bot" phrase is used when the
# feature pushed this session toward automation, the "human" phrase when it
# pushed toward a real user. Phrases describe the *behaviour*, not the raw
# number, so a non-technical reader understands them.
FEATURE_PHRASES = {
    "scroll_hizi_varyansi": {
        "bot": "Kaydırma hızı fazla düzenli — insan kaydırmasındaki değişkenlik yok",
        "human": "Kaydırma hızı doğal biçimde değişken",
    },
    "tereddut_skoru": {
        "bot": "Eylem öncesi neredeyse hiç duraksama yok",
        "human": "Eylemler öncesi doğal duraksamalar var",
    },
    "etkilesim_entropisi": {
        "bot": "Olay zamanlaması kanal içinde alışılmadık derecede düzenli",
        "human": "Olay zamanlaması insan davranışıyla tutarlı düzensizlikte",
    },
    "ivme_degisimi": {
        "bot": "Fare ivmesi tekrarlayıcı — gerçek el hareketinin değişkenliği yok",
        "human": "Fare ivmesi gerçek el hareketiyle uyumlu",
    },
    "tiklama_yogunlugu": {
        "bot": "Tıklama yoğunluğu otomasyona benzer bir cadence gösteriyor",
        "human": "Tıklama yoğunluğu insan etkileşimiyle uyumlu",
    },
    "odak_degisimi": {
        "bot": "Sekme odak davranışı olağandışı",
        "human": "Sekme odak davranışı normal",
    },
    "hiz_otokorelasyonu": {
        "bot": "Fare hızında insan hareketine özgü süreklilik (ivmelenme) görülmüyor",
        "human": "Fare hızı, gerçek harekette beklenen momentumu taşıyor",
    },
    "yon_tutarliligi": {
        "bot": "Hareket yönü hedefe yönelik değil — insan erişim hareketiyle uyumsuz",
        "human": "Hareket yönü hedefe yönelik ve tutarlı",
    },
    "zaman_kuantasyonu": {
        "bot": "Aynı milisaniyelik aralık tekrar tekrar görülüyor — betik zamanlayıcısı işareti",
        "human": "Olay aralıkları insan girdisinde beklendiği gibi tekrarlanmıyor",
    },
    "duraklama_dagilimi": {
        "bot": "Duraklama süreleri fazla tekdüze — insan duraklamalarının ağır kuyruğu yok",
        "human": "Duraklama süreleri insan davranışındaki gibi geniş dağılımlı",
    },
    "tiklama_oncesi_hareket": {
        "bot": "Tıklamalar imleç hareketi olmadan gerçekleşiyor — betik tıklaması işareti",
        "human": "Tıklamalar öncesinde doğal imleç hareketi var",
    },
    "kanal_gecis_gecikmesi": {
        "bot": "Klavye ve fare arasındaki geçiş zamanlaması insan ritmine uymuyor",
        "human": "Klavye–fare geçiş zamanlaması insan ritmiyle uyumlu",
    },
}

INSUFFICIENT_SIGNAL_REASON = (
    "Karar için yeterli davranış verisi toplanmadı (az etkileşim / yalnızca klavye)"
)

# Evidence-state labels shown to the analyst. These describe how far the
# verdict can be trusted, separately from the risk number itself.
EVIDENCE_STATES = {
    "YETERLI": "Yeterli sinyal",
    "YETERSIZ": "Yetersiz sinyal",
    "BELIRSIZ": "Anormal ama belirsiz",
    "YUKSEK_GUVEN": "Yüksek güvenli otomasyon",
}


def evidence_state(risk_score: float, signal_sufficiency: float) -> str:
    """Classifies how conclusive the verdict is (returns a key of EVIDENCE_STATES).

    Signal is checked first on purpose: a high risk score built on almost no
    data is not "high-confidence automation", it is "we could not observe
    enough to say", and must not be dressed up as the former.
    """
    if signal_sufficiency < SIGNAL_FLOOR:
        return "YETERSIZ"
    if risk_score >= 80:
        return "YUKSEK_GUVEN"
    if risk_score >= 60:
        return "BELIRSIZ"
    return "YETERLI"


def build_reason_codes(
    signed_impacts: list[dict],
    signal_sufficiency: float,
    max_each: int = 3,
) -> dict:
    """Turns ranked signed SHAP contributions into flagged/allowed reason lists.

    `signed_impacts` are dicts with keys `feature`, `direction` ("bot"/"human")
    and `impact` (absolute magnitude), already sorted most-important first.
    """
    flagged: list[str] = []
    allowed: list[str] = []

    for item in signed_impacts:
        phrases = FEATURE_PHRASES.get(item["feature"])
        if phrases is None:
            continue
        if item["direction"] == "bot" and len(flagged) < max_each:
            flagged.append(phrases["bot"])
        elif item["direction"] == "human" and len(allowed) < max_each:
            allowed.append(phrases["human"])

    # An unobserved session is allowed on the *absence* of automation evidence,
    # not on positive human evidence -- say so first and explicitly.
    if signal_sufficiency < SIGNAL_FLOOR:
        allowed.insert(0, INSUFFICIENT_SIGNAL_REASON)

    return {"flagged": flagged, "allowed": allowed}
