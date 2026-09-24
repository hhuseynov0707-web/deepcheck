# Entegrasyon Kılavuzu

> **Bu belgenin kapsamı.** DeepCheck'i bir satıcının (banka, e-ticaret sitesi,
> e-cüzdan) kendi ödeme akışına nasıl bağlayacağı. Web ödeme sayfası bugün
> çalışıyor ve bu depodaki demo tam olarak bu deseni kullanıyor. WebView
> tabanlı cüzdanlar aynı SDK ile çalışır; yerel (native) mobil uygulamalar için
> **SDK yoktur** ve o bölüm bilinçli olarak **yol haritası** etiketlidir.
>
> **Sayılar hakkında.** Buradaki her sayı ya depodaki bir ölçüm dosyasından
> gelir ya da o ölçümler üzerinde bu sayfada açıkça gösterilen bir aritmetiktir.
> **Yük testi yapılmadı.** Kapasite bölümündeki her değer
> "ölçümden türetilmiş hesap" etiketi taşır ve kaynağı yanında yazar.
>
> **Gerçek müşteri verisi yoktur.** Müşteri profili katmanının bütün oranları
> **sentetik kimliklerle**, tarayıcı ölçümleri ise **betikle yürütülen
> Playwright koşularıyla** ölçüldü. Jüriye gösterilen prototip de **sentetik
> demo müşterileriyle** çalışır ve mekanizmayı gösterir, gerçek insanlardaki
> doğruluğu değil.

