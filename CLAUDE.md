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
| ML Model | Random Forest | sklearn. LSTM və Isolation Forest ölçmə nəticəsində skordan çıxarılıb, sonra düzəldilmiş data üzərində **yenidən mühakimə edilib** və yenə kənarda qalıb (səbəbi scorer.py-də, təkrarlamaq üçün `backend/model_selection.py`) |
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
│   ├── model_selection.py     # Model seçimi araşdırması (8 ailə, 6 protokol)
│   ├── train_model.py         # Sintetik data + identity latent-ləri + training
│   ├── demo_seed.py           # Münsiflər üçün SENTETİK demo müştəriləri
│   ├── benchmark.py           # Form-fill generatoru, latency və skor benchmark-ları
│   ├── record_session.py      # Etiketlənmiş real sessionu data/real/-a yazır
│   ├── evaluate.py            # O sessionları real scoring yolundan keçirir
│   ├── test_scorer.py         # 71 test — skor, auth, ladder, token, ardıcıl qayda
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
├── data/real/                 # Real insan qeydləri — 1 şəxs, 1 sessiya (p01, 2026-09-25)
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
  `sessions.verified_at` yazır. **Bir step-up = bir təsdiq:** doğrulama onun
  verdiyi təsdiqlə xərclənir (`main._consume_step_up`, compare-and-set,
  `last_seen_at`-a toxunmadan). Əvvəllər bir doğrulama sessiyadakı hər
  `verify`-ı 5 dəqiqə ərzində `allow` edirdi
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

> **Doyma xəbərdarlığı (2026-09-25-də yenidən ölçüldü).** Əvvəlki rəqəmlər
> 234 laboratoriya sətrindən sayılmışdı; həmin sətirlər **artıq etibarlı
> deyil** — fayl normallaşdırılmış vektorları saxlayır, xam hadisələri yox
> (234-dən 0-ı), ona görə yenidən təlimdən sonra köhnə koordinat sistemində
> donub qalırlar. Aşağıdakılar **bir real yazının** 52 axışında, xidmətdəki
> bundle ilə yenidən çıxarılıb (arxiv xam hadisələri saxlayır):
>
> | feature | köhnə bundle | xidmətdəki bundle |
> |---|---|---|
> | `ivme_degisimi` tavanda | 48/52 | **3/52** |
> | `scroll_hizi_varyansi` tavanda | 27/52 | **0/52** |
> | `tereddut_skoru` tavanda | 9/52 | **4/52** |
> | `duraklama_dagilimi` tavanda | 44/52 | **44/52 — dəyişməyib** |
>
> Endpoint-ləri **simulyator** tərpətdi, real data yox: log-persentil
> uçları təlim paylanmasına fit edilir, kadr saatı isə həmin paylanmanı
> dəyişdi. Real xam dəyərlərin havuza qarışdırılması **işə düşmədi** —
> 49 real axış log-miqyaslı feature başına 46/47/48 dəyər verir,
> `MIN_SCALING_VALUES = 50` həddinin altında. Hədd **endirilmədi**; ikinci
> bir yazı onu keçir. `duraklama_dagilimi` ümumiyyətlə log-persentil
> miqyaslı deyil (CV / `DISPERSION_DIVISOR` = 1.5), bu mexanizmin əli ona
> çatmır və indi əsas qalıq tavandır.
>
> Real trafikdə hələ də ölçülə bilməyənlər: `kanal_gecis_gecikmesi`
> 52 axışın yalnız **2**-də, `tiklama_oncesi_hareket` 19-da ölçülüb.
> Ətraflı: `docs/evaluation.md`.

### backend/lstm_model.py
- Kanonik `FEATURE_NAMES` siyahısı və `FEATURE_SCHEMA_VERSION` (hazırda **3**)
  burada yaşayır. Versiya **əl ilə** artırılır: ad/sıra dəyişikliyini sha256 pin tutur, amma bir
  feature-in **hesablanma üsulunun və ya miqyasının** dəyişməsini yalnız
  nəzərdən keçirmə tutur. Saxlanılan müştəri profilləri yalnız eyni versiya
  daxilində müqayisə olunur
- **Versiya indi 2-dir.** Kadr saatı ilə yenidən təlim
  `scroll_hizi_varyansi`, `tereddut_skoru` və `ivme_degisimi` üçün
  log-persentil uclarını tərpətdi, yəni **eyni xam telemetri artıq başqa
  yerə düşür**. Real yazının 52 axışında ölçülüb (arxiv həm xam hadisələri,
  həm də köhnə bundle-ın hesabladığı feature-ləri saxlayır): orta fərq
  -0.197 / -0.177 / -0.185, maksimum |fərq| 0.263 / 0.340 / 0.401 — qalan
  doqquzu bit-bit eyni. Heç bir ad dəyişmədiyi üçün sha256 pin bunu görə
  bilməzdi. Bump saxlanılmış vektorları **təqaüdə göndərir**; demo
  müştəriləri `demo_seed.py --reset` ilə yenidən toxumlanıb
