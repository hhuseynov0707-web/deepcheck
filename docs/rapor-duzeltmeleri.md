# Ön Değerlendirme Raporu: Düzeltmeler

> **Bu dosyanın amacı.** Jüri, ön değerlendirme raporumuzu (`DeepCheckRapor.pdf`,
> Finansal Teknolojiler Yarışması) okudu. Rapordaki bazı iddialar bugünkü kodla ve
> ölçümlerle desteklenmiyor. Aşağıda her iddia için üç şey var:
> **raporda ne yazıyor**, **bugün doğru olan ne** (ölçümüyle ve kaynak dosyasıyla)
> ve **final rapora yapıştırılabilecek önerilen metin**.
>
> **Temel kural:** Bu projede **gerçek müşteri verisi yok** ve bugüne kadar hiçbir
> gerçek kişinin oturumu kaydedilmedi. Aşağıdaki her sayının neyin üzerinde
> ölçüldüğü yanında yazıyor. Kaynak üç türlü: sentetik (simülatör) veri, gerçek bir
> Chromium tarayıcısında Playwright betikleriyle yürütülen laboratuvar koşuları ve
> sentetik müşteri kimlikleri. Final raporda bu etiketi kaybeden sayı yanlış
> hâle gelir.
>
> **Teslimden önce:** Model doğruluğu ve kapasite sayıları bu çalışmada yeniden
> ölçülüyor ya da hesaplanıyor (ayrıntısı son bölümde). Rapor teslim edilmeden önce
> her sayı, yanında adı geçen dosyayla yeniden karşılaştırılmalı.

Hazırlanma tarihi: 2026-09-19. Sayfa numaraları PDF sayfa numaralarıdır. Sayılar
Türkçe yazımla verilmiştir (ondalık ayırıcı virgül); kaynak dosyalarda nokta ile
geçerler.

---

## Özet

| # | Rapordaki iddia | Durum | Kısaca doğrusu |
|---|---|---|---|
| 1 | "%98 anomali yakalama başarısı" | Ölçümü yok | Betikli tarayıcı koşularında bot akışlarının %76,1'i yakalandı, betikli insan akışlarında yanlış işaret %0 oldu. Gerçek kullanıcıda ölçülmedi. |
| 2 | Kapalı beta, gerçek kullanıcı verisiyle ince ayar | Yanlış | Sentetik veri ve 234 betikli tarayıcı akışı kullanıldı. Gerçek kişi kaydı yok. |
| 3 | RF + Isolation Forest + LSTM; tabloda "+ Transformer" | Yanlış | Skoru tek bir Random Forest üretiyor. IF ile LSTM ölçüm sonucunda skordan çıkarıldı. Transformer hiç kullanılmadı. |
| 4 | AWS Lambda, serverless, 50 ms altı | Mimari yanlış, süre kısmen doğru | Dağıtım Docker Compose ile. Skorlama 17,7 ms, karar uç noktası konteynerde p95 8,3–43,2 ms. Kapasite yük testiyle ölçülmedi. |
| 5 | "Tüm kararlar SHAP ile açıklanabilir" | Abartılı | SHAP yalnızca tek akışın RF skorunu açıklıyor. Karar katmanının adımları gerekçe koduyla açıklanıyor. |
| 6 | Yanlış hesap dondurmayı %40 azaltma | Ölçümü yok | Çıkarılmalı ya da pilotta ölçülecek bir hipotez olarak yazılmalı. Ürün zaten hesap dondurmuyor. |
| 7 | "Anonim; IP, cookie, keystore yok; sadece X-Y" | Eksik ve yanlış | SDK tuş zamanlamasını, kaydırmayı, odak değişimini ve işaretçi türünü de topluyor. Müşteri profili takma adlı biyometrik veri sayılıyor. |
| 8 | SOC ekran görüntüleri (coğrafya, ASN, Puppeteer, IP kara listesi) | Kodda yok | Bugünkü panodan yeni ekran görüntüsü alınmalı. |
| 9 | Rakip analizi | Eksik | Doğrudan bot tespiti ve davranışsal biyometri oyuncuları eklenmeli. |

---

## 1. "%98 anomali yakalama başarısı"

**Raporda ne yazıyor**

- s. 4 (§3): "Kontrollü test ortamında elde edilen %98 anomali yakalama başarısı,
  algoritmanın ayırt edici gücünü kanıtlamaktadır."
- s. 5 (§4): "anomali yakalama doğruluk oranını %98'in üzerine çıkarır" (kaynak
  olarak [6] gösterilmiş).
- s. 5'teki ekran görüntüsünde de "AI Güven Skoru (Confidence): %98.8" yazıyor.

**Bugün doğru olan**

- Depoda %98 yakalama oranı veren bir ölçüm yok. Kaynakçadaki [6] numara (Statista,
  "Online Payment Fraud") DeepCheck'in kendi başarısına kaynak olamaz.
- **Eğitimde görülmeyen betikli tarayıcı koşuları** (koşular bir bütün olarak
  dışarıda tutuldu; `docs/evaluation.md`, 2026-09-06 ölçümü):

  | Senaryo | Akış sayısı | İşaretlenen |
  |---|---|---|
  | H1_human (betikli insan) | 25 | %0 |
  | H2_keyboard_only (betikli insan, yalnızca klavye) | 11 | %0 |
  | A2_randomized (bot) | 8 | %75 |
  | A3_human_mimic (bot) | 10 | %100 |
  | A4_evasive (bot) | 28 | %68 |

  Toplamda betikli insan akışlarında yanlış işaret **%0** (36 akış), bot
  akışlarında yakalama **%76,1** (46 akışın 35'i). "İnsan" satırları da betik,
  gerçek kişi değil.
- **Aynı 234 laboratuvar akışında, koşu bazında gruplanmış çapraz doğrulama**
  (`backend/model_selection.py` protokol C). Sayılar `backend/scorer.py` içindeki
  yorumda ve `docs/juri-cevaplari.md` "Dördüncü Soru" bölümünde:
  - Random Forest'ın ROC-AUC değeri 0,988.
  - Botların %90'ı 60 ve üzeri, %63'ü 80 ve üzeri skor aldı.

  ROC-AUC bir **sıralama** ölçüsüdür, yakalama oranı değildir: rastgele seçilen
  bir bot akışının rastgele seçilen bir insan akışından yüksek skor alma
  olasılığını verir. Üstelik modelin örneklerini gördüğü senaryolar üzerinde
  ölçüldü. "%98" diye yuvarlanmamalı.
- **Hiç görülmemiş saldırı** (protokol D; senaryonun tamamı eğitimden çıkarıldı):
  ağaç modellerinin hiçbiri A2, A3 ve A4 saldırılarının %20'sinden fazlasını
  yakalayamadı (`docs/juri-cevaplari.md`, `TECHNICAL_GUIDE.md` §16).
- **Bağımsız yazılmış saldırgan düzeneği** (sınıf başına 12 oturum,
  `docs/evaluation.md`):
  - Düz çizgide hareket eden otomasyonun %100'ü bloklandı.
  - İnsan personasının %100'ü onaylandı.
  - İnsanlaştırılmış botun da **%100'ü onaylandı** (ortalama skor 22,7;
    insanlara karşı AUC 0,92).
  - Özellikleri bilerek taklit eden bot da %100 onaylandı (AUC 0,08).
- Ekran görüntüsündeki "%98.8 güven" türü değer, bugünkü panoda
  "Son pencere model kesinliği" adıyla geçiyor. Bu değer modelin seçtiği sınıfa
  verdiği olasılıktır, yani max(p, 1−p). Doğruluk oranı değildir
  (`frontend/src/pages/Dashboard.jsx` içindeki yorum).
- **Yeniden ölçülüyor.** Bu çalışmada karar katmanının ardışık testi ve konformal
  koruması değişiyor. Laboratuvar verisinde de bazı öznitelikler tavana dayanmış.
  `lab/real_telemetry.json` üzerinde yaptığımız sayım (234 akış):
  - `ivme_degisimi` 90 akışta 1,0 değerinde.
  - `duraklama_dagilimi` 109 akışta 1,0 değerinde.
  - `scroll_hizi_varyansi` bütün akışlarda aynı sabit değerde, yani hiç ölçülmemiş.

  Bu yüzden doğruluk sayıları teslimden önce yeniden kontrol edilmeli.

**Önerilen metin (§3 Yöntem)**

> DeepCheck'in doğruluğu henüz gerçek kullanıcılar üzerinde ölçülmemiştir. Bugünkü
> ölçüm, gerçek bir Chromium tarayıcısında Playwright betikleriyle yürütülen ve
> eğitimde kullanılmayan koşular üzerindedir: betikli insan davranışının %0'ı
> yanlışlıkla işaretlenmiş, bot akışlarının %76,1'i yakalanmıştır. Bu sonuç, modelin
> örneklerini gördüğü saldırı tekniklerini tanıdığını gösterir. Modelin hiç görmediği
> tekniklere genelleme yapmadığını da ölçtük: bağımsız olarak yazılmış, insan
> hareketini taklit eden bir bot durdurulamamıştır. Gerçek kullanıcılarla doğruluk
> ölçümü bir sonraki adımdır.

