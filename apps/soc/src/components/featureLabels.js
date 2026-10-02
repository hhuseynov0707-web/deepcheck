// A short Turkish gloss for each of the twelve features, so the SOC screens can
// print "Zaman kuantalanması" beside the raw `zaman_kuantasyonu` instead of
// leaving an analyst to decode a snake_case identifier.
//
// These are LABELS, not new measurements. Every line restates what the feature
// already is in backend/lstm_model.py (FEATURE_NAMES and the block comment
// under it) and in CLAUDE.md's feature table; nothing here computes, rounds or
// reinterprets a number. The raw name is always printed next to the gloss, so
// what the backend calls a feature stays visible and greppable.
//
// The canonical name and order live in backend/lstm_model.py. This map is
// keyed by that name; a feature with no entry falls back to its raw name
// rather than to an invented description.
export const FEATURE_LABELS = {
  // Marginal statistics (1-6).
  scroll_hizi_varyansi: "Kaydırma hızı değişkenliği",
  tereddut_skoru: "Hareket öncesi duraksama",
  etkilesim_entropisi: "Olay aralığı entropisi",
  ivme_degisimi: "İmleç ivmesi değişkenliği",
  tiklama_yogunlugu: "Tıklama yoğunluğu",
  odak_degisimi: "Sekme odağı kaybı",
  // Structure and cross-channel (7-12).
  hiz_otokorelasyonu: "Hız otokorelasyonu",
  yon_tutarliligi: "Yön tutarlılığı",
  zaman_kuantasyonu: "Zaman kuantalanması",
  duraklama_dagilimi: "Duraklama dağılımı",
  tiklama_oncesi_hareket: "Tıklama öncesi hareket",
  kanal_gecis_gecikmesi: "Kanal geçiş gecikmesi",
};

export function featureLabel(name) {
  return FEATURE_LABELS[name] ?? null;
}