- LSTM tərifi qalıb, amma **skorda iştirak etmir**. 2026-09-25-də düzəldilmiş
  data üzərində yenidən ölçüldü və yenə kənarda qaldı: köhnə formada ROC-AUC
  0.848, sunum yolunun həqiqətən ürətdiyi formada (padded prefix) 0.871 —
  meşənin 0.996-sının altında. Var olma səbəbi olan devir-təslimdə hələ də
  bir axış (köhnə formada dörd axış) gec tutur. Yenidən ölçmək üçün
  `TRAIN_LSTM=1 python train_model.py`; tam cədvəl `scorer.py`-dədir
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
- **Qeyd (2026-09-25):** kadr saatı ilə yenidən təlimdən sonra bu qat öz işində
  **zəiflədi**. Eyni sabitlərlə `(8, 0.02, 3)` müqayisə: mouse-da **fərqli
  adamın** eskalasiyası 47.9% → **18.4%** (yəni hesab ələ keçirmənin yarıdan
  çoxu itdi), eyni adamın yanlış çağırılması 5.0% → 3.8%. Klaviaturada fərqli
  adam 3.2% → 11.6%, imtina 88.1% → 23.9%. Laboratoriya indi başqa sabitlər
  çıxarır (`PROFILE_SCALE_FLOOR` 0.0062 → 0.02, `PROFILE_TOP_K` 3 → 12), amma
  **heç nə dəyişdirilmədi**: sabiti yenidən çıxarmaq ölç → qoy → yenidən ölç
  dövrüdür. Ətraflı və səbəb fərziyyəsi: `docs/profile-evaluation.md`
  başlığındakı xəbərdarlıq. Qat susqun halda **sönülüdür** və skor ondan asılı
  deyil — buna görə bu bloklayıcı deyil, ardıcıl işdir
- Ətraflı: TECHNICAL_GUIDE.md §19. Fayl `2140efa` commit-i ilə **səhvən
  silinmişdi** və 2026-09-25-də bərpa edilib (git-dəki 953 sətirlik versiya
  köhnə idi; 2179 sətirlik yenidən yazılmış versiya Claude Code-un fayl
  tarixçəsindən qaytarılıb). Hələ commit edilməyib

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
- **Kadr saatı (2026-09-25).** Pointer və scroll vaxt damğaları indi növbəti
  render kadrının sərhədində vurulur, niyyət anında yox. Sessiya başına
  yeniləmə tezliyi 60/120/144 Hz, çəkilər 0.70/0.20/0.10 — yeganə ölçülmüş
  maşın 60 Hz-dir, qarışıq isə **fərziyyədir**. Düşən kadr 3% (ölçülüb:
  973 daxili aralıqdan 32-si bir kadrdan uzundur), birləşdirilmiş
  (coalesced) kimi modelləşdirilir. Klik və keydown **kadra bağlanmır**:
  yazıda onların aralıqları 16.67 ms-in misilinə yalnız 7.1% və 10.2%
  hallarda düşür, yəni təsadüf səviyyəsində. Səbəbi və ölçməsi:
  `docs/evaluation.md`
- Pointer hərəkəti **minimum-jerk yolu üzərində partlayışdır** (əl sürətlənir,
  zirvəyə çatır, yavaşıyır), hər nümunə üçün müstəqil təsadüfi gəzinti deyil.
  Partlayış uzunluğu və istirahət yazıya fit edilib (median 10.5 nümunə,
  417 ms istirahət). Skriptlər sabit sürətdə qalır (`minimum_jerk=False`)
- `human_autofill` personası (insan sinfinin 17.6%-i): brauzerin yadda
  saxladığı kartı seçir, çox vaxt yalnız 3 CVV rəqəmi yazır. Bundan əvvəl
  təlimdəki hər insan pəncərəsi ≥15 klaviatura hadisəsi daşıyırdı, yəni
  "heç yazmamaq" **yalnız botdan gələ bilərdi**
- `BOT_REAL_CLOCK_RATE = 0.50` — `bot_sophisticated`-in yarısı real brauzeri
  real input ilə sürür və eyni kadr saatını alır. Düzəlişin yeni bir
  "bir-bitlik keçid"ə çevrilməsinə qəsdən imkan verilmir
- Sessionların 12%-i orta yerdə **dəyişir** (insan → bot və əksi) — ardıcıl
  model üçün öyrəniləcək yeganə zaman siqnalı budur
- `load_recorded_samples()` `data/real/{human,bot}/*.json`-u **yerində** oxuyur
  (git-də izlənən `lab/real_telemetry.json`-a köçürmür: `.gitignore` real
  adamların xam vaxtlarını tarixçəyə buraxmır). Bu sətirlər xam daşıdığı üçün
  hər təlimdə cari miqyasla yenidən çıxarılır və köhnə koordinat sistemində
  dona bilmir — 234 laboratoriya sətrindən fərqli olaraq