**Önerilen metin (§4 İş Modeli):** "%98'in üzerine çıkarır" ifadesi çıkarılmalı.
Yerine şu yazılabilir: "Bugünkü ölçümler ve sınırları için Yöntem bölümüne
bakınız."

---

## 2. "Kapalı beta testlerinden alınan gerçek kullanıcı verileriyle ince ayar"

**Raporda ne yazıyor**

- s. 4 (§3): "Başlangıçta 50.000 satırlık sentetik veri setiyle temel davranış
  kalıpları modellenmiş, ardından kapalı beta testlerinden alınan gerçek kullanıcı
  verileriyle ince ayar yapılarak gerçek dünya kaotikliğine uyum sağlanmıştır."
- Aynı raporun s. 3'ündeki (§1) cümle ise doğru: "İlk testleri de bot ve insan
  davranışlarını simüle ettiğimiz sentetik veri setleri üzerinde yaptık." Bu iki
  bölüm birbiriyle çelişiyor ve jüri bunu fark edebilir.

**Bugün doğru olan**

- Kapalı beta yapılmadı ve hiçbir gerçek kişinin oturumu kaydedilmedi.
  `data/real/human/` ve `data/real/bot/` klasörlerinde yalnızca yer tutucu dosya
  (`.gitkeep`) var. `data/real/README.md` bunu açıkça yazıyor: detektörün bugüne
  kadar gördüğü her "insan" bir betikti.
- Eğitim verisi iki kaynaktan geliyor:
  1. **Sentetik veri.** `backend/train_model.py` 25.000 sentetik oturum üretir.
     Her oturumda 10 akış penceresi vardır, toplam 250.000 pencere eder. Random
     Forest her oturumun son penceresiyle, yani 25.000 satırla eğitilir. Raporda
     geçen "50.000 satır" da güncel değil.
  2. **Betikli tarayıcı verisi.** `lab/real_telemetry.json` dosyasında 234
     etiketli akış var. Bu akışlar `lab/capture.py` ile, gerçek bir Chromium'da
     gerçek SDK üzerinden kaydedildi:
     - 47 koşu ve 6 senaryo: H1_human 50, H2_keyboard_only 44, A1_naive 16,
       A2_randomized 27, A3_human_mimic 40, A4_evasive 57 akış.
     - Tek makine ve tek tarayıcı sürümü kullanıldı.
     - "İnsan" senaryoları da betikle yürütüldü.
     - Eğitimde bu akışlar, toplamda sentetik veri kadar ağırlık alacak şekilde
       ağırlıklandırılıyor (`REAL_TELEMETRY_WEIGHT = 120`).
- Gerçek kişileri kaydetmek için gereken araçlar hazır ama henüz kullanılmadı:
  `backend/record_session.py --person` (kişi bazlı ayrım yapar) ve
  `backend/evaluate.py`. `data/real/README.md`'deki hedef, yanlış pozitif oranı
  söylemeden önce farklı kişilerden ve farklı giriş cihazlarından en az 30 insan
  oturumu toplamak.
- Müşteri profili katmanı da yalnızca sentetik kimliklerle ölçüldü
  (`docs/profile-evaluation.md`).
- Jüri prototipinde sentetik demo müşterileri kullanılacak. Bunlar arayüzde ve SOC
  panosunda sentetik olarak işaretli görünmeli ve hiçbir ölçüme dahil edilmemeli.

**Önerilen metin**

> Model iki veri kaynağıyla eğitilmiştir: (1) davranış simülatöründen üretilen
> 25.000 sentetik oturum (250.000 akış penceresi) ve (2) gerçek bir Chromium
> tarayıcısında, gerçek SDK üzerinden Playwright betikleriyle yürütülen altı
> senaryodan kaydedilmiş 234 etiketli akış. Bu tarayıcı verisindeki "insan"
> davranışı da betikle üretilmiştir; bugüne kadar gerçek bir kişinin oturumu
> kaydedilmemiştir. Gerçek kullanıcı kaydı için gereken araçlar (kişi bazlı ayrım
> yapan kayıt ve değerlendirme betikleri) hazırdır. Bir sonraki adım, farklı
> kişilerden ve farklı giriş cihazlarından (fare, dokunmatik yüzey, dokunmatik
> ekran, yalnızca klavye) en az 30 oturum toplamaktır.

---

## 3. "Random Forest ve Isolation Forest ... PyTorch tabanlı LSTM" ve "Random Forest + LSTM + Transformer"

**Raporda ne yazıyor**

- s. 4 (§3): "Random Forest ve Isolation Forest non-lineer davranış örüntülerinde
  yüksek başarı sağlarken, zaman bağımlı analizler için PyTorch tabanlı LSTM
  modelleri devreye alınır."
- s. 6 (§5), rakip tablosunun "AI Modeli" satırı: "Random Forest + LSTM +
  Transformer".
- s. 7 (§7), proje takvimi: "hibrit model (RF, Isolation Forest) eğitimi".

**Bugün doğru olan**

- **Skoru tek bir Random Forest üretiyor.** `backend/scorer.py` içindeki
  `compute_risk()` fonksiyonu `rf.predict_proba` sonucunu kullanır ve skor
  100 × P(dolandırıcılık) olarak hesaplanır. Model 200 ağaçlı ve en fazla 12
  derinlikte (`TECHNICAL_GUIDE.md` §7).
- **Transformer yok.** Depodaki kod dosyalarının hiçbirinde Transformer geçmiyor
  (2026-09-19 tarihli kod araması).
- **Isolation Forest skordan çıkarıldı (ağırlığı 0).** Kaynak: `backend/scorer.py`
  ve `TECHNICAL_GUIDE.md` §7.
  - Eğitimde görülmeyen 82 gerçek tarayıcı akışında tek başına ROC-AUC değeri
    **0,340** çıktı. Bu rastgeleden bile kötü, yani model ters çalışıyor: yalnızca
    insan satırlarıyla eğitildiği için insana benzeyen saldırıyı "normal" sayıyor.
  - Isolation Forest'lı karışımın AUC değeri 0,977, onsuz karışımınki 0,990.
  - Çıkarılması skorlama süresini de 31,7 ms'den 17,7 ms'ye indirdi.
  - Model hâlâ eğitilip pakette saklanıyor ama istek başına çağrılmıyor.
- **LSTM skordan çıkarıldı.** Kaynak: `backend/scorer.py`.
  - Yalnızca simülatörle eğitildiği için tarayıcı trafiğinde çıktısı "insan"
    tarafına çöktü (ROC-AUC 0,947 ama Brier skoru 0,51).
  - 234 laboratuvar akışında 60 ve üzeri skora ulaşan bot oranı RF 0,6 + LSTM 0,4
    karışımında 0,79, RF tek başına 0,90. 80 ve üzeri için oranlar 0,54 ve 0,63.
  - Oturum ortasındaki devir-teslimi RF'den 4 akış geç yakaladı.
  - Model tanımı `backend/lstm_model.py`'de duruyor ve yalnızca `TRAIN_LSTM=1` ile
    eğitiliyor.
- **Zaman boyutu açık bir kuralla ele alınıyor.** Oturum skoru son 5 akışın
  medyanıdır. Mevcut akış önceki medyanın 35 puan üstüne sıçrarsa yumuşatma bu
  sıçramayı bastıramaz. Simülasyonda (`backend/scorer.py`, `smooth_session_score`):
  - 185 devir-teslimin 185'i ilk otomatik akışta yakalandı.
  - 14.790 meşru akıştan en fazla 6'sının skoru değişti.
- **Gradient boosting denendi ama benimsenmedi.** Kaynak: `backend/scorer.py` ve
  `docs/juri-cevaplari.md`.
  - LightGBM ve XGBoost, ROC-AUC'ta RF ile aynı düzeyde. Görülen senaryolarda
    80 ve üzeri skorla daha çok bot yakaladılar (0,89'a karşı 0,63).
  - Ancak H1 insan senaryosu eğitimden çıkarıldığında LightGBM bu görülmemiş
    insanların %74'ünü blokladı. RF hiçbirini bloklamadı.
- Çalışma yeniden üretilebilir: `cd backend && python model_selection.py` komutu 7
  model ailesini 4 protokolle karşılaştırır (dizüstü bilgisayarda 6–8 dakika).

**Önerilen metin (§3 Yöntem)**

> Risk skoru tek bir Random Forest sınıflandırıcısından (200 ağaç) gelir:
> Risk Skoru = 100 × P(dolandırıcılık | davranış). Isolation Forest, LSTM ve
> gradient boosting modelleri de denenmiş, ölçüm sonucunda skordan çıkarılmıştır.
> Isolation Forest gerçek tarayıcı verisinde ters çalışmıştır (ROC-AUC 0,34).
> Yalnızca simülatörle eğitilen LSTM, tarayıcı trafiğinde bot yakalamayı
> düşürmüştür (60 ve üzeri skorda %90'dan %79'a). LightGBM ise eğitimde görmediği
> betikli insan senaryosunun %74'ünü bloklamıştır. Zaman boyutu, oturumun son beş
> akışının medyanı ve ani sıçramaları yumuşatmadan geçiren açık bir kuralla ele
> alınır. Model seçimi çalışması depoda yeniden üretilebilir durumdadır.

**Rakip tablosu hücresi (AI Modeli, DeepCheck sütunu):** "Random Forest (tek model;
LSTM, Isolation Forest ve gradient boosting ölçülüp elendi)".

