# DeepCheck MVP — CLAUDE.md

## Layihə haqqında

DeepCheck — istifadəçi davranışını real vaxtda analiz edərək bot və insan arasındakı fərqi 0-100 arasında risk skoru ilə müəyyən edən bir süni zəka SDK-sıdır. Layihə **Teknofest Finansal Teknolojiler Yarışması** üçün hazırlanır.

> ÖNƏMLİ: Bu layihə Teknofest münsiflər heyətinə təqdim olunacaq. Buna görə **bütün UI mətnləri, etiketlər, düymələr, dashboard yazıları, xəta mesajları və istifadəçiyə görünən hər şey TÜRKCƏ olmalıdır.** Kod daxilindəki dəyişən adları, funksiya adları və şərhlər ingilis dilində qala bilər.

> DÜRÜSTLÜK QAYDASI: bu layihədə **heç bir real müştəri datası yoxdur**. Hər rəqəm nəyin üzərində ölçüldüyünü söyləməlidir (sintetik, betikli brauzer, yaxud kağız üzərində hesab). Ölçülməyən heç nə iddia edilmir.

---

## Tech Stack

| Hissə | Texnologiya | Qeyd |
|---|---|---|
| Backend | FastAPI (Python 3.11) | Async, yüksək performanslı |
| Database | PostgreSQL 16 | Session, davranış və müştəri profili cədvəlləri |
| ML Model | Random Forest | sklearn. LSTM və Isolation Forest ölçmə nəticəsində skordan çıxarılıb (səbəbi scorer.py-də, təkrarlamaq üçün `backend/model_selection.py`) |
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
├── README.md                  # İşə salma, konfiqurasiya, sintetik demo
├── TECHNICAL_GUIDE.md         # Tam texniki izah (§17 kapasitet, §18-19 profil, §20 doyma)
├── docker-compose.yml         # Bütün servisləri qaldırır
├── .env.example               # Hər dəyişən və səbəbi
├── sdk/
│   └── deepcheck.js           # Brauzer SDK — davranış toplayır (34 KB, asılılıqsız)
├── backend/
│   ├── Dockerfile
│   ├── entrypoint.sh          # Lazım olsa train edir, sonra uvicorn (default 2 worker)
│   ├── requirements.txt
│   ├── main.py                # FastAPI app, 13 endpoint, qərar yolu, retention sweep
│   ├── database.py            # PostgreSQL bağlantısı + additive migration-lar
│   ├── models.py              # DB modelləri (sessions, behavior_data + 4 profil cədvəli)
│   ├── scorer.py              # Feature extraction + risk skoru + smoothing + SHAP
│   ├── lstm_model.py          # FEATURE_NAMES + FEATURE_SCHEMA_VERSION + LSTM tərifi
│   ├── profiles.py            # Müştəri davranış profilinin statistikası (saf modul)
│   ├── profile_lab.py         # Profil qatını sintetik kimliklərdə ölçür → docs/profile-evaluation.md
│   ├── model_selection.py     # Model seçimi araşdırması (RF vs GBM vs LSTM)
│   ├── train_model.py         # Sintetik data + identity latent-ləri + training
│   ├── demo_seed.py           # Münsiflər üçün SENTETİK demo müştəriləri
│   ├── benchmark.py           # Form-fill generatoru, latency və skor benchmark-ları
│   ├── record_session.py      # Etiketlənmiş real sessionu data/real/-a yazır
│   ├── evaluate.py            # O sessionları real scoring yolundan keçirir
│   ├── test_scorer.py         # 54 test — skor, auth, ladder, token, ardıcıl qayda
│   ├── test_profiles.py       # 97 test — profil statistikası, endpointlər, retention
│   └── test_demo.py           # 18 test — sentetik demo müştəriləri və etiketlənməsi
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf             # access_log off
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── demoCustomers.js   # Sentetik demo müştərilərinin siyahısı
│       ├── pages/
│       │   ├── Demo.jsx       # Ödəmə formu demo səhifəsi
│       │   ├── Dashboard.jsx  # SOC dashboard
│       │   └── KvkkNotice.jsx # /kvkk — aydınlatma metni
│       └── components/
│           ├── RiskBadge.jsx
│           ├── SessionTable.jsx
│           ├── RiskChart.jsx      # D3.js qrafik
│           ├── VerificationModal.jsx
│           ├── ProfilePanel.jsx   # SOC "Müşteri Profili" kartı
│           └── SyntheticBadge.jsx # "Sentetik demo verisi" nişanı
├── lab/
│   ├── capture.py             # Real Chromium-u real SDK ilə sürür, telemetri yazır
│   └── bot_lab.py             # Adversarial ssenarilər
├── data/real/                 # Real insan qeydləri — HAZIRDA BOŞDUR
└── docs/
    ├── evaluation.md          # Brauzer laboratoriyası ölçmələri
    ├── profile-evaluation.md  # Profil qatı (profile_lab.py generasiya edir)
    ├── juri-cevaplari.md      # Münsiflər üçün türkcə cavablar
    ├── rapor-duzeltmeleri.md  # Ön-qiymətləndirmə raportundakı iddiaların düzəlişi
    ├── kvkk-aydinlatma.md     # Aydınlatma metni (/kvkk səhifəsi bunu oxuyur)
    ├── dpia.md                # DPIA — pilot üçün ÖN ŞƏRT
    └── index.html             # Landing page