- `simulate_identity_sessions()` — profil laboratoriyası üçün **kimlik latenti**.
  Təlim datasını **bit-bit dəyişmir** (T30 bunu yoxlayır)
- RF + Isolation Forest final pəncərə üzərində train olunur (LSTM yalnız
  `TRAIN_LSTM=1` ilə). Isolation Forest hələ təlim edilir və
  bundle-da saxlanılır, lakin **skorda çəkisi 0-dır**. Əvvəlki səbəb
  («ROC-AUC 0.340, tərsdir») **geri çəkilib**: o rəqəm donmuş laboratoriya
  sətirlərində ölçülmüşdü; yenidən çəkilmiş laboratoriya + real şəxs
  (301 sətir) üzrə eyni model **0.657** verir — tərs deyil. Yenə də kənarda,
  çünki işə yaramır: məşru axışların **38%**-ini və real şəxsin axışlarının
  **76%**-ini addım-yuxarı xəttinin üstünə qaldırır, ona çəki verən hər
  qarışıq uduzur. Səbəb struktur: yalnız insan sətirləri üzrə fit edilir,
  bu məhsulun hədəf aldığı hücum isə məhz insana bənzəyən hücumdur.
  Ətraflı ölçmə: TECHNICAL_GUIDE.md §7, `docs/evaluation.md`
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
- `data/real/`-da **cəmi bir** real yazı var (p01, 52 axış, 2026-09-25) — bir
  şəxs yanlış-müsbət nisbəti deyil. Daha çox adam (20-30 nəfər, hər biri bir
  neçə sessiya, fərqli cihazlar) yazmaq açıq qalan ən mühüm işdir

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

> **Yalnız müşahidə edilmiş axışlar sübutdur (2026-09-26).** Altı struktur
> feature-dan heç birini ölçməyən axış heç bir istiqamətdə sübut deyil: boş
> pəncərə neytral fallback-larla **99.1** alır (tək bir axış müştərini addım-
> yuxarıya məcbur etməyə kifayət edirdi), nazik pəncərə isə marjinal feature-lər
> və müştərinin özü göndərdiyi `hesitation_intervals` vasitəsilə **aşağı
> yönləndirilə** bilir (3000 hazırlanmış payload: median 29.7). Ona görə
> ardıcıl statistika yalnız müşahidə edilmiş axışları oxuyur, və 10 ən yeni
> axışdan 3-dən azı müşahidə edilibsə qərar `verify("insufficient_evidence")`
> olur — nə təsdiq, nə geri dönməz blok. Qaydanın **ilk variantı** testi
> sadəcə keçirib qərarı ladder-ə verirdi; adversarial yoxlama sadə, SDK-ya
> sadiq bir skriptin 300-dən 88-də tahsil edildiyini ölçdü və o variant
> dəyişdirildi. Bağlamadığı hal: avtomatlaşdırma **3+ insana bənzəyən
> müşahidə edilmiş axış** arasında gizlədilərsə — yeni bacarıq deyil, çünki
> eyni saldırgan avtomatlaşdırma zamanı səssiz qalaraq köhnə qaydadan da
> keçir (40/40). Rəqəmlər: `main._structural_bits` üstündəki şərh,
> `docs/evaluation.md`
>
> İkinci yoxlama raundu daha üç qüsur tapdı və hamısı düzəldilib: (1) ladder
> blok qərarını hələ də müşahidə edilməmiş axışların medianına söykəyə
> bilirdi — indi yeni 5 axışdan 3-dən azı müşahidə edilibsə blok `verify`
> olur (sintetik nazik quyruqlarda blok 7/9 → 0); (2) bir step-up 5 dəqiqə
> ərzində sonsuz təsdiq verirdi — indi xərclənir; (3) testlər "müşahidə
> edilmiş"in tərifini sabitləmirdi — real `compute_risk` maskaları ilə test
> əlavə edildi, mutasiya ilə yoxlanıb. **Qapının həll etmədiyi:** kartı
> **təsadüfi aralıqlı** keydown-larla yazan, pointer-siz skript 120-dən
> 119-da tahsil edilir — köhnə qaydada da eyni. Bu modelin boşluğudur, bu
> layihə onu bağladığını iddia etmir

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
   17.7 ms ölçülmüşdü; 2026-09-25 yenidən təlimdən sonra host-da n=400 ilə
   median **18.3 ms**, p95 22.1 ms — yəni dəyişməyib. `benchmark.py`-nin öz
   axış gecikməsi (n=1200) p95 67.6 ms-dən 19.2 ms-ə düşüb; bu ayrı bir
   ölçüdür və səbəbi araşdırılmayıb. `/api/decision` konteyner
   topologiyasında p95 7.4 ms (profil qatı sönülü) və 34.8 ms (yanılı) —
   `docs/profile-evaluation.md` §11; həmin rəqəmlər yenidən təlimdən **əvvəl**
   ölçülüb, latency bölməsi bu qaçışda yenidən işlədilməyib
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

186 backend testi, 60 frontend testi (2026-09-26).

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
