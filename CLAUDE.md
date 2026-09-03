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
- `init()` çağırılanda əvvəlcə `POST /api/session` ilə handshake edir və token alır
- `POST /api/analyze` endpoint-ə `Authorization: Bearer <token>` ilə JSON göndərir
- `window.DeepCheck.init({ apiUrl, intervalMs, onUpdate })` ilə aktivləşdirilir
- `session_id` artıq clientdən göndərilmir — serverdən gəlir
- `authorizedFetch(path, body)` — demo ödəmə axını bunun üzərindən gedir
- Yalnız keydown **vaxtı** yazılır, heç vaxt `e.key` və ya sahə dəyəri yazılmır

### backend/main.py
Endpointlər:
- `POST /api/session` → server session_id yaradır və HMAC imzalı token qaytarır (auth yoxdur)
- `POST /api/analyze` → davranış datasını alır, risk skoru qaytarır (**Bearer token tələb olunur**)
- `POST /api/transaction` → **server tərəfli** qərar: onaylandi / dogrulama_gerekli / reddedildi (Bearer token)
- `POST /api/transaction/verify` → step-up kodunu yoxlayır, tək istifadəlik, 5 cəhd limiti (Bearer token)
- `GET /api/score/{session_id}` → session tarixçəsi (**operator açarı tələb olunur**)
- `GET /api/sessions` → bütün sessionlar, dashboard üçün (**operator açarı tələb olunur**)
- `GET /api/health` → sistem sağlamlığı

ÖNƏMLİ: `session_id` heç vaxt clientdən qəbul edilmir — yalnız serverin imzaladığı tokendən çıxarılır.
Bloklama qərarı brauzerdə deyil, `/api/transaction`-da verilir; frontenddəki `riskScore >= 80`
yalnız görüntü üçündür.

### backend/security.py
- HMAC imzalı session token: `issue_session()` / `verify_session_token()`
- Operator açarı yoxlaması (SOC dashboard endpointləri üçün)
- Token-bucket rate limiter (per-IP), yaddaş sızmasına qarşı məhdudlaşdırılmış

### backend/scorer.py
Çıxarılan 10 feature (hamısı ~0-1 aralığında normalize olunur):

Paylanma formu featureləri (attacker üçün ucuz təqlid edilir):
- `scroll_hizi_varyansi` — scroll sürətinin dəyişkənliyi
- `tereddut_skoru` — ortalama duraksama müddəti (ms)
- `etkilesim_entropisi` — kanal başına event aralıqlarının entropiyası
- `ivme_degisimi` — mouse ivməsinin varyansı
- `tiklama_yogunlugu` — son 5 saniyədəki klik sıxlığı
- `odak_degisimi` — tabın fokusu neçə dəfə itirdiyi

Kinematik / zaman strukturu featureləri (təqlidi baha başa gəlir):
- `hiz_otokorelasyonu` — sürətin öz keçmiş dəyəri ilə korrelyasiyası (real hərəkətdə ətalət var)
- `yon_tutarliligi` — ardıcıl hərəkət vektorlarının istiqamət davamlılığı
- `zaman_kuantasyonu` — eyni millisaniyə aralığının təkrarlanma nisbəti (skript taymerləri təkrarlayır)
- `duraklama_dagilimi` — event aralıqlarının yayılması (insan pauzaları ağır quyruqludur)

ÖNƏMLİ: hər feature-in etibarlı olması üçün minimum sample sayı tələb olunur
(`MIN_AUTOCORRELATION_SAMPLES`, `MIN_ENTROPY_GAPS` və s.). Az sample-dan hesablanan
statistika ölçmə deyil, küydür — onu dəlil kimi saymaq real istifadəçini bloklayır.
Hədd altında feature `NEUTRAL_DEFAULTS`-a düşür.

Risk Skoru formulu: `Risk Score = 100 × P(fraud | behavior)`

Əlavə olaraq iki etibarlılıq ölçüsü qaytarılır:
- `signal_sufficiency` (0-1) — flush-un nə qədər real dəlil daşıdığı. Aşağı olduqda
  `/api/transaction` avtomatik təsdiq etmir, step-up tələb edir.