```

> `backend/load_test.py` **yoxdur**. Heç bir yük testi işlədilməyib; kapasitet
> yalnız kağız üzərində hesablanıb (TECHNICAL_GUIDE.md §17).

---

## Modulların Vəzifəsi

### sdk/deepcheck.js
- Brauzerdə işləyir, `<script>` tegi ilə əlavə edilir
- 10 saniyəlik sürüşən pəncərə saxlayır, hər 2 saniyədə göndərir: pointer
  trajectory, click timing, scroll ritmi, hesitation intervalları, keydown
  **vaxtları** (heç vaxt hansı düymə olduğu deyil), fokus itkiləri
- `POST /api/analyze` endpoint-ə JSON göndərir
- `window.DeepCheck.init({ apiUrl })` ilə aktivləşdirilir; session id və token
  serverdən (`POST /api/session` + `POST /api/session/attest`) gəlir,
  brauzerdə yaradılmır
- `DeepCheck.getSessionId()`, `DeepCheck.getToken()`, `DeepCheck.ready()`,
  **`DeepCheck.flush()`**
  - `flush()` uçuşdakı göndərmə bitəndə həll olunan promise qaytarır və
    **heç vaxt reject etmir**. Demo səhifəsi "Onayla"dan əvvəl onu gözləyir ki,
    qərar indicə baş vermiş davranış üzərində verilsin. Göndərmələr üst-üstə
    düşmür
- Hər axışda **`client_sent_at`** (göndərənin öz saatı) gedir: server mütləq
  saat fərqini yox, hadisələrin **yaşını** və sessiya boyu saat fərqinin
  sabitliyini yoxlayır. Saatı 10 dəqiqə səhv olan istifadəçi bloklanmır
- Vaxt damğalı heç bir hadisə yoxdursa axış ümumiyyətlə göndərilmir (əvvəllər
  boş yük qlobal replay hash-ında toqquşub boş dayanan istifadəçiyə 422 verirdi)
- **401** gələndə SDK bir dəfə **yeni sessiya qeydiyyatdan keçirir** və
  göndərməni təkrarlayır
- **429 / 503 üçün geri-çəkilmə (back-off) YOXDUR.** Hazırda 2xx olmayan hər
  cavab `onError`-a düşür. Dizaynı TECHNICAL_GUIDE.md §17.7-dədir, kodda deyil

### backend/main.py
Endpointlər (cəmi 13):
- `POST /api/session` → yeni session id + imzalı proof-of-work challenge
  (**token vermir**)
- `POST /api/session/attest` → həll edilmiş challenge + 2 runtime ölçüsü
  qarşılığında token verir. Bu token brauzer olduğunu **sübut etmir** — sadə
  bir Python skripti 50 dəfədən 50-sində token alıb
- `POST /api/analyze` → davranış datasını alır, risk skoru qaytarır
  (`X-DeepCheck-Token` başlığı məcburidir, yoxsa 401)
- `POST /api/decision` → **tək icra nöqtəsi**: 40/60/80 pilləsini tətbiq edib
  `allow | warn | verify | block` qaytarır. Skor əldə edilə bilmirsə `verify`
  (heç vaxt `allow`).
  Könüllü olaraq `customer_ref` + `risk_context` qəbul edir — bu halda
  `X-Merchant-Id` və `X-Merchant-Key` başlıqları **məcburidir** (yoxdursa 400,
  səhvdirsə 401)
- `POST /api/demo/charge` → demo satıcı arxa ucu: qərarı işlədib ya tahsilat
  edir, ya rədd edir. Səhifədə heç bir şərt yoxdur
- `POST /api/demo/verify` → demo step-up: kodu yoxlayıb serverdə
  `sessions.verified_at` yazır
- `POST /api/profile/consent` → **profil sətrini yaradan yeganə yol**; hüquqi
  əsas qeyd olunur. 409 = müştəri əvvəl etiraz edib
- `POST /api/profile/erase` → `mode: erase | object`. Həmişə 204, profil olsun
  ya olmasın — varlıq orakulu deyil
- `POST /api/outcome` → satıcı sessiyanı `settled` (vektoru referansa yüksəldir)
  və ya `disputed` (vektoru silir) işarələyir
- `GET /api/profile/review/{session_id}` → mübahisəli qərarın insan tərəfindən
  yoxlanması. **Operator başına** ayrı açar (`PROFILE_REVIEW_KEYS`), hər oxuma
  `profile_access_audit`-ə yazılır
- `GET /api/score/{session_id}` → session tarixçəsi (`X-Dashboard-Key`)
- `GET /api/sessions` → bütün sessionlar (dashboard üçün, `X-Dashboard-Key`)
- `GET /api/health` → sistem sağlamlığı

`DEEPCHECK_SECRET` və `DASHBOARD_KEY` `.env`-dən oxunur. `DEBUG=0` olduqda
onlar təyin edilməyibsə proses **başlamır** — susqun default heç vaxt olmamalıdır.
Profil dəyişənlərinin **heç bir rejimdə** (DEBUG=1 daxil) fallback-ı yoxdur:
təyin edilməyibsə qat sadəcə sönülüdür.

### backend/scorer.py
Çıxarılan **12** feature (kanonik ad və sıra `backend/lstm_model.py`-dakı
`FEATURE_NAMES`-dədir — scorer, train_model, profiles və SHAP etiketləri
hamısı oradan oxuyur, buradakı siyahı onun sənədləşdirilməsidir):

Marjinal statistikalar (1-6):
- `scroll_hizi_varyansi` — scroll sürətinin variansı
- `tereddut_skoru` — hərəkətdən əvvəlki ortalama duraksama (ms, log-persentil miqyaslı)
- `etkilesim_entropisi` — hadisə aralıqlarının entropiyası, **kanal başına** ölçülür
- `ivme_degisimi` — mouse **təcilinin** variansı (sürət deltası deyil)
- `tiklama_yogunlugu` — son 5 saniyədəki klik sıxlığı
- `odak_degisimi` — tab/pəncərənin neçə dəfə fokusu itirdiyi

Struktur və kanallararası (7-12) — bunlar müstəqil küyün təqlid edə bilmədiyi
**quruluşu** ölçür, çünki yuxarıdakı altısını saldırgan sadəcə küy əlavə edərək
bərpa edir (ölçülüb: 2 piksel jitter skoru yarıya endirib):
- `hiz_otokorelasyonu` — pointer sürətinin lag-1 avtokorrelyasiyası
- `yon_tutarliligi` — ardıcıl hərəkət vektorları arasındakı orta kosinus
- `zaman_kuantasyonu` — **eyni millisaniyənin** təkrarlanma payı, kanal başına
- `duraklama_dagilimi` — aralıqların variasiya əmsalı (insan fasilələri ağır quyruqludur)
- `tiklama_oncesi_hareket` — öncəsində hərəkət olan kliklərin payı
- `kanal_gecis_gecikmesi` — pointer ↔ klaviatura keçidindəki median gecikmə

Risk Skoru formulu: `Risk Score = 100 × (meşənin fraud sinfi üçün səs payı)`.
Bu **kalibrə edilmiş `P(fraud | behavior)` deyil** — heç nə onu bir baza
nisbətinə qarşı kalibrə etməyib, çünki kalibrə üçün etiketli real trafik yoxdur.

> **Doyma xəbərdarlığı.** 234 laboratoriya sətrində `scroll_hizi_varyansi`
> heç vaxt ölçülməyib (234/234 neytral default), `ivme_degisimi` 90/234-də
> tavanda (1.0), `duraklama_dagilimi` 109/234-də tavanda. Ətraflı və yenidən
> çəkim planı: TECHNICAL_GUIDE.md §20.

### backend/lstm_model.py
- Kanonik `FEATURE_NAMES` siyahısı və `FEATURE_SCHEMA_VERSION` burada yaşayır.
  Versiya **əl ilə** artırılır: ad/sıra dəyişikliyini sha256 pin tutur, amma bir
  feature-in **hesablanma üsulunun** dəyişməsini yalnız nəzərdən keçirmə tutur.
  Saxlanılan müştəri profilləri yalnız eyni versiya daxilində müqayisə olunur
- LSTM tərifi qalıb, amma **skorda iştirak etmir**: yalnız simulyatorla təlim
  edildiyi üçün brauzer trafikində çıxışı "insan"a çökürdü (bot ≥60: RF tək
  0.90, qarışıq 0.79) və devir-təslimi RF-dən 4 axış gec tuturdu. Yenidən
  ölçmək üçün `TRAIN_LSTM=1 python train_model.py`
- Sessiya səviyyəli zaman məntiqi `scorer.smooth_session_score()`-dadır:
  5 axışın medianı + 35 bal sıçrayışda yumşaltma bypass

### backend/profiles.py — müştəri davranış profili (saf statistika)
- **Müqavilə, bir cümlə ilə:** müştərinin öz davranış tarixçəsi yalnız **əlavə
  doğrulama** istəyə bilər. Heç vaxt bloklamır, heç vaxt təsdiqləmir, skoru nə
  aşağı salır, nə qaldırır, və profilə **uyğun gəlmək heç bir güzəşt vermir**
  (qurbanın davranışını təkrar oynadan bot profilə mükəmməl uyğun gəlir)
- Yeganə mümkün nəticə: `allow | warn → verify`
- Bu, DeepCheck-də **hesab ələ keçirməyə** (account takeover) qarşı yeganə
  nəzarətdir. Kart-sınayan botlara qarşı **heç nə vermir** — onlar müştəri
  referansı olmadan gəlir və bu koda heç vaxt çatmır
- DB, FastAPI və model bundle-ı **idxal etmir** — ölçülə və test edilə bilən saf modul
- Statistika: sessiya → 12 rəqəmlik vektor (yalnız **həqiqətən ölçülmüş**
  feature-lər, `measured_mask`) → həmin müştərinin **öz** ≤20 vektoru ilə
  **eyni modallıq** daxilində müqayisə (touch | mouse | keyboard; havuzlanma
  yoxdur) → top-3 z ortalaması → **full conformal** rank
- Yetkinlik = `ceil(1/0.05) − 1` = **19** referans. Bu tənzimlənmiş rəqəm deyil,
  arifmetikadır: n referansla ən kiçik mümkün p-dəyər 1/(n+1)-dir
- Step-up ilə xilas olmuş sessiya `probation=true` ilə **saxlanılır, amma
  referans deyil** — yüksəldilənə qədər (satıcının `settled` bildirişi, yaxud
  ardıcıl 3 keçilmiş çağırış) statistikaya girmir
- Ətraflı: TECHNICAL_GUIDE.md §19

### backend/profile_lab.py
- Profil qatını **sintetik kimliklər** üzərində ölçür və
  `docs/profile-evaluation.md` faylını yaradır
- Xidmətdəki model bundle-ını **oxuyur, heç vaxt yazmır**; xidmətdəki
  verilənlər bazasını ümumiyyətlə açmır (sentetik demo datası ölçməyə girmir)
- `PROFILE_MIN_FEATURE_OBS`, `PROFILE_SCALE_FLOOR`, `PROFILE_TOP_K`
  sabitləri buradan gələn ölçməyə əsaslanır; `test_profiles.py` sabitlərlə
  nəşr olunmuş sənədin uyğunluğunu yoxlayır

### backend/train_model.py
- 25.000 sintetik **session** yaradır; hər biri 10 ardıcıl flush pəncərəsi
  (cəmi 250.000 feature sətri)
- İnsan davranışı: təbii mouse variansı, scroll ritmi 0.3-0.8, hesitation 200-1500ms
- Bot davranışı: piksel-mükəmməl kliklər, sıfır hesitation, sabit sürət
- Sessionların 12%-i orta yerdə **dəyişir** (insan → bot və əksi) — ardıcıl
  model üçün öyrəniləcək yeganə zaman siqnalı budur
- `simulate_identity_sessions()` — profil laboratoriyası üçün **kimlik latenti**.
  Təlim datasını **bit-bit dəyişmir** (T30 bunu yoxlayır)
- RF + Isolation Forest final pəncərə üzərində train olunur (LSTM yalnız
  `TRAIN_LSTM=1` ilə). Isolation Forest hələ təlim edilir və
  bundle-da saxlanılır, lakin **skorda çəkisi 0-dır**: real held-out
  brauzer sətirlərində tək başına ROC-AUC 0.340 verdi — təsadüfdən də pis,
  çünki yalnız insan sətirləri üzrə fit edilir və bu məhsulun hədəf aldığı
  hücum məhz insana bənzəyən hücumdur. Ətraflı ölçmə: TECHNICAL_GUIDE.md §7
- `NEUTRAL_DEFAULTS` burada hesablanır və `model.pkl` içində saxlanılır
  (`scorer.py`-də əl ilə saxlanılmır)

### backend/demo_seed.py — SENTETİK demo müştəriləri
- Komandanın müştəri bazası yoxdur, ona görə münsiflərə göstərilən prototip
  **sintetik müştərilərlə** işləyir: Ayşe, Mehmet, Zeynep — hər biri 20 mouse
  və 20 keyboard sessiyası, ayrılmış `demo` satıcı ad sahəsində
- Yazılan hər şey **görünür şəkildə sentetik işarələnir**: `is_synthetic`
  bayrağı, `sentetik-...` müştəri referansları, Demo səhifəsində "sentetik
  geçmiş", SOC panelində "Sentetik demo verisi" nişanı
- **Heç bir ölçməyə girmir** (`test_demo.py` girərsə pozulur)
- `--status`, `--reset`, `--simulate <ad>`

### backend/record_session.py və backend/evaluate.py
- `record_session.py` — etiketlənmiş **real** sessionu Postgres-dən
  `data/real/{label}/{id}.json` faylına yazır (xam telemetri ilə birlikdə);
  simulyasiya edilmiş sessiyanı qəbul etmir
- `evaluate.py` — həmin sessionları real scoring yolundan keçirib accuracy,
  false-positive nisbəti və ROC-AUC hesablayır → `docs/evaluation.md`
- `data/real/` **hazırda boşdur**. Açıq qalan ən mühüm iş budur

### frontend/src/pages/Demo.jsx
- Türkcə ödəmə formu (Kart Numarası, Tutar, Onayla)
- SDK embedded
- Sağ üst küncdə canlı risk skoru badge-i (hər 2 saniyə yenilənir) — **yalnız
  göstərmək üçündür**, ödənişi bloklayan qərar deyil
- "Onayla" düyməsi əvvəlcə `DeepCheck.flush()`-u gözləyir, sonra
  `POST /api/demo/charge` çağırır və qayıdan `action`-a əməl edir.
  Eşiklər brauzerdə müqayisə edilmir: brauzerdəki hər nəzarət saldırganın
  redaktə edə biləcəyi nəzarətdir
- "Müşteri Referansı (demo)" sahəsi və "Demo Müşterisi" seçicisi — seçicidəki
  hər müştəri açıq şəkildə **sentetik** olaraq yazılıb. Boş buraxılanda profil
  qatı işə düşmür
- Etiketlər: 0-40 = "Gerçek Kullanıcı ✓", 40-60 = "Şüpheli ⚠", 60-80 = "Yüksek Risk 🔴", 80-100 = "Bot Tespit Edildi 🚫"

### frontend/src/pages/Dashboard.jsx
- Türkcə SOC dashboard — dark theme
- Session cədvəli: Session ID, Risk Skoru, Etiket, Zaman
- Rəngli satırlar: yaşıl/sarı/narıncı/qırmızı
- Seçilmiş session üçün D3.js ilə risk skoru qrafiki
- SHAP top 3 feature horizontal bar chart
- **"Müşteri Profili" kartı** (`ProfilePanel.jsx`): vəziyyət, giriş növü,
  referans sayı, sapma və p-dəyər, ən çox sapan feature-lər. Server referans
  vektorlarını heç oxumayıbsa **heç bir say yazılmır** (yəni "0 / 19" heç vaxt
  müştərinin tarixçəsi yoxdur kimi oxunmur)
- Simulyasiya edilmiş sessiyalarda **"Sentetik demo verisi"** nişanı; metrik
  kartları onları saymır və neçəsini kənarda saxladığını yazır
- Hər 3 saniyədə auto-refresh

---

## Risk Skoru Kateqoriyaları (Türkcə)

| Skor | Etiket | Rəng | Aksiyon |
|---|---|---|---|
| 0-40 | Gerçek Kullanıcı | Yaşıl | Müdaxilə yoxdur |
| 40-60 | Şüpheli | Sarı | **Ek doğrulama** — bax aşağıdakı qeyd |
| 60-80 | Yüksek Risk | Narıncı | Əlavə doğrulama tələb olunur |
| 80-100 | Bot Tespit Edildi | Qırmızı | Session bloklanır |

> **40-60 bandı haqqında.** Etiket dəyişmir, amma aksiya dəyişdi. Ardıcıl
> testin (SPRT formasında) nəticəsi qətiləşməyəndə — ki bu, praktikada məhz orta
> bantdır — qərar `warn` deyil, `verify` olur. Səbəbi ölçülüb: `warn` kartı
> çəkir, və əvvəllər bot 10 axış boyu qəsdən qeyri-müəyyən davranaraq
> 20 saniyəyə təsdiq ala bilirdi. Ödəniş qapısında qeyri-müəyyənlik qəbul üçün
> əsas deyil, əlavə sübut istəmək üçün əsasdır.
>
> Sərhədlər sintetik data üzərində seçilmiş bir **iş nöqtəsidir**, Wald-ın xəta
> zəmanətləri deyil: axış skorları üst-üstə düşən pəncərələrdən gələn kalibrə
> edilməmiş səs paylarıdır.

> **Skorlanan tərəfə deyilən səbəb.** `cluster`, `sequential`, `conformal`,
> `profile_deviation` və `profile_rate_limited` daxili səbəbləri müştəriyə
> **tək bir `step_up`** olaraq gedir. Hansı yoxlamanın işlədiyini demək
> saldırgana köklənmə siqnalıdır: göndər, səbəbi oxu, dəyiş, təkrarla. Əsl
> səbəb `decision_audit` cədvəlində və SOC panelində qalır.
> Telemetriyanın **vəziyyətini** təsvir edən səbəblər (`unknown_session`,
> `insufficient_evidence`, `stale`, `verified`, `score`) olduğu kimi qaytarılır
> — sayt onlarla "bir neçə saniyə daha" deyə bilməlidir.

---

## API Response Formatı

```json
{
  "session_id": "uuid",
  "risk_score": 73.4,
  "label": "Yüksek Risk",
  "confidence": 0.91,
  "shap_explanation": [],
  "response_time_ms": 47
}
```

`shap_explanation` **boş gəlir** — bax Əsas Qaydalar 3. `SHAP_IN_ANALYZE=1`
yalnız canlı nümayiş üçündür.

---

## Əsas Qaydalar

1. **Hər UI mətni türkcə olmalıdır** — demo, dashboard, xəta mesajları, etiketlər
2. Response time hər zaman loglanmalıdır — 50ms altında saxla. `compute_risk`
   17.7 ms ölçülüb; `/api/decision` konteyner topologiyasında p95 7.4 ms
   (profil qatı sönülü) və 34.8 ms (yanılı) — `docs/profile-evaluation.md` §11
3. SHAP explanation hər `/api/analyze` cavabında **artıq qaytarılmır**. `/api/analyze` skorlanan tərəfə cavab verir və onu məhkum edən üç xüsusiyyəti adlandırmaq hücumçuya köklənmə siqnalı verir: göndər, səbəbi oxu, dəyiş, təkrarla. Bu, canlı detektora qarşı nəzarətli optimallaşdırma döngüsüdür və adversarial sınaqda məhz bundan istifadə edilib. İzah hər sətirdə saxlanılır və SOC panosu onu `GET /api/score/{id}`-dən (`X-Dashboard-Key` arxasında) oxuyur. Yalnız canlı nümayiş üçün `SHAP_IN_ANALYZE=1`
4. Docker Compose ilə `docker-compose up --build` əmri ilə hər şey işləməlidir
5. `train_model.py` ilk öncə run edilməlidir — `model.pkl` yaranır
6. Frontend `http://localhost:3000`, backend `http://localhost:8000` portunda işləyir
7. **Ölçülməyən heç bir rəqəm yazılmır.** Hər rəqəmin yanında nəyin üzərində
   ölçüldüyü durmalıdır. Sentetik data ilə ölçülmüş hər "yanlış çağırış"
   nisbəti **aşağı hədd**dir: sintetik insan real insandan daha öz-özünə
   uyğundur