**Takvim (Temmuz):** "25.000 oturumluk sentetik veri üreteci, öznitelik
mühendisliği ve Random Forest eğitimi. Isolation Forest ile LSTM denendi, ölçüm
sonucunda skordan çıkarıldı."

---

## 4. "AWS Lambda tabanlı serverless mimari ... 50 ms altında"

**Raporda ne yazıyor**

- s. 4 (§3): "AWS Lambda tabanlı serverless mimari sayesinde sistem 50 ms altında
  tepki süresiyle çalışır. Her istek bağımsız ölçeklenir ve soğuk başlatma (cold
  start) süresi minimize edilmiştir."
- s. 7 risk tablosu (Ölçeklenebilirlik): "AWS tabanlı serverless altyapı ve
  event-driven asenkron veri işleme."
- s. 3 (§1): "50 milisaniye gibi çok kısa bir sürede".
- s. 5 (§4): "saniyede 50'den fazla mikro davranış verisini 50 milisaniyenin altında
  analiz ederek".
- s. 7 takvim: "SOC dashboard (50ms yanıt süreli)".

**Bugün doğru olan: mimari**

- Dağıtım Docker Compose ile yapılıyor. `docker-compose.yml` üç servis tanımlar:
  - PostgreSQL 16 (`postgres:16-alpine`),
  - FastAPI arka ucu (Uvicorn ile),
  - nginx ile sunulan React ön yüzü.

  Sistem tek makinede `docker-compose up --build` komutuyla çalışır. Portlar
  varsayılan olarak yalnızca 127.0.0.1 adresine açılır.
- AWS Lambda kullanılmıyor. `README.md`'nin Deployment bölümünde "Docker Compose +
  Uvicorn, and nothing else" yazıyor. Bir zamanlar depoda duran ve hiç test
  edilmemiş Lambda adaptörü kaldırıldı.
- Varsayılan olarak 2 Uvicorn işçisi çalışıyor (`backend/entrypoint.sh`). Her işçi
  modelleri ve SHAP açıklayıcısını kendi belleğinde tutar, bu da işçi başına
  yaklaşık 300–400 MB eder.
  - 4 işçi, Docker Desktop'ın yaklaşık 2 GB'lık sanal makinesinde belleği taşırdı:
    `/api/health` 25 saniyede yanıt verdi (`README.md`, `backend/entrypoint.sh`).
  - Yani bu kurulum bugün CPU'dan önce bellekle sınırlı.
- "Her istek bağımsız ölçeklenir" cümlesi bugün doğru değil. Hız sınırları her
  işçinin kendi belleğinde tutuluyor (`TECHNICAL_GUIDE.md` §15). Paylaşılan tek
  durum PostgreSQL. Birden fazla makineye ölçeklemek için paylaşılan bir hız sınırı
  deposu gerekiyor; bu henüz yapılmadı.
- "Event-driven" veri işleme de yok. SDK her 2 saniyede bir REST isteği gönderiyor.
  Kuyruk ya da olay altyapısı bulunmuyor.

**Bugün doğru olan: süre (depoda var olan ölçümler)**

| Ne ölçüldü | Sonuç | Koşullar | Kaynak |
|---|---|---|---|
| `compute_risk`: tek akışta öznitelik çıkarımı + Random Forest + SHAP | 17,7 ms (Isolation Forest çağrısıyla 31,7 ms idi) | Isolation Forest'ın çıkarıldığı ölçüm; ağ ve veritabanı yazımı dahil değil | `backend/scorer.py` |
| `/api/decision`, müşteri profili kapalı | p50 5,5 ms / p95 8,3 ms (tekrarı: 5,7 / 8,7 ms) | Konteyner, docker-compose topolojisi, Linux, n=300. ASGI uygulaması doğrudan çağrıldı (HTTP istemcisi yok). Geçici PostgreSQL 16, sentetik oturumlar | `docs/profile-evaluation.md` §11 |
| `/api/decision`, profil gölge modda | p50 26,1 ms / p95 40,5 ms | aynı | aynı |
| `/api/decision`, profil uyguluyor ve öğreniyor | p50 25,8 ms / p95 43,2 ms | aynı | aynı |
| Aynı ölçüm, Windows ana makinesinden Docker Desktop port yönlendirmesiyle | Profil kapalıyken p95 17,7 ms. Profil açık yollarda p95 39,9–81,6 ms; beş yapılandırma **50 ms bütçesini aştı** | Windows geliştirme makinesi | aynı |

- Bu süreler ağ gecikmesini ve tarayıcı tarafını içermiyor, yani uçtan uca süre
  değiller.
- `README.md` "Performance" tablosundaki 42,4 ms ortalama, üç modelli eski topluluk
  için ölçülmüştü ve bugünkü sistemi tanımlamıyor. `TECHNICAL_GUIDE.md` §7 ve
  §16'daki "42 ms" ifadeleri de aynı durumda. Bu sayılar rapora alınmamalı.
- Depodaki tek eşzamanlılık ölçümü eski koda ait: `AUDIT.md` C-3 (2026-07-31).
  - Tek işçide 20 eşzamanlı `/api/analyze` çağrısıyla saniyede 13,2 istek ölçüldü.
    Buradan "sürdürülebilir eşzamanlı oturum ≈ 26" sonucu çıkarıldı.
  - O sırada skorlama olay döngüsünü bloke ediyordu, skorlama yaklaşık 74 ms
    sürüyordu ve sunucu tek işçiyle `--reload` modunda çalışıyordu.
  - O günden bu yana skorlama iş parçacığı havuzuna taşındı ve 17,7 ms'ye indi.
    Varsayılan işçi sayısı da 2 oldu.
  - Bu yüzden bu ölçüm bugünkü sistemi tanımlamıyor. `TECHNICAL_GUIDE.md` §16'daki
    "bir işçi yaklaşık 20 oturumu karşılar" cümlesi de güncel bir ölçüme
    dayanmıyor.
- "Saniyede 50'den fazla mikro davranış verisi" ölçülmüş bir sayı değil. SDK her
  işaretçi olayını tarayıcının ürettiği sıklıkta kaydeder ve her 2 saniyede son 10
  saniyelik pencereyi gönderir (`sdk/deepcheck.js`). Saniyedeki olay sayısı cihaza
  ve kullanıcıya göre değişir.
- SOC panosu 3 saniyede bir yenileniyor. "Ortalama Yanıt Süresi" kartı akış başına
  skorlama süresini (`response_time_ms`) gösteriyor, uçtan uca süreyi değil
  (`frontend/src/pages/Dashboard.jsx`).
- **Kapasite henüz ölçülmedi.** Eşzamanlı istek kapasitesi bir yük testiyle
  ölçülmedi. Bu çalışmada, var olan ölçümlerden yola çıkarak aritmetikle bir
  kapasite, maliyet ve aşırı yük değerlendirmesi hazırlanıyor (`TECHNICAL_GUIDE.md`;
  entegrasyon açısından `docs/entegrasyon.md`). Bu değerlendirme kesinleşmeden
  raporda saniyedeki istek sayısı ya da eşzamanlı kullanıcı sayısı verilmemeli.
  Verilecekse "ölçümden türetilmiş hesap, yük testi değil" etiketiyle verilmeli.
  Ekran görüntüsündeki "4,205 istek/sn" ve "24 ms" değerleri ölçüm değil.

**Önerilen metin (§3 Yöntem)**

> DeepCheck, Docker Compose ile tek komutla ayağa kalkan üç servisten oluşur:
> PostgreSQL veritabanı, FastAPI arka ucu ve React ön yüzü. Kurum sistemi kendi ağı
> içinde çalıştırabilir; davranış verisi kurumun altyapısından çıkmaz. Tek bir
> davranış akışının skorlanması (öznitelik çıkarımı, Random Forest ve SHAP
> açıklaması) 17,7 ms olarak ölçülmüştür. Ödeme anındaki karar uç noktası konteyner
> içinde, müşteri profili kapalıyken p95 8,3 ms, açıkken en fazla p95 43,2 ms
> ölçülmüştür. Bu süreler ağ gecikmesini içermez. Yüksek eşzamanlı yük altındaki
> kapasite henüz bir yük testiyle ölçülmemiştir.
> *[Kapasite bölümü tamamlandığında buraya o bölümün hesapla türetilmiş rakamları
> "hesap" etiketiyle eklenecek.]*

**Risk tablosu hücresi (Ölçeklenebilirlik)**

> Oturum ve karar durumu PostgreSQL'de tutulur. Arka uç süreçleri çoğaltılabilir,
> ancak hız sınırları henüz her sürecin kendi belleğinde tutulmaktadır. Birden fazla
> makineye ölçeklemek için paylaşılan bir hız sınırı deposu ve aşırı yükte istek
> boşaltma gerekir; ikisi de henüz uygulanmamıştır. Kapasite bir yük testiyle
> ölçülmemiştir.