İlgili belgeler: mimari ve ölçümler için [`../TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md)
(§17 kapasite, §18 veri toplama, §19 müşteri profili), jüri cevapları için
[`juri-cevaplari.md`](juri-cevaplari.md), profil katmanının ölçümleri için
[`profile-evaluation.md`](profile-evaluation.md), model doğruluğu için
[`evaluation.md`](evaluation.md), hukuki metinler için
[`kvkk-aydinlatma.md`](kvkk-aydinlatma.md) ve [`dpia.md`](dpia.md).

---

## 1. Entegrasyonun şekli: tek cümle

Tarayıcı davranışı toplar, **karar satıcının sunucusunda istenir ve orada
uygulanır**. Tarayıcıda hiçbir eşik karşılaştırılmaz; tarayıcıdaki her kontrol
saldırganın düzenleyebileceği bir kontroldür.

```
  Müşterinin tarayıcısı                Satıcının sunucusu            DeepCheck
  ──────────────────────               ──────────────────            ─────────
  <script src=deepcheck.js>
  DeepCheck.init({apiUrl})  ──────────────────────────────────────►  POST /api/session
                            ◄──────────────────────────────────────  challenge
                            ──────────────────────────────────────►  POST /api/session/attest
                            ◄──────────────────────────────────────  session_id + token
  (her 2 sn, sayfa açıkken) ──────────────────────────────────────►  POST /api/analyze
  "Öde" düğmesi
  await DeepCheck.flush()
  session_id + token  ────►  /checkout
                             POST /api/decision  ──────────────────►  action
                             action allow|warn ise tahsilat
                       ◄───  sonuç
```

DeepCheck arka ucu **satıcının kendi ağında** çalışan bir konteynerdir
(`docker-compose.yml`): davranış verisi kurumun altyapısından çıkmaz. Ödeme
sağlayıcısına, kart şemasına ya da cüzdanın muhasebesine hiç dokunulmaz.
DeepCheck yalnızca "bu ödemeyi onaylamadan önce ek doğrulama iste" der.

---

## 2. Web ödeme sayfası — bugün çalışan yol

### 2.1 SDK'yı sayfaya ekleyin

```html
<script src="https://<host>/deepcheck.js"></script>
<script>
  DeepCheck.init({ apiUrl: "https://<host>" });
</script>
```

`sdk/deepcheck.js` bağımlılıksızdır; küçültülmemiş hâliyle 33.914 bayt, gzip ile
12.208 bayttır (2026-09-19 dosya boyutu ölçümü). Çerez, `localStorage` ve
tarayıcı parmak izi kullanmaz.

Oturum kimliği ve imzalı jeton **sunucudan** gelir; SDK bunları kendisi ister
(`POST /api/session` → iş ispatı → `POST /api/session/attest`) ve her akışta
`X-DeepCheck-Token` başlığıyla gönderir. Tarayıcıda oturum kimliği üretilmez.

Bilmeniz gereken üç davranış:

- `DeepCheck.ready()` sunucu oturumu verdiğinde çözülür.
  `DeepCheck.getSessionId()` ve `DeepCheck.getToken()` ödeme çağrısının
  ihtiyaç duyduğu iki değeri verir. **Sayfa yüklenirken önbelleğe almayın:**
  401 alındığında SDK bir kez yeni oturum açar ve bu iki değer değişir.
- `DeepCheck.flush()` o anki pencereyi hemen gönderir ve gönderim bitince
  çözülür; **hiçbir zaman reddetmez**, bu yüzden ödeme yolunda `await`
  edilebilir. Kararı istemeden önce çağırın ki karar, iki saniyeye kadar eski
  bir pencereye değil, az önce olan davranışa baksın.
- **429 ve 503 için geri çekilme (back-off) yoktur.** 2xx olmayan her yanıt
  `onError` geri çağrısına ve `deepcheck:error` DOM olayına düşer; başarısız
  istek asla `onUpdate`'e ulaşmaz, yani ölü bir arka uç temiz skor gibi
  görünemez. Geri çekilme tasarımı `TECHNICAL_GUIDE.md` §17.7'de yazılı ve
  **uygulanmadı** (bkz. §8.3).

Sayfada canlı skor göstermek isterseniz `onUpdate` geri çağrısını kullanın; bu
**yalnızca gösterim** içindir, ödemeyi hiçbir zaman buna göre kesmeyin.
Akış yanıtında `provisional` alanı doğruysa risk rengi yerine "ölçülüyor"
gösterin: bir oturumun ilk saniyeleri karar verilebilecek kadar sinyal
taşımaz (`docs/evaluation.md`, seyrek ilk akış ölçümü).

### 2.2 Ödeme anında kararı sunucudan isteyin

```js
// Satıcının arka ucu — tarayıcıdan gelen session_id ve token ile
const res = await fetch("https://<host>/api/decision", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-DeepCheck-Token": token,
    // Müşteri profili katmanı kullanılacaksa (bölüm 5):
    "X-Merchant-Id": "acme",
    "X-Merchant-Key": process.env.DEEPCHECK_MERCHANT_KEY,
  },
  body: JSON.stringify({
    session_id,
    customer_ref: "musteri-4711",             // isteğe bağlı, bölüm 5
    risk_context: { amount_band: "high", new_beneficiary: true }, // kaydedilir, karara girmez
  }),
});
const decision = await res.json();
if (decision.action === "allow" || decision.action === "warn") {
  await paymentProvider.charge(order);
}
```

Yanıt:

```json
{
  "action": "verify",
  "risk_score": 73.4,
  "label": "Yüksek Risk",
  "message": "Ek dogrulama gerekli",
  "reason": "step_up"
}
```

| `action` | Skor bandı | Etiket | Satıcının yapması gereken |
|---|---|---|---|
| `allow` | 0–40 | Gerçek Kullanıcı | Ödemeyi işleyin, kullanıcı hiçbir şey görmez |
| `warn` | 40–60 | Şüpheli | Ödemeyi işleyin, isterseniz uyarı gösterin |
| `verify` | 60–80 | Yüksek Risk | Ek doğrulama isteyin (SMS, 3-D Secure) |
| `block` | 80–100 | Bot Tespit Edildi | Ödemeyi reddedin |

Bandlar **sunucuda** uygulanır (`backend/main.py`, `ACTION_LADDER`); yukarıdaki
tablo yalnızca hangi aksiyonun ne demek olduğunu anlatır. `action` dışındaki
hiçbir alan karar için kullanılmamalıdır.

### 2.3 `reason` alanı ve ek doğrulama

`reason`, kullanıcıya ne göstereceğinizi belirler:

| `reason` | Anlamı | Arayüzde |
|---|---|---|
| `score` | Skorun kendisi | Aksiyon mesajı yeterli |
| `insufficient_evidence` | Henüz 3 akıştan az davranış var | "Birkaç saniye sonra tekrar deneyin" — **OTP istemeyin** |
| `stale` | Son akış 30 saniyeden eski | Sayfayı canlandırın ya da ek doğrulama |
| `unknown_session` | Oturum kaydı yok | Sayfayı yenileyin |
| `verified` | Ek doğrulama sunucuda kayıtlı, `verify` → `allow` yükseltildi | Ödeme geçer |
| `step_up` | Ek doğrulama gerekli | OTP / 3-D Secure isteyin |

`step_up`, altı iç gerekçenin (`cluster`, `conformal`, `ambiguous`,
`sequential`, `profile_deviation`, `profile_rate_limited`) tek bir dış
karşılığıdır (`backend/main.py`, `PUBLIC_REASONS`). Skorlanan istemciye hangi
kontrolün devreye girdiği bilinçli olarak söylenmez: bu bilgi saldırgana
"gönder, hangi kontrol yakaladı oku, değiştir, tekrarla" döngüsü verir. İç
gerekçe denetim kaydında ve SOC panosunda durur.

**Ek doğrulamanın sonucu tarayıcıda değil sunucuda tutulur.** Demo'da
`POST /api/demo/verify` kodu doğrular ve oturuma yazar; sonraki karar bunu
okur (`VERIFICATION_VALID_S = 300` saniye geçerli). Gerçek entegrasyonda bu
adım sizin SMS / 3-D Secure sağlayıcınızdır ve doğrulamayı DeepCheck'e
bildireceğiniz genel bir uç nokta **henüz yoktur** — bugün bunu yalnızca demo
akışı yapabiliyor (bkz. §9, eksikler). Doğrulama yalnızca `verify` kararını
yükseltir; `block` hiçbir kodla aşılamaz.

---

## 3. WebView tabanlı e-cüzdanlar

Uygulama içi bir WebView web sayfasıdır: SDK aynı script etiketiyle çalışır,
API aynı REST API'dir, entegrasyon bölüm 2 ile **birebir aynıdır**. İki dürüst
sınır:

- **Dokunmatik hakkında hiçbir oranımız yok.** SDK Pointer Events kullanır ve
  dokunmatik girdiyi ayrı bir girdi türü olarak tanır; müşteri profili de
  dokunmatiği ayrı tutar. Ama simülatörün parmak modeli olmadığı için
  `train_model.simulate_identity_sessions` dokunmatik talebini reddeder
  (`docs/profile-evaluation.md` §1). Telefonda katman çalışır; **kalitesi
  ölçülmedi.**
- **WebView yolu uçtan uca test edilmedi.** Teknik bir engel bilinmiyor,
  ama "çalışıyor" demek için bir koşu gerekir; yapılmadı.

WebView'da dikkat edilecek iki pratik nokta: SDK'nın çalıştığı sayfa `apiUrl`
ile farklı kökendeyse `CORS_ORIGINS` ayarlanmalıdır (öntanımlı `*`, üretimde
daraltın), ve WebView arka plana atıldığında `focus_changes` kanalı normal bir
sekme gizlenmesi gibi davranır.

---

## 4. Yerel (native) Android / iOS — YOL HARİTASI

**Bugün yerel bir SDK yoktur ve yazılmadı.** API platformdan bağımsız düz
REST'tir, yani yerel bir uygulama aynı yükü kendisi üretebilir; aşağıdaki
sözleşme, yazılacak bir yerel SDK'nın neyi üretmesi gerektiğini tanımlar.
Bu bölüm bir **tasarım**dır, ölçüm değildir.

### 4.1 Oturum açma (iki adım)

1. `POST /api/session` → `{ session_id, challenge, difficulty_bits }`.
2. İstemci, SHA-256 özeti `difficulty_bits` kadar (öntanımlı 12) sıfır bitle
   başlayan bir `nonce` bulur ve iki çalışma zamanı ölçümüyle birlikte
   `POST /api/session/attest` çağırır:

```json
{
  "session_id": "…",
  "challenge": "…",
  "nonce": "41233",
  "runtime": { "clock_resolution_us": 100.0, "timer_lag_ms": 4.0 }
}
```

Yanıt `{ session_id, token, attested }`. Jeton 30 dakika geçerlidir
(`SESSION_TOKEN_TTL_S`); 401 alındığında istemci **bir kez** yeniden kayıt
olup aynı pencereyi göndermelidir.

> **İş ispatının sınırı, açıkça:** bu kontrol istemcinin tarayıcı olduğunu
> kanıtlamaz. Düz bir Python istemcisi (yalnızca `hashlib` ve elle yazılmış iki
> çalışma zamanı değeri) 50 denemenin 50'sinde geçerli jeton aldı
> (2026-09-19, `backend/main.py`, "Runtime attestation" yorumu). Kanıtladığı
> tek şey, oturum başına küçük bir maliyet ödendiğidir.

### 4.2 Akış yükü

`POST /api/analyze`, başlıkta `X-DeepCheck-Token`. Gövde:

| Alan | Tür | Üst sınır | İçerik |
|---|---|---|---|
| `session_id` | string | 128 | Sunucudan gelen kimlik |
| `mouse_trajectory` | `[{x, y, t}]` | 2000 | Birincil işaretçi hareketi |
| `click_timing` | `[{x, y, t}]` | 500 | Tıklamalar |
| `scroll_events` | `[{scrollY, t}]` | 1000 | Kaydırma konumu |
| `hesitation_intervals` | `[ms]` | 500 | 400 ms ve üzeri boşlukların süresi |
| `focus_changes` | `[t]` | 200 | Odağın kaybedildiği anlar |
| `key_events` | `[{t}]` | 1000 | **Yalnızca zaman damgası** — tuş asla |
| `client_signals` | nesne | — | `untrusted_events`, `webdriver`, `pointer_mouse`, `pointer_pen`, `pointer_touch` (kaydedilir, skora girmez) |
| `client_sent_at` | ms | — | Gönderenin kendi saati |

Zamanlama sözleşmesi, model bu pencerede eğitildiği için **aynen**
uygulanmalıdır: **10 saniyelik kayan pencere**, **2 saniyede bir gönderim**
(`sdk/deepcheck.js`, `ROLLING_WINDOW_MS`, `DEFAULT_INTERVAL_MS`). Bütün zaman
damgaları tek bir saatten gelmelidir; sunucu mutlak saat karşılaştırmaz,
olayların **gönderenin saatine göre** yaşına ve oturumun saat farkının
kararlılığına bakar (`MAX_OFFSET_DRIFT_MS = 10.000` ms).

İki kural yerel istemcide de geçerlidir:

- **Zaman damgalı hiçbir olay yoksa akış gönderilmez.** Boş pencereler
  birbirinin aynısıdır ve sunucunun tekrar-oynatma (replay) kontrolü ikincisini
  reddeder.
- **Aynı anda birden fazla analiz isteği açık olmamalıdır.**

### 4.3 Yerel yolda ne yapılamaz

- **Dokunmatik için ölçüm yok** (bölüm 3).
- Model tarayıcıda toplanmış veriyle eğitildi; yerel bir uygulamanın olay
  frekansı ve zaman kuantalanması farklıdır. Yerel bir SDK, **önce yeniden
  ölçüm** gerektirir: yerel yolun skorlarının ne anlama geldiği ölçülmeden
  üretime alınmamalıdır.
- Yerel istemcide "iş ispatı" maliyeti pil ve gecikme demektir; `difficulty_bits`
  ortam değişkeniyle ayarlanabilir (`POW_DIFFICULTY_BITS`).

---

## 5. Müşteri profili: rıza, referans ve geri bildirim

Müşteri profili katmanı **isteğe bağlıdır ve öntanımlı olarak kapalıdır**.
Yaptığı tek şey, `allow` veya `warn` olan bir kararı `verify`ye çevirmektir:
hiçbir zaman bloklamaz, skoru ve etiketi değiştirmez, eşleşen bir oturuma da
hiçbir avantaj sağlamaz (`backend/test_profiles.py`,
`test_profile_layer_never_blocks_and_never_changes_the_score`, 1.440
kombinasyon).

### 5.1 Satıcı kimlik bilgisi

`DEEPCHECK_MERCHANT_KEYS="acme:<32+ karakterlik anahtar>,globex:<başka anahtar>"`.
İstekte `X-Merchant-Id` ve `X-Merchant-Key` başlıkları gönderilir. Anahtar
satıcının sunucusunda kalır; **tarayıcıya asla verilmez**.

`customer_ref` satıcının kendi müşteri referansıdır (hesap numarası, telefon,
e-posta; yazdırılabilir ASCII, en fazla 200 karakter). DeepCheck onu saklamaz:
`HMAC-SHA256` ile satıcıya özel bir `profile_id` türetir
(`backend/profiles.py`, `derive_profile_id`). Ham referans hiçbir tabloda,
yanıtta ya da günlükte bulunmaz; satıcı ad alanları ayrı olduğu için profiller
satıcılar arasında eşleştirilemez.

Davranış kuralları:

- Referans var ama satıcı başlığı yok → **400**.
- Referans var, başlık yanlış → **401**.
- Referans biçimi bozuk → **400**; değerin kendisi yanıtta **asla** yankılanmaz.
- Kimlik bilgisi doğrulandıktan sonra katman kapalıysa referans **sessizce yok
  sayılır**: hiçbir şey okunmaz, yazılmaz, hız kovası harcanmaz ve karar
  referans hiç gönderilmemiş gibi verilir (yanlış yapılandırma satıcının
  ödemesini bozmamalıdır). Yani kimlik bilgisi kontrolü katmandan **önce**
  gelir: yanlış anahtar, katman kapalıyken de 401 alır.

### 5.2 Rıza olmadan profil yok

```
POST /api/profile/consent   { "customer_ref": "...", "basis": "explicit_consent" }
```

Profil satırını oluşturabilen **tek** çağrı budur; `/api/decision` asla profil
oluşturmaz. Yanıtlar: 201 (açıldı), 409 (müşteri daha önce itiraz etti —
itiraz kaydı kalıcıdır), 400 (geçersiz referans ya da dayanak), 401, 503
(katman kapalı).

Kod iki dayanak kabul eder: `explicit_consent` ve `contract_necessity`.
Kişiye bağlı davranış profili KVKK md. 6 ve GDPR md. 4(14) anlamında
**biyometrik (özel nitelikli) veridir**; bu veri türü için sözleşmenin ifası
istisnalar arasında sayılmaz. **Pratikte yalnızca açık rıza kullanın**;
ayrıntı [`kvkk-aydinlatma.md`](kvkk-aydinlatma.md) ve [`dpia.md`](dpia.md).

### 5.3 Silme ve itiraz

```
POST /api/profile/erase     { "customer_ref": "...", "mode": "erase" | "object" }
```

Her zaman **204** döner — profil olsun olmasın aynı yanıt, çünkü yanıtın
kendisi bir müşterinin profillenip profillenmediğini söylememelidir. `erase`
vektörleri ve profil satırını siler, oturumlardaki, karar denetim
kayıtlarındaki ve erişim kayıtlarındaki takma ad bağını düşürür, karar
kayıtlarında saklanan karşılaştırma vektörünü temizler. `object` aynı silmeyi
yapar ve ayrıca sonraki rızaları engelleyen bir itiraz kaydı bırakır; bu kayıt
sonraki bir `erase`'den de sağ çıkar.

### 5.4 Sonuç geri bildirimi (öneri: bağlayın)

```
POST /api/outcome           { "session_id": "...", "outcome": "settled" | "disputed" }
```

Her zaman 204. Öğrenme yetkilendirme anında olur (bir demoda ve pilotta
mutabakat akışı yoktur), bu yüzden **itiraz edilmemiş bir dolandırıcılık
müşterinin tamponunda oturabilir.** Bunu düzelten uç nokta budur:

- `disputed` o oturumun vektörünü **siler** (tam geri alma; tampon zaten
  istatistiğin kendisidir).
- `settled`, ek doğrulamayı geçtiği için "onay bekleyen" (probation) olarak
  saklanan bir vektörü referansa yükseltir.

Mutabakat akışınızı bu uca bağlamazsanız katman yine çalışır; kaybettiğiniz
şey, dolandırıcılık bildirildiğinde profili düzeltme imkânıdır.

### 5.5 İnsan incelemesi

```
GET /api/profile/review/{session_id}   (X-Review-Operator + X-Review-Key)
```

Ayrı bir operatör kimlik bilgisiyle çalışır (**SOC pano anahtarı değil**), ve
her erişim — 404 dâhil — bir erişim kaydı yazar. Karar denetim kaydı 90 gün
saklandığı için, günler sonra gelen bir itiraz da cevaplanabilir
(GDPR md. 22(3), KVKK md. 11(1)(g)).

### 5.6 Katmanı açmadan önce

`.env`: `PROFILE_LAYER=1` hesaplar, saklar ve denetime yazar (**gölge modu**);
`PROFILE_ESCALATION=1` ayrıca ek doğrulama istemesine izin verir.
`DEEPCHECK_PROFILE_KEY` yoksa katman **hiçbir modda** açılmaz (DEBUG dâhil).

Dürüst iş sırası: önce gölge modunda çalıştırın, kendi trafiğinizde
ek doğrulama oranını ölçün, sonra uygulamaya alın. Yayımlanan oranlar sentetik
kimliklerden gelir ve **alt sınırdır** (`docs/profile-evaluation.md` §5:
aynı kişi fare %4,9 / klavye %3,8; farklı kişi fare %47,5 / klavye %26,4).
Bir müşterinin olgun sayılması için girdi türü başına **19 referans oturum**
gerekir ve günde en fazla 3 oturum öğrenilir — yani en hızlı olgunlaşma 7
gündür.

---

## 6. Sentetik demo müşterileri (jüri prototipi)

Ekibin müşteri tabanı olmadığı için jüri prototipi **sentetik demo
müşterileriyle** çalışır (`backend/demo_seed.py`). Bu bilinçli bir seçimdir ve
pazarlık konusu olmayan tek şey etikettir: sentetik her müşteri, oturum ve sayı
arayüzde ve SOC panosunda **"Sentetik demo verisi"** rozetiyle görünür, demo
ad alanı gerçek satıcıların ad alanından ayrıdır (`demo` kimliği rezervedir ve
hiçbir gerçek satıcı alamaz) ve bu veriler **hiçbir ölçüme girmez**
(`backend/profile_lab.py` sunulan veritabanını hiç açmaz). Demo sayfasında
bırakılan her şey 24 saatte silinir.

Prosedürün tamamı: [`juri-cevaplari.md`](juri-cevaplari.md),
"Demo Prosedürü — Sentetik demo müşterileri".

---

## 7. Yapılandırma özeti

| Değişken | Zorunlu | Ne işe yarar |
|---|---|---|
| `DEEPCHECK_SECRET` | evet (`DEBUG=0`) | Oturum jetonu ve iş ispatı imzası |
| `DASHBOARD_KEY` | evet (`DEBUG=0`) | SOC panosu uç noktaları |
| `CORS_ORIGINS` | hayır | İzinli kökenler (öntanımlı `*`; üretimde daraltın) |
| `DEEPCHECK_MERCHANT_KEYS` | profil için | `id:key` çiftleri, anahtar ≥ 32 karakter |
| `DEEPCHECK_PROFILE_KEY` | profil için | Takma ad türetme anahtarı; **yedeği yoktur** |
| `DEEPCHECK_PROFILE_KEY_VERSION` | hayır | Anahtar sürümü (öntanımlı 1) |
| `PROFILE_LAYER` / `PROFILE_ESCALATION` | hayır | Gölge modu / uygulama (ikisi de öntanımlı 0) |
| `PROFILE_REVIEW_KEYS` | inceleme için | `operatör:anahtar` çiftleri |
| `RAW_RETENTION_HOURS` / `ROW_RETENTION_HOURS` | hayır | Ham telemetri 1 sa, satır 24 sa |
| `PROFILE_RETENTION_DAYS` / `PROFILE_ACCESS_RETENTION_DAYS` | hayır | 180 gün hareketsizlik / 365 gün erişim kaydı |
| `UVICORN_WORKERS` | hayır | Öntanımlı 2 (bellek nedeniyle; §8.4) |

> **Anahtar değişimi uyarısı.** `DEEPCHECK_PROFILE_KEY` değiştirilirse bütün
> `profile_id` değerleri yeniden türetilir ve **her profil sessizce sıfırlanır**;
> ham referans saklanmadığı için eski profiller yeni anahtarla eşleştirilemez.
> Bu bilinçli bir takastır: anahtar uzun ömürlü olmalıdır.

---

## 8. Hata yönetimi ve aşırı yük

### 8.1 Kapalı devre (fail closed) — sözleşmenin en önemli maddesi

Karar alınamıyorsa yanıt **`verify`**'dir, asla `allow`. Skorun yokluğu
masumiyet kanıtı değildir; SDK'yı hiç çalıştırmayan bir istemcinin durumu tam
olarak budur.

| Durum | Sonuç |
|---|---|
| Oturum kaydı yok, 3 akıştan az kanıt, bayat telemetri | `verify` |
| Model yüklenemedi | `/api/analyze` 503; `/api/health` durumu söyler |
| Skor yok ya da sonlu değil | `verify` (`get_action`) |
| Profil okuması başarısız (veritabanı) | 503 — satıcı bunu `verify` saymalıdır |
| Profil devre kesicisi sayılamıyor | Katman hiçbir şey söylemez |
| Denetim kaydı yazılamadı | Günlüklenir, geri alınır; karar yine de geçerlidir |
| Karar çağrısı tamamen başarısız (ağ, 5xx) | **Satıcının entegrasyonu bunu `verify` saymalıdır** |

Son satır DeepCheck'in dışarıdan zorlayamayacağı tek maddedir ve entegre edenin
yükümlülüğüdür: `try/catch` bloğunuzun `catch` dalı ödemeyi geçirmemelidir.

### 8.2 Hız sınırları

`RATE_LIMITS` (işçi başına, bellekte): IP başına oturum açma 10/dk, oturum
başına akış 60/dk, oturum başına ödeme kararı 20/dk, **müşteri başına**
profilli karar 60/saat, satıcı başına profil yönetimi 600/dk.

Müşteri kovası dolduğunda **karar reddedilmez**: katman profili okumaz ve
uygulama modundayken `allow|warn` kararını `verify`ye çevirir. Sebebi ölçülü
bir seçimdir — kovayı tüketip profilsiz yargılanabilen bir saldırgan, kovanın
var olma nedenini ortadan kaldırırdı.

### 8.3 429 ve 503 aldığınızda

- **SDK'da geri çekilme yok** (§2.1). Bir yük boşaltma tasarımı
  `TECHNICAL_GUIDE.md` §17.7'de yazılıdır ve **uygulanmadı**: 503/429'da
  `Retry-After` varsa ona uyup yoksa aralığı 16 saniyeye kadar ikiye katlamak
  ve tam jitter uygulamak. Jitter olmadan yeniden başlatılan bir dağıtım bütün
  açık sayfalara aynı yeniden deneme anını verir.
- **Satıcı sunucusunda:** `/api/decision` **boşaltılmamalıdır** — ödeme başına
  tek istektir ve başarısızlık modu zaten `verify`dir. Boşaltılacak olan
  `/api/analyze`'dir: düşen bir akış 2 saniyelik bir kanıt penceresi kaybettirir
  ve SDK'nın kayan tamponu onu zaten bir sonraki akışta yeniden gönderir.

### 8.4 Çökme riski

CPU yüzünden hayır: tavan aşıldığında istekler **kuyruğa girer ve yavaşlar**,
düşmez ve süreç ölmez (`TECHNICAL_GUIDE.md` §17.5). Skorlama iş parçacığı
havuzunda çalışır, yani olay döngüsünü bloklamaz — bu düzeltmeden önce 20
eşzamanlı akışta olay döngüsünde p95 762 ms, en fazla 1510 ms gecikme ölçülmüştü
(`AUDIT.md` C-3) ve asıl tehlike buydu: zaman aşımına uğrayan bir sağlık
kontrolü **sağlıklı** bir konteyneri yeniden başlattırır.

Gerçekten devirebildiğimiz tek şey **bellekti, yük değil**: dört işçi
forestların ve SHAP açıklayıcısının dörder kopyasını tutar (~300–400 MB),
Docker Desktop'ın ~2 GB'lık sanal makinesinde makine takas alanına düşer ve
`/api/health` CPU %1'deyken 25 saniyede cevap verdi. `UVICORN_WORKERS`
öntanımlı olarak bu yüzden 2'dir. **Ana makineyi yalnızca çekirdek sayısına
göre boyutlandırmak bu hatayı tekrarlar.**

Sınırlanmayanlar, açıkça: eşzamanlı istek sayısına tavan yok, veritabanı
bağlantı havuzu SQLAlchemy öntanımlarında (işçi başına 5 + 10, 30 saniyelik
bekleme), hız sınırları işçi başına ve bellekte.

---

## 9. Kapasite ve maliyet — ölçümden türetilmiş hesap

Bu bölümün tamamı `TECHNICAL_GUIDE.md` §17'dendir ve aynı kaynaklardan aynı
aritmetikle yeniden hesaplanmıştır. **Yük testi yapılmadı.**

**Yük işlem sayısıyla değil, eşzamanlı ziyaretçi sayısıyla artar.** SDK sayfa
açıkken 2 saniyede bir gönderir, yani bir aktif oturum saniyede 0,5
`/api/analyze` demektir; 60 saniyelik bir ödeme ≈ **30 akış + 1 karar**.

| Yol | Değer | Türü | Kaynak |
|---|---|---|---|
| `compute_risk` (öznitelik + RF + SHAP) | 17,7 ms | ölçüm | `backend/scorer.py` |
| `/api/decision`, profil kapalı | p50 5,0 / p95 7,4 ms | ölçüm | `profile-evaluation.md` §11 (konteyner, n=300) |
| `/api/decision`, profil açık (gölge) | p50 24,0 / p95 34,8 ms | ölçüm | aynı |
| `/api/decision`, profil açık, ek doğrulama istiyor | p50 16,0 / p95 21,8 ms | ölçüm | aynı |
| `/api/analyze` uçtan uca | **~23 ms** | **hesap** | 17,7 ms skorlama + profil kapalı karar yolunun 5,0 ms p50'si (doğrulama, oturum güncellemesi, satır yazımı, commit) |

Son satır bir kurgudur, ölçüm değil, ve bu bölümdeki en büyük hata kaynağıdır:
`/api/analyze` tek başına hiç ölçülmedi.

**vCPU başına tavan (hesap):**

```
  1000 ms / 23 ms         = 43 analiz/sn/vCPU
  43 / 0,5 (oturum başına) = ~87 eşzamanlı aktif ödeme oturumu/vCPU
