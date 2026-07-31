# DeepCheck MVP — CLAUDE.md

## Layihə haqqında

DeepCheck — istifadəçi davranışını real vaxtda analiz edərək bot və insan arasındakı fərqi 0-100 arasında risk skoru ilə müəyyən edən bir süni zəka SDK-sıdır. Layihə **Teknofest Finansal Teknolojiler Yarışması** üçün hazırlanır.

> ÖNƏMLİ: Bu layihə Teknofest münsiflər heyətinə təqdim olunacaq. Buna görə **bütün UI mətnləri, etiketlər, düymələr, dashboard yazıları, xəta mesajları və istifadəçiyə görünən hər şey TÜRKCƏ olmalıdır.** Kod daxilindəki dəyişən adları, funksiya adları və şərhlər ingilis dilində qala bilər.

---

## Tech Stack

| Hissə | Texnologiya | Qeyd |
|---|---|---|
| Backend | FastAPI (Python 3.11) | Async, yüksək performanslı |
| Database | PostgreSQL | Session və davranış məlumatları |
| ML Model | Random Forest + Isolation Forest + LSTM | sklearn + PyTorch |
| Real-time | REST API polling (hər 2 saniyə) | Frontend fetch ilə |
| Deploy | Docker Compose | `docker-compose up` ilə hər şey qalxır |
| Frontend | React + Vite | Müasir, sürətli |
| Styling | Tailwind CSS | Dark theme, responsive |
| Qrafiklər | D3.js | Risk score vizualizasiyası |

---

## Folder Strukturu

```
deepcheck-mvp/
├── CLAUDE.md                  # Bu fayl
├── docker-compose.yml         # Bütün servisləri qaldırır
├── sdk/
│   └── deepcheck.js           # Brauzer SDK — davranış toplayır
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                # FastAPI app, endpointlər
│   ├── database.py            # PostgreSQL bağlantısı (SQLAlchemy)
│   ├── models.py              # DB modelləri
│   ├── scorer.py              # Feature extraction + risk skoru
│   ├── lstm_model.py          # PyTorch LSTM modeli
│   └── train_model.py         # Sintetik data + model training
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── App.jsx
        ├── pages/
        │   ├── Demo.jsx       # Ödəmə formu demo səhifəsi
        │   └── Dashboard.jsx  # SOC dashboard
        └── components/
            ├── RiskBadge.jsx
            ├── SessionTable.jsx
            └── RiskChart.jsx  # D3.js qrafik
```

---

## Modulların Vəzifəsi

### sdk/deepcheck.js
- Brauzerdə işləyir, `<script>` tegi ilə əlavə edilir
- Hər 2 saniyədə bir toplayır: mouse trajectory, click timing, scroll ritmi, hesitation intervalları
- `POST /api/analyze` endpoint-ə JSON göndərir
- `window.DeepCheck.init(sessionId)` ilə aktivləşdirilir

### backend/main.py
Endpointlər:
- `POST /api/analyze` → davranış datasını alır, risk skoru qaytarır
- `GET /api/score/{session_id}` → session tarixçəsi
- `GET /api/sessions` → bütün sessionlar (dashboard üçün)
- `GET /api/health` → sistem sağlamlığı

### backend/scorer.py
Çıxarılan featurelər:
- `scroll_variance` — scroll sürətinin dəyişkənliyi
- `click_entropy` — kliklərin entropiyası
- `avg_hesitation` — ortalama duraksama müddəti (ms)
- `mouse_speed_delta` — mouse sürətinin dəyişimi
- `interaction_rhythm` — etkileşim ritmi

Risk Skoru formulu: `Risk Score = 100 × P(fraud | behavior)`

### backend/lstm_model.py
- PyTorch ilə LSTM — davranışı zaman seriyası kimi analiz edir
- Input: son 10 saniyəlik davranış sequence-i
- Output: fraud ehtimalı (0-1)

### backend/train_model.py
- 50.000 sətirlik sintetik dataset yaradır
- İnsan davranışı: təbii mouse variansı, scroll ritmi 0.3-0.8, hesitation 200-1500ms
- Bot davranışı: piksel-mükəmməl kliklər, sıfır hesitation, sabit sürət
- RF + Isolation Forest + LSTM train edir, `model.pkl` saxlayır

### frontend/src/pages/Demo.jsx
- Türkcə ödəmə formu (Kart Numarası, Tutar, Onayla)
- SDK embedded
- Sağ üst küncdə canlı risk skoru badge-i (hər 2 saniyə yenilənir)
- Etiketlər: 0-40 = "Gerçek Kullanıcı ✓", 40-60 = "Şüpheli ⚠", 60-80 = "Yüksek Risk 🔴", 80-100 = "Bot Tespit Edildi 🚫"

### frontend/src/pages/Dashboard.jsx
- Türkcə SOC dashboard — dark theme
- Session cədvəli: Session ID, Risk Skoru, Etiket, Zaman
- Rəngli satırlar: yaşıl/sarı/narıncı/qırmızı
- Seçilmiş session üçün D3.js ilə risk skoru qrafiki
- SHAP top 3 feature horizontal bar chart
- Hər 3 saniyədə auto-refresh

---

## Risk Skoru Kateqoriyaları (Türkcə)

| Skor | Etiket | Rəng | Aksiyon |
|---|---|---|---|
| 0-40 | Gerçek Kullanıcı | Yaşıl | Müdaxilə yoxdur |
| 40-60 | Şüpheli | Sarı | Uyarı göstərilir |
| 60-80 | Yüksek Risk | Narıncı | Əlavə doğrulama tələb olunur |
| 80-100 | Bot Tespit Edildi | Qırmızı | Session bloklanır |

---

## API Response Formatı

```json
{
  "session_id": "uuid",
  "risk_score": 73.4,
  "label": "Yüksek Risk",
  "confidence": 0.91,
  "shap_explanation": [
    {"feature": "click_entropy", "value": 0.12, "impact": 28.3},
    {"feature": "avg_hesitation", "value": 0.0, "impact": 24.1},
    {"feature": "mouse_speed_delta", "value": 0.98, "impact": 19.7}
  ],
  "response_time_ms": 47
}
```

---

## Əsas Qaydalar

1. **Hər UI mətni türkcə olmalıdır** — demo, dashboard, xəta mesajları, etiketlər
2. Response time hər zaman loglanmalıdır — 50ms altında saxla
3. SHAP explanation hər `/api/analyze` cavabında olmalıdır
4. Docker Compose ilə `docker-compose up --build` əmri ilə hər şey işləməlidir
5. `train_model.py` ilk öncə run edilməlidir — `model.pkl` yaranır
6. Frontend `http://localhost:3000`, backend `http://localhost:8000` portunda işləyir

---

## Başlama Sırası

```bash
# 1. Modeli train et
cd backend && python train_model.py

# 2. Hər şeyi qaldır
docker-compose up --build

# 3. Demo səhifəsi
http://localhost:3000/demo

# 4. SOC Dashboard
http://localhost:3000/dashboard
```