Kaynakçadaki [2] (AWS Event-Driven Architecture), Lambda iddiasıyla birlikte
kaldırılmalı.

---

## 5. "Sistemin tüm kararları SHAP metodlarıyla açıklanabilir"

**Raporda ne yazıyor**

- s. 5 (§3): "Sistemin tüm kararları SHAP metodlarıyla açıklanabilir hale
  getirilmiştir [4]."
- s. 6 (§5): "kullanıcı kararlarını Açıklanabilir Yapay Zeka (SHAP) kullanılarak
  şeffaflaştırır".
- Rakip tablosu: "Tam SHAP entegrasyonu".

**Bugün doğru olan**

- SHAP (`shap.TreeExplainer`, `backend/scorer.py`) **tek bir akışın Random Forest
  olasılığını** açıklıyor. 12 özniteliğin her birinin katkısı hesaplanıyor. En büyük
  üçü saklanıyor ve SOC panosunda "En Etkili 3 Özellik (SHAP)" başlığıyla
  gösteriliyor (`frontend/src/pages/Dashboard.jsx`). Ağaç modellerinde bu hesap
  kesin sonuç veriyor.
- SHAP'ın **açıklamadığı** karar adımları:
  - **Oturum skoru.** Panodaki rozet, son 5 akışın medyanı ve sıçrama kuralıyla
    yumuşatılmış skoru gösterir. SHAP çubukları ise yalnızca son akışı anlatır:
    `session.shap_explanation` son akıştan yazılır (`backend/main.py`).
  - **Ardışık test (SPRT).** "Yeterli kanıt yok", "belirsiz" ve "biriken kanıt"
    kararları (`insufficient_evidence`, `ambiguous`, `sequential`).
  - **Diğer kurallar.** Konformal koruma (bloğu ek doğrulamaya çevirir), küme
    kuralı (varsayılan olarak kapalı) ve bayat veri kuralı (`stale`).
  - **Müşteri profili katmanı.** Bu katmanın kendi açıklaması var: müşterinin kendi
    geçmişinden en çok sapan K=3 öznitelik (z değerleriyle) ve konformal p-değeri.
    SOC'taki "Müşteri Profili" kartında gösteriliyor
    (`frontend/src/components/ProfilePanel.jsx`). Bu SHAP değil.
  - **Ek doğrulama (step-up).** Doğrulanmış bir oturumda `verify` kararının
    `allow`'a yükseltilmesi.
- Bu adımların her biri kararla birlikte makinece okunabilir bir **gerekçe kodu**
  (`reason`) döndürür (`backend/main.py`, `REASON_MESSAGES`). Profil katmanı
  açıkken:
  - Her karar iç gerekçesiyle birlikte `decision_audit` tablosuna yazılır ve 90 gün
    saklanır.
  - Bir insan incelemeci kanıtı `GET /api/profile/review/{session_id}` üzerinden,
    ayrı bir operatör anahtarıyla görebilir. Her erişim kayda geçer.
- Bilinçli bir sınırlama: SHAP, skorlanan istemciye (`/api/analyze` yanıtında)
  varsayılan olarak **gönderilmez** (`SHAP_IN_ANALYZE=0`). "Beni hangi özellik
  yakaladı?" bilgisi saldırgana bir ayar sinyali verir. SOC panosu açıklamayı
  `X-Dashboard-Key` başlığıyla korunan `/api/score/{id}` uç noktasından okur. Aynı
  nedenle `/api/decision`, dört iç gerekçeyi istemciye tek bir `step_up` olarak
  bildirir.

**Önerilen metin (§3 Yöntem)**

> Her davranış akışının risk skoru SHAP ile açıklanır: Random Forest kararına en
> çok katkı veren üç öznitelik güvenlik operasyon (SOC) panosunda gösterilir. Karar
> katmanının kendi adımları (oturum boyunca yumuşatma, ardışık kanıt testi, ek
> doğrulama ve müşteri profili) SHAP ile değil, her kararla birlikte kaydedilen
> gerekçe kodlarıyla açıklanır. Müşteri profili kararlarında, müşterinin kendi
> geçmişinden en çok sapan üç öznitelik de ayrıca gösterilir. Açıklamalar yalnızca
> yetkili operatöre gösterilir, skorlanan istemciye gönderilmez; çünkü bu bilgi
> saldırgan için bir ayar sinyali olur.

**Rakip tablosu hücresi (DeepCheck):** "Akış skorunda SHAP (en etkili 3 öznitelik);
karar adımlarında gerekçe kodu".

Kaynakçadaki [4] (Industry Today) SHAP'ın kaynağı değil. SHAP kaynakçada 9. sırada.

---

## 6. "Yanlış hesap dondurma vakalarını %40 azaltmayı vaat"

**Raporda ne yazıyor**

- s. 5 (§4): "yanlış hesap dondurma vakalarını %40 azaltmayı vaat etmektedir [6]".
- s. 4 (§3), eşik tablosu: "60-80 ... Face ID, SMS doğrulama kodu veya iki faktörlü
  kimlik doğrulama" ve "80-100 ... ilgili kullanıcı hesabı geçici olarak askıya
  alınır veya işlem durdurulur".
- s. 7 risk tablosu (Yanlış Pozitifler): "Hibrit model (ML + Biyometri), sürekli
  eğitim ve geri bildirim döngüsü."

**Bugün doğru olan**