```

Yöntemin tek gerçek eşzamanlılık ölçümüne yakınlığı: `AUDIT.md` C-3
(2026-07-31, eski üç modelli topluluk, istek başına ~74 ms, tek işçi) 20
eşzamanlı çağrıda **13,2 istek/sn** ölçtü; aynı hesap `1000/74 = 13,5` verir —
**%2,4 fark**. Bu eski kodda tek bir noktadır, bugünkü sayının doğrulaması
değildir; yöntemin neden alıntılandığının gerekçesidir.

**Ödeme başına maliyet (hesap):** 30 × 23 ms + 1 × 24 ms = **0,71
CPU-saniye** ⇒ bir vCPU-saat ≈ **5.000 ödeme** (3600 / 0,71). Bulut vCPU-saat
fiyatını biz satın almadığımız için bir para tutarı yazmıyoruz: ödeme başına
çıkarım maliyeti, sizin vCPU-saat fiyatınızın **1/5000**'idir. Gerçek gider
Postgres ve işletmedir.

**Profil katmanının payı:** bu 0,71'in içindedir, üstüne değil. Otuz bir
istekten birine 19 ms p50 ekler (24,0'a karşı 5,0), yani ödeme 0,695'ten
0,714 CPU-saniyeye çıkar: **+%2,7**.

**Depolama (satır boyutları Postgres 16'da ölçüldü, gerisi hesap):**

| Ne | Boyut |
|---|---|
| `behavior_data`, ham telemetriyle | 6,1 kB |
| `behavior_data`, 1 saatlik boşaltmadan sonra | 644 B |
| `customer_profile_vectors` (bir vektör) | 1.363 B |
| `decision_audit`, profil görüşü + karşılaştırılan vektör | 1.051 B |

```
  bir ödeme, ilk saat        30 x 6,1 kB = 183 kB
  1. - 24. saat              30 x 644 B  =  19 kB
  24 saat sonra                            0 B
  90 gün saklanan            0 ya da 1 denetim satırı (268 B / 1.051 B)

  bir müşteri profili, üst sınır (20 referans + 4 onay bekleyen):
    tek girdi türü    24 x 1.363 B =  32,7 kB
    1.000.000 müşteri              =  32,7 GB   (iki girdi türü: 65,4 GB)

  çalışma kümesi, saatte 1.000 ödemede:
    son 1 saat (ham)   30.000 x 6,1 kB      = 183 MB
    1-24. saat         23 x 30.000 x 644 B  = 444 MB
                                       toplam ~630 MB ve orada durur