- `temporal_support` (0-1) — LSTM-in girişində nə qədər real zaman strukturu var.
  Flush pəncərələrə bölünə bilməyəndə `build_sequence()` sabit vektor verir; orada
  LSTM-in çıxışı mənalı deyil. Ona görə LSTM-in ansambl çəkisi bu dəyərə vurulur,
  qazanmadığı pay isə Random Forest-ə keçir (üç çəkinin cəmi həmişə 1.0).
  Səbəb ölçülüb: seed edilməmiş LSTM-in iki fərqli run-u eyni payload-da 0.98 və 0.43
  vermişdi — sabit 0.3 çəki ilə bu, 16 xal fərq və verdikt dəyişikliyi demək idi.

### backend/lstm_model.py
- PyTorch ilə LSTM — davranışı zaman seriyası kimi analiz edir
- Input: son 10 saniyəlik davranış sequence-i
- Output: fraud ehtimalı (0-1)

### backend/train_model.py
- 50.000 sətirlik sintetik dataset yaradır
- İnsan davranışı **ballistik hərəkət modeli** ilə simulyasiya olunur (minimum-jerk profili,
  hədəfə yönəlmiş sub-movement-lər + titrəmə) — əvvəlki IID təsadüfi gəzişmə əvəzinə.
  Bu vacibdir: IID gəzişmə məhz attacker skriptinin ürətdiyi şeydir.
- Personalar: `human`, `human_rushed`, `human_sparse` (az siqnallı real flush),
  `bot`, `bot_sophisticated`, `bot_evasive` (insanı təqlid edən adversarial bot)
- RF + Isolation Forest + LSTM train edir, `model.pkl` saxlayır
- `--seed N` ilə fərqli draw-larda train etmək olar (LSTM-in seed-lər arası
  sabitliyini ölçmək üçün; ümumi dəqiqlik bunu tamamilə gizlədir)
- Sınanıb rədd edilib: LSTM-i seyrək ardıcıllıqlarla train etmək onu seyrək
  girişlərdə **daha pis** edir (tiled dəqiqlik 0.8603 → 0.7539, RF 0.9838 → 0.9524).
  Səbəb strukturaldır — tiled ardıcıllıq sabit seriyadır, aqreqat feature
  vektorundan artıq heç nə daşımır, ona görə LSTM orada RF-in zəif dublikatıdır.
- `SEED = 42` həm NumPy, həm PyTorch üçün tətbiq olunur — əks halda LSTM hər run-da
  fərqli çıxır və nəticələr təkrarlanmır (bu, real bir bug idi)
- `python train_model.py --print-neutral-defaults` → `NEUTRAL_DEFAULTS` dəyərlərini
  yenidən hesablayır (persona dəyişəndə mütləq yenilənməlidir)

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
1a. **Bloklama qərarı həmişə serverdə verilir.** Frontend yalnız göstərir, qərar vermir.
1b. **`session_id` heç vaxt clientdən qəbul edilmir** — yalnız imzalı tokendən.
2. Response time hər zaman loglanmalıdır — 50ms altında saxla
3. SHAP explanation hər `/api/analyze` cavabında olmalıdır
4. Docker Compose ilə `docker-compose up --build` əmri ilə hər şey işləməlidir
5. `train_model.py` ilk öncə run edilməlidir — `model.pkl` yaranır
6. Frontend `http://localhost:3000`, backend `http://localhost:8000` portunda işləyir

---

## Başlama Sırası

```bash
# 0. (İstəyə bağlı) Secret-ləri təyin et — təyin edilməsə hər proses üçün
#    təsadüfi yaradılır və xəbərdarlıq verilir (demo üçün işləyir, produksiya üçün yox)
export DEEPCHECK_SECRET="..." DEEPCHECK_OPERATOR_KEY="..."

# 1. Modeli train et
cd backend && python train_model.py

# 2. Hər şeyi qaldır
docker-compose up --build

# 3. Demo səhifəsi
http://localhost:3000/demo

# 4. SOC Dashboard
http://localhost:3000/dashboard
```