- %40 için hiçbir ölçüm, simülasyon ya da hesap yok. [6] (Statista, "Online
  Payment Fraud") DeepCheck'in kendi performansına kaynak olamaz. Bunu ölçmek için
  bir kurumun gerçek hesap dondurma oranı (başlangıç çizgisi) ve karşılaştırmalı
  bir pilot gerekir. İkisi de henüz yok.
- **Ürün hesap dondurmuyor.** En sert eylem tek bir işlemi reddetmek: skor 80 ve
  üzerindeyse `block` kararı verilir (`backend/main.py`, `ACTION_MESSAGES`:
  "Islem Reddedildi"). Müşteri profili katmanı hiç bloklamaz, yalnızca ek doğrulama
  ister. Bunu `backend/test_profiles.py` içindeki
  `test_profile_layer_never_blocks_and_never_changes_the_score` testi 1.320
  kombinasyonda doğruluyor.
- **Ek doğrulama kanalı kurumun kendi kanalıdır** (SMS, 3-D Secure vb.). Demoda bu
  kanalın yerine sabit bir kod kullanılıyor. Face ID entegrasyonu yok.
- **"Sürekli eğitim" yok.** Model elle yeniden eğitiliyor
  (`python train_model.py`). Geri bildirim uç noktası (`POST /api/outcome`: ödemeyi
  onaylama ya da itiraz) yalnızca müşteri profili katmanını besliyor, modeli
  beslemiyor.
- Yanlış reddi azaltmaya yönelik ölçülmüş tek veriler simülasyondan geliyor:
  - LSTM skordan çıkarıldığında yavaş yazan simüle kullanıcıların doğrudan
    bloklanma oranı %5,2'den %0,2'ye indi. Buna karşılık tipik kullanıcıların
    doğrudan onaylanma oranı %95'ten %89,4'e düştü, kalanlardan ek doğrulama
    istendi. Ölçüm stil başına 500 oturumla, `benchmark.py` form doldurma üreteciyle
    yapıldı (`TECHNICAL_GUIDE.md` §7, `docs/juri-cevaplari.md`).
  - Yalnızca klavye kullananlar dahil beş meşru etkileşim stilinde, sentetik
    oturumlarda bloklama oranı %0 çıktı (%95 güven aralığı 0–6,0; stil başına 60
    oturum; `docs/evaluation.md`).

  Bunlar gerçek müşteri oranları değil.

**Önerilen metin (§4 İş Modeli):** %40 cümlesi çıkarılmalı. Yerine:

> DeepCheck hesap dondurmaz. Tasarımdaki en sert eylem tek bir işlemin reddidir ve
> yalnızca çok yüksek risk skorunda uygulanır; belirsiz durumlarda müşteri
> reddedilmez, ek doğrulamaya yönlendirilir. Bu yaklaşımın yanlış red ve dondurma
> vakalarını ne kadar azalttığı henüz ölçülmemiştir; pilot çalışmada, kurumun mevcut
> yanlış red oranı başlangıç çizgisi alınarak ölçülecektir.

**Önerilen metin (§3 eşik tablosu)**

> 0–40 "Gerçek Kullanıcı": müdahale yapılmaz. 40–60 "Şüpheli": kanıt yeterliyse
> işlem uyarıyla geçer, ardışık test karar veremiyorsa ek doğrulama istenir. 60–80
> "Yüksek Risk": ek doğrulama istenir; doğrulama kanalı (SMS, 3-D Secure vb.)
> kurumun kendi kanalıdır. 80–100 "Bot Tespit Edildi": işlem reddedilir, hesap
> askıya alınmaz. Skor alınamazsa karar her zaman ek doğrulamadır, hiçbir zaman
> onay değildir.

**Risk tablosu hücresi (Yanlış Pozitifler)**

> Belirsizlik durumunda ret yerine ek doğrulama uygulanır. Yalnızca 80 ve üzeri skor
> işlemi reddeder, müşteri profili hiçbir zaman reddetmez. Yanlış bloklama oranı
> tek bir genel oran olarak değil, etkileşim stiline göre (yalnızca klavye
> kullananlar dahil) ayrı ayrı ölçülür.

---

## 7. "IP, cookie veya keystore tutmadan sadece X-Y koordinat analizi"

**Raporda ne yazıyor**

- s. 7 risk tablosu (Veri Gizliliği): "Anonim veri kullanımı; IP, cookie veya
  keystore tutmadan sadece X-Y koordinat analizi."
- s. 6 (§5): "DeepCheck AI, içerik verisi toplayarak ..." Bu cümle gerçeğin tersini
  söylüyor: SDK içerik toplamıyor.

**Bugün doğru olan: SDK ne topluyor** (`sdk/deepcheck.js`)

| Kanal | Gönderilen | Not |
|---|---|---|
| İşaretçi hareketi (`pointermove`, yoksa `mousemove`) | x, y, zaman | Yalnızca birincil işaretçi |
| Tıklama | x, y, zaman | |
| Kaydırma | Sayfanın kaydırma konumu (`scrollY`), zaman | |
| Klavye (`keydown`) | **Yalnızca zaman damgası** | Basılan tuş (`e.key`, `e.code`) ve alan değeri hiçbir zaman okunmaz |
| Duraksama | 400 ms ve üzerindeki boşlukların süresi | |
| Odak | Sekmenin gizlendiği anların zaman damgası | |
| Köken sayaçları | `isTrusted=false` olay sayısı, `navigator.webdriver`, işaretçi türü sayıları (fare/kalem/dokunma) | Saklanır ama skora girmez |
| Oturum açılışı | İş ispatı (proof-of-work) çözümü, saat çözünürlüğü ve zamanlayıcı gecikmesi ölçümü | Oturum anahtarı (token) almak için |
| Her gönderim | Gönderenin saat değeri (`client_sent_at`) | Tekrar oynatma ve saat tutarlılığı kontrolü için |

SDK her 2 saniyede son 10 saniyelik pencereyi gönderir.

- **Toplanmayanlar:** tuş içeriği, form alanı değerleri ve sayfa içeriği (DOM). SDK
  çerez ya da localStorage kullanmıyor ve tarayıcı parmak izi (canvas, font vb.)
  çıkarmıyor. Veritabanında IP adresi ya da User-Agent sütunu yok
  (`backend/models.py`).
- **"IP tutmadan" ifadesinin sınırı.** Arka uç istemcinin IP adresini yalnızca hız
  sınırlaması için bellekte kullanıyor ve veritabanına yazmıyor (`backend/main.py`,
  `_client_ip`). Ancak varsayılan web sunucusu erişim günlükleri istemci IP'sini
  konteyner günlüklerine yazıyor:
  - Uvicorn'un erişim günlüğü açık; `backend/entrypoint.sh`'de `--no-access-log`
    yok.
  - Ön yüzdeki nginx'in varsayılan günlüğü de açık.

  Bu günlükler kapatılmadan ya da bir saklama süresine bağlanmadan "IP tutmuyoruz"
  cümlesi doğru olmaz.
- **"Anonim" ifadesi yanlış.** Oturum telemetrisi rastgele bir oturum kimliğine
  bağlı, ama entegre eden kurum bu kimliği kendi müşterisiyle eşleştirebilir. Bu
  yüzden veri anonim değil, en fazla takma adlı (pseudonymous). Müşteri profilinde
  durum daha da açık: bir kişinin işaretçi ve tuş zamanlaması davranışını kimliğe
  bağlı olarak saklamak **biyometrik veri** sayılır (GDPR md. 4(14); KVKK md. 6'ya
  göre özel nitelikli kişisel veri). Takma adlandırma bunu değiştirmez (GDPR Gerekçe
  26). Bu değerlendirme müşteri profili tasarım belgesinin §9.1'inde yazılı ve kod
  buna göre kuruldu.

**Bugünkü KVKK duruşu: kodda olanlar**

- **Saklama süreleri.** Ham telemetri 1 saat sonra boşaltılıyor, satırlar 24 saat
  sonra siliniyor (`backend/main.py`, varsayılanlar `RAW_RETENTION_HOURS=1` ve
  `ROW_RETENTION_HOURS=24`).
- **Varsayılan olarak kapalı.** Müşteri profili katmanı `PROFILE_LAYER=0` ile gelir.
  Açmak için ayrıca özel bir profil anahtarı ve kurum kimlik bilgileri gerekir.
  Geliştirme ortamı için bile yedek bir değer tanımlı değil.
- **Rıza olmadan profil yok.** Profil yalnızca kurumun `POST /api/profile/consent`
  çağrısıyla, kaydedilen bir hukuki dayanakla oluşur. Rıza yoksa profil kaydı da
  yoktur, katman hiçbir şey yapmaz ve reddetmek müşteriye hiçbir bedel ödetmez.
  Meşru menfaat dayanak olarak bilinçli şekilde kabul edilmiyor.
- **Takma ad.** Profil kimliği = HMAC-SHA256(ayrı bir profil anahtarı, kurum kimliği
  ve müşteri referansı) (`backend/profiles.py`, `derive_profile_id`).
  - Ham müşteri referansı saklanmaz, günlüğe yazılmaz ve hiçbir yanıtta dönmez.
  - Her kurumun ayrı ad alanı var, yani profiller kurumlar arasında eşleştirilemez.
- **Profilde ham telemetri yok.** Giriş türü başına en fazla 20 referans ve 4 onay
  bekleyen oturum vektörü saklanır. Her vektör 12 normalize sayıdan oluşur.
- **Profil kayıtlarının saklanması.** Profil 180 gün hareketsiz kalırsa silinir.
  Karar denetim kaydı 90 gün, profil erişim kaydı 365 gün saklanır
  (`backend/main.py`).
- **Silme ve itiraz.** `POST /api/profile/erase` uç noktası `erase` (silme) ve
  `object` (itiraz) modlarını destekler. İtiraz kaydı sonraki rıza denemelerini
  engeller (409).
- **İnsan incelemesi.** `GET /api/profile/review/{session_id}` ayrı bir operatör
  anahtarıyla çalışır ve her erişim kaydedilir.
- **Hiçbir zaman bloklamaz.** Katman yalnızca ek doğrulama ister. Bu, GDPR md.
  22(4)'ün özel nitelikli veriye dayalı tamamen otomatik karar yasağına da uygun.

**Henüz olmayanlar (raporda iddia edilmemeli)**

- **Hukuki metinler.** KVKK aydınlatma metni (`docs/kvkk-aydinlatma.md`) ve veri
  koruma etki değerlendirmesi (`docs/dpia.md`) tasarımda öngörülüyor ama bu dosya
  yazılırken depoda yoktu. Etki değerlendirmesi herhangi bir pilotun ön koşulu.
- **Şifreleme.** Dinlenmedeki veri için yalnızca disk (volume) düzeyinde şifreleme
  öngörülüyor; bu belgelenmiş bir karar ve sütun düzeyinde şifreleme yok. İletimde
  TLS'i, sistemi dağıtan kurumun ters vekil sunucusu sağlamalı; Docker Compose
  kurulumu düz HTTP ile çalışıyor. Bu yüzden risk tablosundaki "uçtan uca
  şifreleme" ifadesi doğru değil.
- **Pano erişimi.** SOC pano anahtarı paylaşılan bir sır; analist bazında kimlik yok
  (`TECHNICAL_GUIDE.md` §15).
- **Hukuki dayanak değerleri.** Kod iki dayanak kabul ediyor: `explicit_consent` ve
  `contract_necessity` (`backend/main.py`, `PROFILE_CONSENT_BASES`). Özel nitelikli
  veride "sözleşmenin ifası", GDPR md. 9(2)'de ve KVKK md. 6'da sayılan istisnalar
  arasında yok. Raporda yalnızca **açık rıza** yazılmalı; `contract_necessity`
  değeri hukuk incelemesine sunulmalı.

**Önerilen metin (risk tablosu, Veri Gizliliği)**

> Tuş içeriği, form alanları ve sayfa içeriği hiçbir zaman toplanmaz; SDK yalnızca
> işaretçi koordinatlarını, tıklama ve kaydırma olaylarını, tuşa basma zamanlarını
> ve sekme odak zamanlarını gönderir. Ham davranış verisi 1 saat içinde boşaltılır,
> 24 saat içinde silinir; veritabanında IP adresi tutulmaz. Müşteri profili yalnızca
> açık rıza ile oluşturulur, takma adlı (HMAC) bir kimlikle saklanır, ham veri
> içermez ve silme ya da itiraz yoluyla kaldırılabilir. Kişiye bağlı davranış profili
> KVKK md. 6 kapsamında özel nitelikli (biyometrik) veri olarak ele alınır; pilot
> öncesinde veri koruma etki değerlendirmesi yapılacaktır.

Bu metni kullanmadan önce web sunucusu erişim günlükleri kapatılmalı ya da metne
"erişim günlükleri N gün saklanır" eklenmeli.

**§5'teki cümle:** "içerik verisi toplayarak" ifadesi "içerik verisi toplamadan"
olarak düzeltilmeli.

---

## 8. SOC ekran görüntüleri

**Raporda ne var**

Raporun 5. sayfasındaki iki ekran görüntüsü. Başlıkları "DEEPCHECK // SİBER
GÜVENLİK (SOC) MERKEZİ", adres çubuğunda `localhost:8081` yazıyor. Görünen öğeler:

- **Üst kartlar:** "Canlı Trafik 4,205 istek/sn", "Engellenen Tehdit 842 bloke/sa",
  "Ort. Dikkat Skoru 78/100", "Yapay Zeka Gecikmesi 24 ms".
- **İstek listesi:** her istek için bir IP adresi.
- **"Kimlik & Çevre Verisi" bölümü:** Coğrafi Konum (Bakü, AZ), Cihaz/OS (Bilinmeyen
  Linux (Headless)), Tarayıcı (Chromium (Puppeteer)), ISP/ASN (Cloud Datacenter ASN
  1492).
- **Diğer alanlar:** "AI Güven Skoru (Confidence): %98.8" ve ham sensör JSON'u
  (`is_bot_prob`, `fingerprint`).
- **Düğmeler:** "IP'yi Kara Listeye Al" ve "Oturumu Sonlandır".

**Bugün doğru olan** (`frontend/src/pages/Dashboard.jsx`)

- Pano `http://localhost:3000/dashboard` adresinde çalışıyor ve açılışta erişim
  anahtarı istiyor.
- Panoda şunlar var:
  - dört metrik kartı: Toplam Oturum, Ortalama Risk Skoru, Tespit Edilen Bot,
    Ortalama Yanıt Süresi;
  - renkli etiketli oturum listesi;
  - seçili oturum için risk rozeti, "Son pencere model kesinliği" ve yanıt süresi;
  - "En Etkili 3 Özellik (SHAP)" çubukları;
  - "Müşteri Profili" kartı;
  - D3.js ile çizilen "Risk Skoru Geçmişi" grafiği.
- Görüntülerdeki şu öğelerin **hiçbiri kodda yok**: coğrafi konum, ISP/ASN,
  cihaz/işletim sistemi, tarayıcı adı, IP adresi, ham sensör JSON'u, "IP'yi kara
  listeye al" ve "oturumu sonlandır" düğmeleri, saniyedeki istek sayacı.
- Bu öğeler tasarım gereği de olamaz:
  - Veritabanında IP ve User-Agent tutulmuyor (bkz. bölüm 7).
  - Puppeteer ya da headless tarayıcı tespiti yapılmıyor. `navigator.webdriver`
    toplanıyor ama skora girmiyor ve kolayca gizlenebiliyor.
  - Playwright ve Puppeteer'ın ürettiği olaylar `isTrusted=true` olduğundan köken
    sayacı sürülen tarayıcıyı yakalamıyor (`sdk/deepcheck.js`,
    `TECHNICAL_GUIDE.md` §4.1).
- Panoda müdahale düğmesi yok. Pano izleme amaçlı; karar otomatik olarak
  `POST /api/decision` uç noktasında veriliyor.
- Görüntülerdeki sayılar (4.205 istek/sn, 842 bloke/sa, 24 ms, %98,8) ölçülmemiş
  örnek değerler.
- Görüntüdeki "Dikkat Skoru"nun yönü bugünkü sistemin tersi: bot 12/100 almış, yani
  yüksek değer insanı gösteriyor. Bugünkü sistem risk skoru kullanıyor; yüksek değer
  riskli demek ve bot 80 ve üzeri alıyor.

**Önerilen işlem**

İki görüntüyü kaldırın ve bugünkü panodan yeni görüntü alın:

1. `docker-compose up --build` ile sistemi başlatın.
2. `/demo` sayfasında bir ödeme yapın.
3. `/dashboard` sayfasını açıp görüntüyü alın.

Sentetik demo müşterileri görüntüde sentetik olarak işaretli görünmeli.

**Önerilen resim altı**

> Şekil X. DeepCheck SOC panosu (çalışan prototip): oturum listesi, seçili oturumun
> risk skoru ve skor geçmişi, akış skorunun SHAP açıklaması (en etkili üç öznitelik)
> ve müşteri profili kartı. Görüntüdeki oturumlar demo sayfasında üretilmiştir;
> müşteri profilleri sentetiktir ve gerçek müşteri verisi içermez.

**Rakip tablosu, "Canlı Müdahale (SOC Paneli)" satırı (DeepCheck):** "Canlı izleme
panosu (3 saniyede bir yenilenir); müdahale kararı otomatik olarak sunucu tarafında
verilir".

---

## 9. Rakip analizi: eksik doğrudan oyuncular

**Raporda ne yazıyor**

s. 6 (§5) şu oyuncuları anıyor: Riskified, Forter, Payfix, iyzico, Google
Analytics, Mixpanel, Hotjar, BioCatch, ThreatMetrix ve Sardine.

- Tablodaki bazı hücrelerin kaynağı yok. Örneğin BioCatch/Sardine için "Derin
  öğrenme (kapalı kaynak)", açıklanabilirlik için "Sınırlı", maliyet için "Yüksek
  maliyet, karmaşık kurulum".
- "Mevcut sistemlerin derinlemesine analiz yapma kabiliyetine sahip olmadığı"
  iddiası, aşağıdaki oyuncular düşünüldüğünde savunulamaz.
- Doğrudan bot tespiti yapan oyuncular listede hiç yok.

**Eksik doğrudan rakipler**

Aşağıdaki bilgiler yalnızca şirketlerin kamuya açık belgelerinden alındı ve hiçbir
rakam uydurulmadı. 2026-09-19'da kontrol edildi; teslimden önce ürün sayfalarından
yeniden kontrol edilmeli.

| Oyuncu | Ne yapar (kamuya açık belgelere göre) | Kaynak |
|---|---|---|
| **Cloudflare Bot Management** | Her isteğe 1–99 arası bir bot skoru verir (1 bot, 99 insan). Sezgisel kurallar, makine öğrenimi (tespitlerin çoğu) ve headless tarayıcıları yakalayan JavaScript tespitleri gibi motorlar kullanır. Sitenin önünde, Cloudflare ağında çalışır. | [bot skoru](https://developers.cloudflare.com/bots/concepts/bot-score/), [tespit motorları](https://developers.cloudflare.com/bots/concepts/bot-detection-engines/) |
| **DataDome** | Her isteği sunucu tarafı bir modülle gerçek zamanlı değerlendirir. İstemci tarafındaki JavaScript etiketi fare hareketi ve tuş vuruşu gibi davranış verisi ile tarayıcı ve cihaz bilgisi toplar; gerektiğinde CAPTCHA ya da Device Check sınaması gösterir. Algılama betiğini periyodik olarak yeniden derleyerek tersine mühendisliği zorlaştırır. | [JavaScript etiketi](https://docs.datadome.co/docs/javascript-tag), [betik yenileme](https://datadome.co/bot-management-protection/dynamic-bot-detection-script-rebuilds/) |
| **HUMAN Security** | Eski adı White Ops. Temmuz 2022'de PerimeterX ile birleşti. Bot saldırılarına, hesap ele geçirme ve hesap kötüye kullanımına, reklam dolandırıcılığına karşı ürünlerini tek platformda sunar. | [birleşme duyurusu](https://www.humansecurity.com/newsroom/human-and-perimeterx-unite-in-market-changing-merger-to-safeguard-customers-from-sophisticated-bot-attacks-fraud-and-account-abuse/) |
| **Kasada** | Kullanıcının görmediği istemci tarafı sorgulama, asimetrik iş ispatı (proof-of-work) sınaması ve sürekli değişen (polimorfik), gizlenmiş kodla otomasyonu pahalı hâle getirir. CAPTCHA göstermemeyi hedefler. | [Bot Defense](https://www.kasada.io/bot-defense), [ürün](https://www.kasada.io/product/) |
| **reCAPTCHA Enterprise** (Google Cloud) | Her etkileşim için 0,0–1,0 arası bir skor döndürür (1,0 düşük risk demek). Faturalandırması açık projelere gerekçe kodları verir. Site değerlendirmeleri LEGITIMATE/FRAUDULENT olarak işaretleyerek (annotate) modele geri bildirim gönderir. Güncel belgeler "Google Cloud Fraud Defense" başlığı altında yayımlanıyor. | [skor yorumlama](https://docs.cloud.google.com/recaptcha/docs/interpret-assessment-website), [annotate](https://docs.cloud.google.com/recaptcha/docs/annotate-assessment) |
| **NuData Security** (Mastercard) | Mastercard 2017'de satın aldı. NuDetect ürünü, pasif biyometri ve davranış analitiğiyle kullanıcıyı çevrimiçi etkileşimlerinden tanımayı hedefler. Mastercard'ın dolandırıcılık ve güvenlik ürünlerine entegre edildi. | [Mastercard duyurusu](https://investor.mastercard.com/investor-news/investor-news-details/2017/Mastercard-Enhances-Security-of-the-Internet-of-Things-with-the-Acquisition-of-NuData-Security-Inc/default.aspx) |
| **BehavioSec** (LexisNexis Risk Solutions) | İsveç kökenli. 3 Mayıs 2022'de LexisNexis Risk Solutions tarafından satın alındı. Davranış analiziyle sürekli kimlik doğrulama yapar, mobil dokunmatik ekran ve sensör sinyallerini de işler. LexisNexis ThreatMetrix'in tarayıcı tabanlı çözümlerini tamamlar. | [LexisNexis duyurusu](https://risk.lexisnexis.com/about-us/press-room/press-release/20220503-behaviosec) |
| **BioCatch** | Bankalara yönelik davranışsal biyometri platformu. Hesap açma dolandırıcılığını, hesap ele geçirmeyi (botlar ve uzaktan erişim araçları dahil), sosyal mühendislik dolandırıcılığını ve para katırı hesaplarını tespit etmeyi hedefler. | [BioCatch davranışsal biyometri](https://www.biocatch.com/behavioral-biometrics) |

**Önerilen konumlandırma metni (§5)**

> Bot tespiti ve davranışsal biyometri alanında olgun ticari çözümler vardır.
> Cloudflare Bot Management, DataDome, HUMAN Security, Kasada ve reCAPTCHA Enterprise
> bot trafiğini; BioCatch, NuData (Mastercard) ve BehavioSec (LexisNexis Risk
> Solutions) ise kullanıcının davranışsal biyometrisini hedefler. Bu oyuncuların asıl
> üstünlüğü model değil, çok sayıda müşteriden gelen veri ölçeği ve ağdaki
> konumlarıdır; hiçbiri yalnızca davranış skoruna dayanmaz. DeepCheck bu ölçekte
> rekabet iddiasında değildir. DeepCheck'in farkı şunlardır: kurumun kendi ağında
> çalıştırılabilmesi, kararın tek bir sunucu uç noktasında verilmesi, her model
> tercihinin depoda yeniden üretilebilir bir ölçümle gerekçelendirilmesi ve müşteri
> geçmişinin onay ya da ret için değil, yalnızca ek doğrulama istemek için
> kullanılması. Müşteriyi kendi geçmiş davranışıyla karşılaştırma fikri yeni
> değildir; BehavioSec ve BioCatch da bunu yapar. DeepCheck'in tercihi, bu
> karşılaştırmanın yalnızca ek doğrulama isteyebilecek biçimde sınırlanmasıdır.

**Tablo için öneriler**

- Kaynağı olmayan hücreleri ("Sınırlı", "Kısmi", "Yüksek maliyet", "Derin öğrenme
  (kapalı kaynak)") ya kaynaklandırın ya da çıkarın.
- Yeni bir satır ekleyin: "Veri ölçeği / ağ konumu". Bu satırda DeepCheck için
  dürüst cevap "tek kurum, gerçek veri henüz yok".

---

## Ek tutarsızlıklar (kısa)

| # | Raporda | Bugün doğru olan | Öneri |
|---|---|---|---|
| 1 | s. 4: beş öznitelik sayılıyor (scroll hızı varyansı, tereddüt skoru, etkileşim entropisi, ivme değişimi, tıklama yoğunluğu) | 12 öznitelik var (`backend/lstm_model.py`, `FEATURE_NAMES`): bu beşi ve odak değişimi, ayrıca hız otokorelasyonu, yön tutarlılığı, zaman kuantasyonu, duraklama dağılımı, tıklama öncesi hareket ve kanal geçiş gecikmesi | 12 özniteliği ve ikinci altılının amacını yazın: gürültü eklemekle taklit edilemeyen hareket yapısını ölçmek |
| 2 | s. 4: "Bu öznitelikler, her kullanıcı için benzersiz bir davranışsal parmak izi oluşturur." | Ölçüm bunu desteklemiyor. Sentetik kimliklerde farklı bir kişi, müşterinin olgun profiline karşı fare kullanıcılarında %47,5, klavye kullanıcılarında %26,4 oranında ek doğrulamaya gönderildi (`docs/profile-evaluation.md` §5). Kalan oturumlarda ek doğrulama istenmedi. | "Benzersiz parmak izi" ifadesini çıkarın, yerine ölçülen ayrışmayı sentetik etiketiyle yazın |
| 3 | s. 3 ve s. 6: kullanıcının işlemi "bilinçli" yapıp yapmadığı, "niyet" ve "dikkat kalitesi" ölçülüyor | Model bot ile insanı ayırmak için eğitildi. Dolandırıcının yönlendirmesiyle kendi ödemesini yapan gerçek bir kişiyi ayırt etmeye yönelik hiçbir eğitim verisi ya da ölçüm yok | "Niyet ve dikkat ölçümü" yerine "otomasyon ile insan davranışını ayırt etme" yazın |
| 4 | s. 4 ve s. 5: "100KB altı optimize JavaScript SDK" | Doğru, üstelik daha güçlü söylenebilir: `sdk/deepcheck.js` küçültülmemiş hâliyle 33.914 bayt, gzip ile 12.208 bayt ve hiçbir bağımlılığı yok (2026-09-19 dosya boyutu ölçümü) | "Bağımlılıksız, küçültülmeden 34 KB (gzip ile 12 KB)" |
| 5 | s. 5: "saatler içinde entegre edilebilmektedir"; s. 6: "çoklu platform entegrasyonu" | Entegrasyon süresi ölçülmedi. Adımlar belli: script etiketi, `DeepCheck.init` çağrısı ve kurumun kendi sunucusundan `/api/decision` çağrısı (`README.md`, Entegrasyon bölümü; ayrıntılı rehber `docs/entegrasyon.md` bu çalışmada yazılıyor). Yerel (native) Android/iOS SDK'sı yok. Model dokunmatik ekran verisiyle hiç eğitilmedi. | Süre yerine adımları yazın. Mobil uygulamalar için "WebView içinde aynı SDK; yerel SDK yol haritasında" yazın |
| 6 | s. 7 risk tablosu: "Güvenli API mimarisi, uçtan uca şifreleme ve dâhili anomali tespiti" | Kodda olanlar: HMAC-SHA256 imzalı oturum anahtarı (token), iş ispatı, telemetri tekrar oynatma kontrolleri, hız sınırları (`backend/main.py`). "Uçtan uca şifreleme" kodda yok (bkz. bölüm 7). "Dâhili anomali tespiti" yapan Isolation Forest skordan çıkarıldı. | Kodda olanları yazın. İş ispatının istemcinin tarayıcı olduğunu kanıtlamadığını da ekleyin: tarayıcı süren bir bot bu kontrolü dürüstçe geçer (`sdk/deepcheck.js` içindeki yorum) |
| 7 | s. 7 takvim: "Temmuz: 50.000 satırlık veri seti ... hibrit model (RF, Isolation Forest)"; "Ağustos: ... SOC dashboard (50ms yanıt süreli)" | Bkz. bölüm 2, 3 ve 4 | Takvimi yapılan işe göre güncelleyin (Temmuz satırının önerisi bölüm 3'te) |
| 8 | Kaynakça numaraları metinle eşleşmiyor | Metindeki [2] (PwC) listede 2 numaralı AWS'yi, [3] (%30 terk) IMARC Türkiye fintech raporunu, [4] (SHAP) Industry Today'i, [5] (Türkiye fintech pazarı 2,2 milyar $) PwC'yi, [6] (%98 ve %40) Statista'yı, [7] (4,8 milyar $ pazar) Biometric Update'i, [8] (Türkiye'de 35.000 şirket ve %22) scikit-learn RandomForest belgesini gösteriyor | Numaraları düzeltin. Pazar rakamlarının (48 milyar $, 4,8 milyar $, %22, 35.000 şirket, %30 terk) kaynakları bu dosyada doğrulanmadı; her biri asıl kaynağından kontrol edilmeli, kaynağı bulunamayan çıkarılmalı |
| 9 | Dil | s. 4 ve s. 5'te Azerbaycan Türkçesi biçimleri var: "siqnallarını", "hem də ... üçün", "Random Forest və Isolation Forest ... örüntülərində yüksək", "olaraq", "niyə 'şüpheli' olaraq ... şəkildə analiz edilə bilir". s. 7'deki risk tablosu başlıkları İngilizce ve "(Yığcam)" yazıyor. s. 3'te "48 milyar ..." ile başlayan cümle kopuk görünüyor (metin çıkarımında cümle bölünmüş, PDF'te kontrol edin). Ayrıca yazım hataları var: "yüzysel", "yüzeyel", "kalüplerini", "hazırlanmasıç". | Türkiye Türkçesine çevirin; kopuk cümleyi tamamlayın |

---

## Final raporun bugün dürüstçe iddia edebilecekleri

Her satır bir ölçüme ya da doğrudan kod ve teste dayanıyor. "Neyin üzerinde"
sütunu raporda da sayının yanında yazılmalı.

| # | İddia | Ölçüm / kanıt | Dosya | Neyin üzerinde |
|---|---|---|---|---|
| 1 | Uçtan uca çalışan bir prototip var: tarayıcı SDK'sı, FastAPI, PostgreSQL ve SOC panosu tek komutla ayağa kalkıyor | `docker-compose up --build` | `docker-compose.yml`, `README.md` | Kod |
| 2 | Karar sunucuda ve tek bir noktada veriliyor; skor yoksa hiçbir zaman onay verilmiyor | `/api/decision`; skor yoksa ya da sonlu değilse `get_action` sonucu `verify` | `backend/main.py`, `backend/test_scorer.py` | Kod ve test |
| 3 | Oturum kimliği ve imzalı oturum anahtarı sunucudan geliyor, iş ispatı karşılığında veriliyor; telemetri tekrar oynatması reddediliyor | HMAC-SHA256 oturum anahtarı, iş ispatı, yük özeti, zaman sırası ve saat ofseti kontrolleri | `backend/main.py`, `sdk/deepcheck.js` | Kod. Sınırı: iş ispatı istemcinin tarayıcı olduğunu kanıtlamaz |
| 4 | Betikli tarayıcı koşularında betikli insan akışlarında yanlış işaret %0, bot akışlarında yakalama %76,1 | Eğitimde görülmeyen koşular, 82 akış | `docs/evaluation.md` | Betikli Playwright koşuları; **yeniden ölçülüyor** |
| 5 | Bağımsız saldırgan düzeneğinde düz çizgi otomasyonu %100 bloklandı, insan persona %100 onaylandı; sınırı da ölçüldü: insanlaştırılmış bot durdurulamadı | Sınıf başına 12 oturum | `docs/evaluation.md` | Bağımsız yazılmış simülasyon |
| 6 | Model seçimi ölçüme dayanıyor: RF, 6 başka model ailesiyle karşılaştırıldı; LightGBM görülmemiş insanların %74'ünü blokladı, RF %0 | 7 aile, 4 protokol | `backend/model_selection.py`, `backend/scorer.py`, `docs/juri-cevaplari.md` | Sentetik veri ve betikli tarayıcı verisi |
| 7 | Isolation Forest ve LSTM ölçüm sonucunda skordan çıkarıldı | IF ROC-AUC 0,340; LSTM ile 60 ve üzeri bot oranı 0,90'dan 0,79'a düşüyordu | `backend/scorer.py` | Betikli tarayıcı verisi |
| 8 | Oturum ortasındaki devir-teslim ilk otomatik akışta yakalanıyor | 185 devir-teslimin 185'i yakalandı; 14.790 meşru akıştan en fazla 6'sı etkilendi | `backend/scorer.py` | Simülasyon |
| 9 | Beş meşru etkileşim stilinde (yalnızca klavye kullananlar dahil) sentetik bloklama %0 | %95 güven aralığı 0–6,0, stil başına n=60 | `docs/evaluation.md`, `backend/benchmark.py` | Sentetik |
| 10 | Skorlama 17,7 ms; karar uç noktası konteynerde p95 8,3 ms (profil kapalı), en fazla 43,2 ms (profil açık) | Bkz. bölüm 4 | `backend/scorer.py`, `docs/profile-evaluation.md` §11 | Ağ hariç; geçici PostgreSQL, sentetik oturumlar |
| 11 | SDK 34 KB (gzip ile 12 KB) ve bağımlılıksız | Dosya boyutu | `sdk/deepcheck.js` | 2026-09-19 |
| 12 | Tuş içeriği hiçbir zaman okunmuyor; ham telemetri 1 saatte boşaltılıyor, 24 saatte siliniyor; veritabanında IP ve User-Agent yok | `onKeyDown` yalnızca zaman damgası kaydeder; saklama varsayılanları | `sdk/deepcheck.js`, `backend/main.py`, `backend/models.py` | Kod (erişim günlüğü notuyla, bölüm 7) |
| 13 | Açıklanabilirlik: akış başına SHAP ile en etkili 3 öznitelik, karar gerekçe kodları, profil kararlarında en çok sapan öznitelikler; açıklama skorlanan istemciye gönderilmiyor | SOC panosu | `backend/scorer.py`, `backend/main.py`, `frontend/src/pages/Dashboard.jsx`, `frontend/src/components/ProfilePanel.jsx` | Kod |
| 14 | Müşteri profili yalnızca ek doğrulama istiyor; hiçbir zaman bloklamıyor ve skoru değiştirmiyor | 1.320 kombinasyonluk test | `backend/test_profiles.py` (`test_profile_layer_never_blocks_and_never_changes_the_score`) | Test |
| 15 | Müşteri profili aynı kişiye yanlışlıkla ek doğrulamayı fare kullanıcılarında %4,9, klavye kullanıcılarında %3,8 oranında sordu (**alt sınır**). Farklı bir kişiyi fare kullanıcılarında %47,5, klavye kullanıcılarında %26,4 oranında ek doğrulamaya gönderdi. | 200 sentetik kimlik | `docs/profile-evaluation.md` §5 | **Sentetik kimlikler; gerçek müşteri verisi yok.** Katman kart deneme botlarına karşı hiçbir şey yapmaz; yalnızca insan eliyle hesap ele geçirmeyi hedefler |
| 16 | Ek doğrulamayı geçen bir saldırganın oturumları profili zehirlemiyor: saldırganın 0 ile 6 arasında oturumu onay bekleyen olarak saklandığında, ek doğrulamaya gönderilme oranı her durumda %47,5 kaldı (düz eğri). Sınırı: kurum ödemeyi onaylarsa (`settled`) oturum referansa dönüşür ve koruma azalır; tek bir onaylı oturumla oran %25,5'e düştü. | Fare, alfa 0,05 | `docs/profile-evaluation.md` §7 | Sentetik kimlikler |
| 17 | KVKK'ya göre tasarlandı: rıza olmadan profil yok, HMAC takma ad, silme ve itiraz, insan incelemesi, tanımlı saklama süreleri | Bkz. bölüm 7 | `backend/main.py`, `backend/profiles.py` | Kod. Aydınlatma metni ve etki değerlendirmesi henüz yok |
| 18 | Ekip kendi sınırlarını ölçüp yazdı: bağımsız insanlaştırılmış bot durdurulamadı, profil katmanı kart denemesine karşı etkisiz, gerçek kişi ölçülmedi | Bölüm 1, 2 ve 15. satır | `docs/evaluation.md`, `docs/profile-evaluation.md` | Jüriye önce biz söyleyelim |

---

## Teslimden önce yeniden kontrol edilmesi gereken sayılar

Bu dosyadaki bazı sayılar bu çalışmanın içinde değişiyor ya da henüz kesinleşmedi.
Rapor teslim edilmeden önce aşağıdakiler, adı geçen dosyanın o günkü hâliyle
karşılaştırılmalı:

- **Model doğruluğu.** Karar katmanının ardışık testi ve konformal koruması bu
  çalışmada değiştiriliyor. Laboratuvar verisindeki öznitelik doygunluğu (bölüm 1)
  yüzünden tarayıcı verisinin yeniden kaydedilmesi ve modelin yeniden eğitilmesi
  planlanıyor; bu kapsamda yapılmadı. Kontrol edilecek sayılar:
  - `docs/evaluation.md`: %76,1 ve %0;
  - `backend/scorer.py`: 0,90 ve 0,63;
  - `docs/juri-cevaplari.md`: ROC-AUC 0,988 ve %74.
- **Kapasite ve maliyet.** Kâğıt üzerinde bir hesap yazılıyor (`TECHNICAL_GUIDE.md`);
  yük testi yapılmadı. Rapora girecek her kapasite sayısı "ölçümden türetilmiş
  hesap" etiketi taşımalı.
- **Gecikme.** Süreler ölçüm oturumları arasında oynuyor. Örneğin aynı yapılandırmada
  iki ölçüm arasında gölge modun p95 değeri 40,5 ms'den 37,2 ms'ye değişti
  (`docs/profile-evaluation.md` §11). Rapora son ölçüm alınmalı.
- **Eskimiş belge ifadeleri.** Başka belgelerdeki şu ifadeler bu dosya yazılırken
  günceli yansıtmıyordu ve güncelleniyor. Kontrol etmeden rapora kopyalamayın:
  - `README.md` "Performance" tablosundaki 42,4 ms (üç modelli eski topluluk);
  - `TECHNICAL_GUIDE.md` §7 ve §16'daki "42 ms";
  - `TECHNICAL_GUIDE.md` §16'daki "bir işçi ~20 oturum" ve `AUDIT.md` C-3'teki "~26
    oturum" (eski kod);
  - `TECHNICAL_GUIDE.md` §16'daki "oturum kimliği dışında tanımlayıcı yok" (müşteri
    profili katmanıyla artık doğru değil).
- Bu dosya, ölçümler kesinleştiğinde güncellenecek.

**Son kontrol listesi**

- [ ] Her sayının yanında neyin üzerinde ölçüldüğü yazıyor (sentetik / betikli
  tarayıcı / sentetik kimlik).
- [ ] Metinde şu ifadeler kalmadı: "gerçek kullanıcı verisi", "kapalı beta", "%98",
  "%40", "Transformer", "AWS Lambda", "tüm kararlar SHAP ile", "anonim",
  "benzersiz parmak izi", "hesap askıya alınır".
- [ ] Ekran görüntüleri çalışan panodan alındı ve sentetik demo müşterileri etiketli.
- [ ] Kaynakça numaraları metindeki atıflarla eşleşiyor.
- [ ] Azerbaycan Türkçesi biçimleri ve yazım hataları düzeltildi.