```

Bu bir **tavandır**, beklenti değil: beş oturumu olan bir müşteri beş vektör
saklar (6,8 kB) ve müşterilerin çoğunun hiç olgunlaşmaması beklenir. Profil
tablosunda büyüme terimi yoktur; tampon zaten tahliye eder.

---

## 10. Entegrasyon kontrol listesi

- [ ] SDK sayfada, `DeepCheck.init({ apiUrl })` çağrıldı.
- [ ] Ödeme düğmesi `await DeepCheck.flush()` yapıyor.
- [ ] Karar **sunucudan** isteniyor; tarayıcıda hiçbir eşik karşılaştırılmıyor.
- [ ] `action` dışındaki hiçbir alan karar için kullanılmıyor.
- [ ] Karar çağrısı başarısız olduğunda kod `verify` yoluna giriyor (kapalı devre).
- [ ] `insufficient_evidence` için OTP değil "birkaç saniye sonra" mesajı gösteriliyor.
- [ ] Ek doğrulama sonucu sunucuda tutuluyor.
- [ ] Profil katmanı kullanılacaksa: satıcı anahtarı yalnızca sunucuda, rıza
      uç noktası bağlı, silme/itiraz yolu müşteri hizmetlerine açık,
      `POST /api/outcome` mutabakat akışına bağlı.
- [ ] Katman önce **gölge modunda** çalıştırıldı ve kendi trafiğinizde ek
      doğrulama oranı ölçüldü.
- [ ] TLS'i ters vekil sunucu sağlıyor (Docker Compose kurulumu düz HTTP'dir).
- [ ] Ana makine bellek de hesaba katılarak boyutlandırıldı (işçi başına ~400 MB).

---

## 11. Bu belgenin söylemediği şeyler

- **Yük testi yok.** Bölüm 9'daki her sayı hesaptır; kapasite ölçülmedi.
- **Gerçek müşteri ölçümü yok.** Profil katmanının bütün oranları sentetik
  kimliklerden gelir ve yanlış ek doğrulama oranları **alt sınırdır**.
- **Yerel mobil SDK yok** (bölüm 4), **dokunmatik için oran yok** (bölüm 3),
  **WebView yolu test edilmedi**.
- **Ek doğrulama sonucunu bildiren genel bir uç nokta yok**; bugün bunu yalnızca
  demo akışı yapabiliyor.
- **Uçtan uca şifreleme yok.** Dinlenmedeki veri için disk (volume) düzeyi
  öngörülür; iletimde TLS sizin ters vekil sunucunuzdadır.
- **Katman kart deneme botlarına karşı hiçbir şey yapmaz.** Misafir ödemesi
  müşteri referansı taşımaz ve bu koda hiç ulaşmaz. Katmanın hedefi insan eliyle
  hesap ele geçirmedir ve gücü, satıcının ek doğrulama kanalının gücü kadardır:
  OTP'yi zaten ele geçirmiş bir saldırgana, cevaplayabileceği bir soru sorar.