8. **Profil qatı sönülü buraxılır.** `PROFILE_LAYER=1` (kölgə rejimi) yalnız
   ölçmə üçün; `PROFILE_ESCALATION=1` yalnız demo üçün

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

### Testlər

```bash
cd backend && DEEPCHECK_SECRET=... DASHBOARD_KEY=... DEBUG=0 python -m pytest -q
cd frontend && npm test && npm run build
```

169 backend testi, 59 frontend testi (2026-09-20).

### Münsiflər üçün sentetik demo

```bash
# .env: DEEPCHECK_PROFILE_KEY, DEEPCHECK_MERCHANT_KEYS, PROFILE_LAYER=1,
#       PROFILE_ESCALATION=1, DEMO_ENDPOINTS=1  (.env.example "Jury prototype")
docker compose up -d --build
docker compose exec backend python demo_seed.py            # seed (idempotent, ~20 s)
docker compose exec backend python demo_seed.py --status   # nə saxlanılıb
docker compose exec backend python demo_seed.py --reset    # sıfırla
docker compose exec backend python demo_seed.py --simulate ayse
```

Demo səhifəsində **Demo Müşterisi** seçilir, kart mouse ilə doldurulur və
"Onayla" basılır. Prosedurun addım-addım türkcəsi:
`docs/juri-cevaplari.md`. Bu, **mexanizmi** göstərir, real insanlar üzərindəki
dəqiqliyi yox.
