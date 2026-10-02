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
> **Teslimden önce:** Kapasite sayıları hesapla türetildi ve yazıldı; model
> doğruluğu **yeniden ölçülmedi** (ayrıntısı son bölümde). Rapor teslim
> edilmeden önce her sayı, yanında adı geçen dosyayla yeniden karşılaştırılmalı.

Hazırlanma tarihi: 2026-09-19. **Son güncelleme: 2026-09-20** — profil
katmanının ölçümleri son hâlleriyle yeniden yayımlandıktan
([`profile-evaluation.md`](profile-evaluation.md), 2026-09-19 koşusu) ve karar
katmanındaki son değişiklikler (ardışık testin bot sınırı kuralı, oturum
jetonunun süresi, konformal korumanın sunulan hâlde atıl olduğunun yazılması)
indikten sonra her sayı ve her "bugün doğru olan" cümlesi koda karşı yeniden
kontrol edildi. Bu turda **hiçbir yeni ölçüm yapılmadı**: yük testi yok,
tarayıcı verisi yeniden kaydedilmedi, model yeniden eğitilmedi. Sayfa
numaraları PDF sayfa numaralarıdır. Sayılar Türkçe yazımla verilmiştir
(ondalık ayırıcı virgül); kaynak dosyalarda nokta ile geçerler.

**Sentetik veri kuralı.** Jüri prototipi, ekibin müşteri tabanı olmadığı için
sentetik demo müşterileriyle çalışır. Bu kabul edilmiş bir durumdur; pazarlık
konusu olmayan şey etikettir: her sentetik müşteri, oturum ve sayı arayüzde, SOC
panosunda, veride ve belgelerde **sentetik olarak işaretli** olmalı ve hiçbir
ölçüme girmemelidir. Final rapordaki her ekran görüntüsü ve her sayı bu kurala
uymalıdır.

---

## Özet

| # | Rapordaki iddia | Durum | Kısaca doğrusu |
|---|---|---|---|
| 1 | "%98 anomali yakalama başarısı" | Ölçümü yok | Betikli tarayıcı koşularında bot akışlarının %76,1'i yakalandı, betikli insan akışlarında yanlış işaret %0 oldu. Gerçek kullanıcıda ölçülmedi. |
| 2 | Kapalı beta, gerçek kullanıcı verisiyle ince ayar | Yanlış | Sentetik veri ve 234 betikli tarayıcı akışı kullanıldı. Gerçek kişi kaydı yok. |
| 3 | RF + Isolation Forest + LSTM; tabloda "+ Transformer" | Yanlış | Skoru tek bir Random Forest üretiyor. IF ile LSTM ölçüm sonucunda skordan çıkarıldı. Transformer hiç kullanılmadı. |
| 4 | AWS Lambda, serverless, 50 ms altı | Mimari yanlış, süre kısmen doğru | Dağıtım Docker Compose ile. Skorlama 17,7 ms, karar uç noktası konteynerde p95 7,4–37,9 ms. Kapasite yük testiyle ölçülmedi. |
| 5 | "Tüm kararlar SHAP ile açıklanabilir" | Abartılı | SHAP yalnızca tek akışın RF skorunu açıklıyor. Karar katmanının adımları gerekçe koduyla açıklanıyor. |
| 6 | Yanlış hesap dondurmayı %40 azaltma | Ölçümü yok | Çıkarılmalı ya da pilotta ölçülecek bir hipotez olarak yazılmalı. Ürün zaten hesap dondurmuyor. |
| 7 | "Anonim; IP, cookie, keystore yok; sadece X-Y" | Eksik ve yanlış | SDK tuş zamanlamasını, kaydırmayı, odak değişimini ve işaretçi türünü de topluyor. Müşteri profili takma adlı biyometrik veri sayılıyor. |
| 8 | SOC ekran görüntüleri (coğrafya, ASN, Puppeteer, IP kara listesi) | Kodda yok | Bugünkü panodan yeni ekran görüntüsü alınmalı. Sentetik demo verisi orada "Sentetik demo verisi" rozetiyle görünür. |
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
  yakalayamadı. Görülmemiş saldırıyı yakalayabilen iki model (lojistik regresyon
  ve MLP) ise görülmemiş insanların %48–68'ini de işaretledi
  (`docs/juri-cevaplari.md`, "Dördüncü Soru"; `TECHNICAL_GUIDE.md` §16).
- **Bağımsız yazılmış saldırgan düzeneği** (sınıf başına 12 oturum,
  `docs/evaluation.md`):
  - Düz çizgide hareket eden otomasyonun %100'ü bloklandı.
  - İnsan personasının %100'ü onaylandı.
  - İnsanlaştırılmış botun da **%100'ü onaylandı** (ortalama skor 22,7;
    insanlara karşı AUC 0,92).
  - Özellikleri bilerek taklit eden bot da %100 onaylandı (AUC 0,08).
- Ekran görüntüsündeki "%98.8 güven" türü değer, bugünkü SOC panosunda
  "Son pencere kesinliği" adıyla geçiyor. Bu değer modelin seçtiği sınıfa
  verdiği olasılıktır, yani max(p, 1−p). Doğruluk oranı değildir
  (`apps/soc/src/pages/Dashboard.jsx` içindeki yorum).
- **Yeniden kontrol edilmeli.** Yukarıdaki sayılar akış başına Random Forest
  skorunu ölçer; karar katmanı bu çalışmada değişti. Örneğin ardışık test (SPRT)
  artık bot sınırını aştığında düşük skorlu oturumu da en az ek doğrulamaya
  gönderiyor (`backend/main.py`, `sequential` gerekçesi; bedeli sentetik
  oturumlarda ölçüldü: yavaş yazan 300 oturumun 5'i, yani %1,7'si onaydan ek
  doğrulamaya geçti). Laboratuvar verisinde de bazı öznitelikler tavana dayanmış.
  `lab/real_telemetry.json` içinde saklanan öznitelik değerleri üzerinde
  yaptığımız sayım (234 akış):
  - `ivme_degisimi` 90 akışta 1,0 değerinde.
  - `duraklama_dagilimi` 109 akışta 1,0 değerinde.
  - `scroll_hizi_varyansi` bütün akışlarda aynı sabit değerde (0,349), yani hiç
    ölçülmemiş.

  Bu yüzden doğruluk sayıları teslimden önce yeniden kontrol edilmeli (son
  bölüme bakın).

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
- **Jüri prototipi sentetik demo müşterileriyle çalışıyor.** Ayşe, Mehmet ve
  Zeynep simülatör kimlikleridir (`backend/demo_seed.py`,
  `train_model.simulate_identity_sessions`); her birine 20 fare ve 20 klavye
  oturumluk sentetik bir geçmiş yüklenir. İşaretleri:
  - veritabanında `is_synthetic` bayrakları (oturum, profil, profil vektörü ve
    karar denetim kaydı; `backend/models.py`);
  - müşteri referansları `sentetik-ayse` gibi; `demo_seed.py --simulate`
    çıktısı "SİMÜLE EDİLMİŞ OTURUM" başlığını taşıyor. (Seçicide "sentetik
    geçmiş" yazan eski demo sayfası 2026-10-02'de silindi; sentetik müşteriler
    bugün yalnızca komut satırından çalıştırılıyor.);
  - SOC panosunda "Sentetik demo verisi" rozeti
    (`apps/soc/src/components/SyntheticBadge.jsx`); metrik kartları simüle
    edilmiş oturumları saymıyor ve kaç tanesini dışarıda bıraktığını yazıyor
    (`apps/soc/src/pages/Dashboard.jsx`);
  - hiçbir değerlendirme betiği bu müşterileri okumuyor ve `record_session.py`
    simüle edilmiş bir oturumu kaydetmeyi reddediyor (`backend/test_demo.py`).

  Bu yüzden raporda "müşteri verisi" ya da "pilot" ifadesi kullanılmamalı. Doğru
  ifade: "sentetik demo müşterileri".

**Önerilen metin**

> Model iki veri kaynağıyla eğitilmiştir: (1) davranış simülatöründen üretilen
> 25.000 sentetik oturum (250.000 akış penceresi) ve (2) gerçek bir Chromium
> tarayıcısında, gerçek SDK üzerinden Playwright betikleriyle yürütülen altı
> senaryodan kaydedilmiş 234 etiketli akış. Bu tarayıcı verisindeki "insan"
> davranışı da betikle üretilmiştir; bugüne kadar gerçek bir kişinin oturumu
> kaydedilmemiştir. Gerçek kullanıcı kaydı için gereken araçlar (kişi bazlı ayrım
> yapan kayıt ve değerlendirme betikleri) hazırdır. Bir sonraki adım, farklı
> kişilerden ve farklı giriş cihazlarından (fare, dokunmatik yüzey, dokunmatik
> ekran, yalnızca klavye) en az 30 oturum toplamaktır. Ekibin henüz bir müşteri
> tabanı olmadığından prototipteki demo müşterileri sentetiktir; veride, demo
> sayfasında ve SOC panosunda sentetik olarak işaretlenir ve hiçbir ölçüme
> katılmaz.

---

## 3. "Random Forest ve Isolation Forest ... PyTorch tabanlı LSTM" ve "Random Forest + LSTM + Transformer"

**Raporda ne yazıyor**

- s. 4 (§3): "Random Forest ve Isolation Forest non-lineer davranış örüntülerinde
  yüksek başarı sağlarken, zaman bağımlı analizler için PyTorch tabanlı LSTM
  modelleri devreye alınır."
- s. 6 (§5), rakip tablosunun "AI Modeli" satırı: "Random Forest + LSTM +
  Transformer".
- s. 7 (§7), proje takvimi: "hibrit model (RF, Isolation Forest) eğitimi".
- s. 3 (§1): "sıralı veriler üzerinde çalışan makine öğrenimi modelleri".
- s. 4–5 (§3): "Risk Score = 100 × P(fraud | behavior)" ve bunun "makine öğrenmesi
  modeli tarafından hesaplanan dolandırıcılık olasılığını" ifade ettiği.

**Bugün doğru olan**

- **Skoru tek bir Random Forest üretiyor.** `backend/scorer.py` içindeki
  `compute_risk()` fonksiyonu `rf.predict_proba` sonucunu kullanır ve skor
  100 × P(dolandırıcılık) olarak hesaplanır. Model 200 ağaçlı ve en fazla 12
  derinlikte (`backend/train_model.py`, `TECHNICAL_GUIDE.md` §7). Her akış tek
  başına skorlanır; sıralı bir model skorda yok.
- **Formül bir hedefi anlatıyor, ölçülmüş bir olasılığı değil.** Random Forest'ın
  `predict_proba` değeri ağaçların oy payıdır; kalibre edilmemiştir (Platt ya da
  izotonik adım yok) ve öğrendiği sınıf dengesi gerçek trafiğin dolandırıcılık
  oranı değil, eğitim kümesininkidir (`backend/scorer.py`, "What this number is"
  yorumu). Bu yüzden 40/60/80 eşikleri bir sıralama skoru üzerindeki eşiklerdir.
  Raporda formül kalabilir, ama "olasılık" yerine "risk skoru" denmeli.
- **Transformer yok.** Depodaki kod dosyalarının hiçbirinde Transformer geçmiyor
  (2026-09-19 tarihli kod araması).
- **Isolation Forest skordan çıkarıldı (ağırlığı 0).** Kaynak: `backend/scorer.py`
  ve `TECHNICAL_GUIDE.md` §7.
  - Eğitimde görülmeyen 82 gerçek tarayıcı akışında tek başına ROC-AUC değeri
    **0,340** çıkmıştı ve "ters çalışıyor" denmişti. **Bu gerekçe geri
    çekildi** (2026-09-25): o ölçüm, donmuş laboratuvar satırları üzerindeydi;
    laboratuvar ham telemetriyle yeniden yakalanınca aynı model **0,657**
    veriyor — ters değil. Bileşen yine de dışarıda, çünkü önemli olan yönde işe
    yaramıyor: meşru akışların %38'ini, kaydedilmiş tek kişinin akışlarının
    %76'sını ve görülmemiş insan senaryosunun %87'sini ek doğrulama çizgisinin
    üstüne koyuyor. Yalnızca insan satırlarıyla eğitildiği için "normal" onun
    gözünde insan dağılımıdır; bu üründe ise saldırı zaten insana benziyor.
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
    80 ve üzeri skorla daha çok bot yakalıyorlar (+0,11 ila +0,15; 47 gerçek
    grubun bootstrap'ı sıfırı dışarıda bırakıyor).
  - Görülmemiş insan senaryosunda RF, ek doğrulama çizgisinde %5 ile en düşük
    orana sahip; LightGBM %30, ExtraTrees %63. Blok çizgisinde RF %0,
    LightGBM %4.
  - **2026-09-25 DÜZELTMESİ:** bu maddenin önceki sürümü "LightGBM görülmemiş
    insanların %74'ünü blokladı" diyordu. **O sayı geri çekilmiştir**;
    `lab/real_telemetry.json` yalnızca normalize vektör sakladığı ve aradaki
    yeniden eğitim ölçeklemeyi değiştirdiği için, ölü bir koordinat sisteminde
    ölçülmüştü. Laboratuvar ham telemetriyle yeniden yakalandıktan sonra aynı
    protokol %4 veriyor. Sonuç (RF'de kalmak) değişmedi; gerekçe olarak
    gösterilen sayı değişti.
- Çalışma yeniden üretilebilir: `cd backend && python model_selection.py` komutu 8
  model ailesini 6 protokolle karşılaştırır (dizüstü bilgisayarda ~20 dakika).

**Önerilen metin (§3 Yöntem)**

> Risk skoru tek bir Random Forest sınıflandırıcısından (200 ağaç) gelir:
> Risk Skoru = 100 × P(dolandırıcılık | davranış). Bu değer bir sıralama
> skorudur; gerçek trafikte kalibre edilmiş bir olasılık olarak ölçülmemiştir.
> Isolation Forest, LSTM ve
> gradient boosting modelleri de denenmiş, ölçüm sonucunda skordan çıkarılmıştır.
> Isolation Forest gerçek tarayıcı verisinde meşru akışların %38'ini ek
> doğrulama çizgisinin üstüne koymuştur (ROC-AUC 0,657). LSTM, sunum yolunun
> ürettiği biçimde eğitildiğinde bile (ROC-AUC 0,871) tek başına Random
> Forest'ın altında kalmış (0,996) ve var olma sebebi olan devir-teslimde bir
> akış geç tepki vermiştir. Gradient boosting daha çok bot yakalamakta, ancak
> görülmemiş meşru senaryolarda Random Forest'tan daha çok müşteriyi ek
> doğrulamaya düşürmektedir. Zaman boyutu, oturumun son beş akışının medyanı ve
> ani sıçramaları yumuşatmadan geçiren açık bir kuralla ele alınır. Model seçimi
> çalışması depoda yeniden üretilebilir durumdadır.

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
| `/api/decision`, müşteri profili kapalı | p50 5,0 ms / p95 7,4 ms (tekrarı: 4,8 / 7,4 ms) | Konteyner, docker-compose topolojisi, Linux, n=300. ASGI uygulaması doğrudan çağrıldı (HTTP istemcisi yok). Geçici PostgreSQL 16, sentetik oturumlar | `docs/profile-evaluation.md` §11 |
| `/api/decision`, profil gölge modda | p50 24,0 ms / p95 34,8 ms (tekrarı: 23,8 / 37,9 ms) | aynı | aynı |
| `/api/decision`, profil uyguluyor ve öğreniyor | p50 24,0 ms / p95 31,7 ms (tekrarı: 23,9 / 33,6 ms) | aynı | aynı |
| `/api/decision`, profil uyguluyor ve ek doğrulama istiyor | p50 16,0 ms / p95 21,8 ms (tekrarı: 15,9 / 22,3 ms) | aynı. Bu yol öğrenme yazmasını atladığı için daha ucuz | aynı |
| Aynı ölçüm, Windows ana makinesinden Docker Desktop port yönlendirmesiyle | Profil kapalıyken p95 15,1 ms (tekrarı: 14,8 ms). Profil açık yollarda p95 35,0–53,8 ms; dört yapılandırma **50 ms bütçesini aştı** | Windows geliştirme makinesi | aynı |

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
  (`apps/soc/src/pages/Dashboard.jsx`).
- **Kapasite değerlendirmesi yazıldı; yük testi hâlâ yok.** Bugünkü kodla
  eşzamanlı istek kapasitesi **ölçülmedi**. Var olan ölçümlerden aritmetikle
  çıkarılan kapasite, maliyet ve aşırı yük değerlendirmesi artık depoda:
  `TECHNICAL_GUIDE.md` §17 (ayrıntı) ve `docs/juri-cevaplari.md` "Beşinci Soru"
  (jüriye anlatım). Özet: vCPU başına ~87 eşzamanlı ödeme oturumu, ödeme başına
  0,71 CPU-saniye, vCPU-saat başına ~5.000 ödeme; tavan aşılınca istekler
  kuyruğa girer, süreç ölmez; gerçekten devirebildiğimiz tek şey bellekti
  (dört işçi, ~2 GB'lık sanal makine, `/api/health` 25 saniye). Yöntem, tek
  gerçek eşzamanlılık ölçümüne (`AUDIT.md` C-3, eski kod) %2,4 yaklaşıyor.
  Raporda bu sayılar **yalnızca** "ölçümden türetilmiş hesap, yük testi değil"
  etiketiyle verilmeli. Ekran görüntüsündeki "4,205 istek/sn" ve "24 ms"
  değerleri ölçüm değil.
- **Entegrasyon adımları** `README.md` içindeki Türkçe "Entegrasyon" bölümünde:
  script etiketi, `DeepCheck.init` çağrısı ve kurumun **kendi sunucusundan**
  `POST /api/decision` çağrısı. Ayrıntılı sözleşme — hata yönetimi, hız
  sınırları, rıza/silme/sonuç uç noktaları, WebView ve yerel mobil yol
  haritası, kapasite hesabı — `docs/entegrasyon.md` dosyasındadır.

**Önerilen metin (§3 Yöntem)**

> DeepCheck, Docker Compose ile tek komutla ayağa kalkan üç servisten oluşur:
> PostgreSQL veritabanı, FastAPI arka ucu ve React ön yüzü. Kurum sistemi kendi ağı
> içinde çalıştırabilir; davranış verisi kurumun altyapısından çıkmaz. Tek bir
> davranış akışının skorlanması (öznitelik çıkarımı, Random Forest ve SHAP
> açıklaması) 17,7 ms olarak ölçülmüştür. Ödeme anındaki karar uç noktası konteyner
> içinde, müşteri profili kapalıyken p95 7,4 ms, açıkken en fazla p95 37,9 ms
> ölçülmüştür. Bu süreler ağ gecikmesini içermez ve geçici bir veritabanında,
> sentetik oturumlarla ölçülmüştür. Yüksek eşzamanlı yük altındaki kapasite henüz
> bir yük testiyle ölçülmemiştir.
> *[Kapasite değerlendirmesi tamamlandığında buraya onun hesapla türetilmiş
> rakamları "ölçümden türetilmiş hesap" etiketiyle eklenecek.]*

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
  üçü saklanıyor ve SOC panosunda "Kararı Taşıyan Özellikler" (SHAP) başlığıyla
  gösteriliyor (`apps/soc/src/pages/Dashboard.jsx`). Ağaç modellerinde bu hesap
  kesin sonuç veriyor.
- SHAP'ın **açıklamadığı** karar adımları:
  - **Oturum skoru.** Panodaki rozet, son 5 akışın medyanı ve sıçrama kuralıyla
    yumuşatılmış skoru gösterir. SHAP çubukları ise yalnızca son akışı anlatır:
    `session.shap_explanation` son akıştan yazılır (`backend/main.py`).
  - **Ardışık test (SPRT).** "Yeterli kanıt yok", "belirsiz" ve "biriken kanıt"
    kararları (`insufficient_evidence`, `ambiguous`, `sequential`). Sınırların
    kendisi, kalibre edilmemiş skorlar üzerinde **ölçümle seçilmiş bir çalışma
    noktasıdır**, Wald'ın hata oranları değildir (`backend/main.py`,
    `SPRT_NOMINAL_ALPHA` üstündeki yorum); yani "şu oranda yanılır" diye bir
    garanti vermez.
  - **Diğer kurallar.** Konformal koruma (bir bloğu ek doğrulamaya çevirebilir),
    küme kuralı (varsayılan olarak kapalı) ve bayat veri kuralı (`stale`).
    Konformal koruma **sunulan modelde atıldır**: kalibrasyonu laboratuvarın
    betikli insan personalarından gelen 36 skordur, en yükseği 27,71'dir, bu
    yüzden 80 ve üzeri her skor p = 1/37 = 0,027 < 0,05 alır ve hiçbir blok
    yumuşatılmaz. Model paketi bu durumu yüklenirken günlüğe yazar
    (`backend/scorer.py`). Raporda "konformal koruma kullanıcıyı koruyor"
    denmemeli; gerçek insanlarla kalibre edilene kadar kimseyi korumuyor.
  - **Müşteri profili katmanı.** Bu katmanın kendi açıklaması var: müşterinin kendi
    geçmişinden en çok sapan K=3 öznitelik (z değerleriyle) ve konformal p-değeri.
    SOC'taki "Müşteri Profili" kartında gösteriliyor
    (`apps/soc/src/components/ProfilePanel.jsx`). Bu SHAP değil.
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
  nedenle `/api/decision`, altı iç gerekçeyi (`cluster`, `conformal`, `ambiguous`,
  `sequential`, `profile_deviation`, `profile_rate_limited`) istemciye tek bir
  `step_up` olarak bildirir
  (`backend/main.py`, `PUBLIC_REASONS`). İç gerekçe yalnızca SOC panosunda ve
  denetim kaydında durur.

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
  `test_profile_layer_never_blocks_and_never_changes_the_score` testi 1.440
  kombinasyonda doğruluyor.
- **Yanlış bloklamaya karşı konformal koruma bugün devreye girmiyor.** Kod, gerçek
  kullanıcı dağılımına uyan yüksek bir skorda bloğu ek doğrulamaya çevirecek bir
  koruma içeriyor. Ancak kalibrasyonu, laboratuvarın betikli insan senaryolarından
  gelen 36 değere dayanıyor ve bunların en yükseği 27,71. Bu yüzden 80 ve üzeri her
  skor p = 1/37 alıyor ve koruma hiçbir zaman tetiklenmiyor (`backend/main.py`,
  "Conformal guard" yorumu). Gerçek kullanıcılarla kalibre edilene kadar raporda
  "yanlış bloklamayı önleyen koruma" diye sunulmamalı.
- **Ek doğrulama kanalı kurumun kendi kanalıdır** (SMS, 3-D Secure vb.). Demoda bu
  kanalın yerine sayfada yazan sabit bir kod kullanılıyor. Face ID entegrasyonu
  yok.
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
    oturum; `docs/evaluation.md`). Bu tablo skorda henüz LSTM varken ölçüldü ve
    bugünkü modelle yeniden koşulmadı. Aynı tablodaki altıncı dilim (ilk akışı çok
    seyrek olan oturum) %35 oranında blok eşiğinin üstüne çıkıyordu; bu tür akışlar
    artık "geçici" olarak işaretleniyor ve arayüzde risk rengi yerine "ölçülüyor"
    gösteriliyor.

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
  `_client_ip`). Web sunucusu erişim günlükleri istemci IP'sini konteyner
  günlüklerine yazıyordu; ikisi de artık kapalı:
  - Uvicorn `--no-access-log` ile çalışıyor (`backend/entrypoint.sh`).
  - Ön yüzlerin nginx'lerinde `access_log off;` var (`apps/checkout/nginx.conf`,
    `apps/soc/nginx.conf`).

  nginx'in hata günlüğü açık ve bir hata satırında istemci adresini yazabilir. Bu
  yüzden "IP adresi veritabanına yazılmaz, erişim günlükleri kapalıdır" doğru; "hiçbir
  yerde IP yok" doğru değil.
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
  bekleyen oturum vektörü saklanır. Her vektör, oturumun 12 normalize öznitelik
  değerinden (ölçülmeyen öznitelik boş kalır) ve öznitelik başına akışlar arası
  yayılımdan oluşur; yayılım kaydedilir ama karara girmez
  (`backend/profiles.py`, `session_vector`).
- **Profil kayıtlarının saklanması.** Profil 180 gün hareketsiz kalırsa silinir.
  Karar denetim kaydı 90 gün, profil erişim kaydı 365 gün saklanır
  (`backend/main.py`). Demo ad alanındaki karar kayıtları ve sentetik olmayan bir
  demo profiliyle onun öğrendiği vektörler oturumla aynı sürede, 24 saatte
  silinir; orada kimse rıza vermedi ve silme yolu yok. Bu ad alanına bugün
  ödeme sayfasından yazılmaz: içinde yalnızca `demo_seed.py`'nin sentetik
  müşterileri bulunur (ziyaretçinin yazabildiği eski demo sayfası 2026-10-02'de
  silindi).
- **Silme ve itiraz.** `POST /api/profile/erase` uç noktası `erase` (silme) ve
  `object` (itiraz) modlarını destekler. İtiraz kaydı sonraki rıza denemelerini
  engeller (409). Silme; oturumlar, karar kayıtları ve inceleme erişim kayıtlarıyla
  bağı kaldırır ve karar kayıtlarında saklanan karşılaştırma vektörlerini de siler.
  Silmeyle aynı anda verilen bir karar, silinen takma adı kendi kaydına yazamaz
  (PostgreSQL 16 üzerinde iki sırayla da doğrulandı).
- **İnsan incelemesi.** `GET /api/profile/review/{session_id}` ayrı bir operatör
  anahtarıyla çalışır ve her erişim kaydedilir.
- **Hiçbir zaman bloklamaz.** Katman yalnızca ek doğrulama ister. Bu, GDPR md.
  22(4)'ün özel nitelikli veriye dayalı tamamen otomatik karar yasağına da uygun.

**Henüz olmayanlar (raporda iddia edilmemeli)**

- **Hukuki metinler.** KVKK aydınlatma metni (`docs/kvkk-aydinlatma.md`; mağaza
  sayfasındaki bağlantı onu uygulamanın `/gizlilik` sayfasında gösterir) ve veri koruma
  etki değerlendirmesi (`docs/dpia.md`) artık **taslak** olarak var. İkisi de hukuki
  incelemeden geçmedi. Etki değerlendirmesi herhangi bir pilotun ön koşulu ve taslak
  kendi sonucunda bugünkü hâliyle bir pilotu desteklemediğini söylüyor. Raporda
  "KVKK uyumlu" denmemeli; "taslak aydınlatma metni ve etki değerlendirmesi var"
  denebilir.
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
  değeri hukuk incelemesine sunulmalı. Jüri demosundaki sentetik müşterilerin
  kaydında dayanak `demo` yazıyor: arkalarında rıza verebilecek bir kişi yok
  (`backend/models.py`).
- **Anahtar değişimi.** Profil anahtarı değiştirilirse bütün profil kimlikleri
  yeniden türetilir ve her profil sessizce sıfırlanır; ham referans
  saklanmadığı için eski profiller yeni anahtarla eşleştirilemez (`.env.example`,
  `backend/profiles.py`). Bu, anahtarın uzun ömürlü olmasını gerektiren bilinçli
  bir takastır.

**Önerilen metin (risk tablosu, Veri Gizliliği)**

> Tuş içeriği, form alanları ve sayfa içeriği hiçbir zaman toplanmaz; SDK yalnızca
> işaretçi koordinatlarını, tıklama ve kaydırma olaylarını, tuşa basma zamanlarını
> ve sekme odak zamanlarını gönderir. Ham davranış verisi 1 saat içinde boşaltılır,
> 24 saat içinde silinir; veritabanında IP adresi tutulmaz. Müşteri profili yalnızca
> açık rıza ile oluşturulur, takma adlı (HMAC) bir kimlikle saklanır, ham veri
> içermez ve silme ya da itiraz yoluyla kaldırılabilir. Kişiye bağlı davranış profili
> KVKK md. 6 kapsamında özel nitelikli (biyometrik) veri olarak ele alınır; pilot
> öncesinde veri koruma etki değerlendirmesi yapılacaktır.

Erişim günlükleri kapatıldığı için bu metin bugünkü kodla doğru. Metne "hiçbir
yerde IP tutulmaz" gibi daha geniş bir cümle eklenmemeli: nginx'in hata günlüğü
açık.

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
- **"SHAP Biyometrik Nedensellik" paneli:** SHAP katkısı yerine ham birimler
  gösteriyor ("Jerk: 0.00", "4000 px/sn", "12 ms", "X:420, Y:800").
- **Diğer alanlar:** "AI Güven Skoru (Confidence): %98.8", "Biyometrik parmak izine
  göre otomatik engelleme uygulandı" ve ham sensör JSON'u (`is_bot_prob`,
  `fingerprint`).
- **Düğmeler:** "IP'yi Kara Listeye Al" ve "Oturumu Sonlandır".

**Bugün doğru olan** (`apps/soc/src/pages/Dashboard.jsx`)

- Pano `http://localhost:3100` adresinde, kendi sunucusuyla ayrı bir uygulama
  olarak çalışıyor (`apps/soc`) ve açılışta erişim anahtarı istiyor.
- Panoda şunlar var:
  - dört metrik kartı: Toplam Oturum, Ortalama Risk Skoru, Tespit Edilen Bot,
    Ortalama Yanıt Süresi;
  - renkli etiketli oturum listesi;
  - seçili oturum için risk rozeti, "Son pencere kesinliği" ve yanıt süresi;
  - "Kararı Taşıyan Özellikler" (SHAP) çubukları;
  - "Müşteri Profili" kartı (`apps/soc/src/components/ProfilePanel.jsx`);
  - D3.js ile çizilen "Risk Skoru Geçmişi" grafiği;
  - sentetik veride mor "Sentetik demo verisi" rozeti: simüle edilmiş oturumlarda
    (oturum listesi ve seçili oturum) ve sentetik bir profille verilen kararlarda
    (profil kartı). Metrik kartları simüle edilmiş oturumları saymaz; kartların
    altında "Metrikler N sentetik demo oturumunu (simüle edilmiş, gerçek kişi değil)
    içermez." yazar.
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
  `POST /api/decision` uç noktasında veriliyor. Sağ üstteki "Oturumu Kapat"
  düğmesi analistin panodan çıkışıdır (erişim anahtarını sekmeden siler), müşteri
  oturumunu sonlandırmaz.
- Görüntülerdeki sayılar (4.205 istek/sn, 842 bloke/sa, 24 ms, %98,8) ölçülmemiş
  örnek değerler.
- Görüntüdeki "Dikkat Skoru"nun yönü bugünkü sistemin tersi: bot 12/100 almış, yani
  yüksek değer insanı gösteriyor. Bugünkü sistem risk skoru kullanıyor; yüksek değer
  riskli demek ve bot 80 ve üzeri alıyor.

**Önerilen işlem**

İki görüntüyü kaldırın ve bugünkü panodan yeni görüntü alın. Adımlar
`docs/juri-cevaplari.md` içindeki "Demo Prosedürü — Sentetik demo müşterileri"
bölümünde:

1. Sistemi başlatın ve sentetik demo müşterilerini yükleyin
   (`docker compose up -d --build`, ardından
   `docker compose exec backend python demo_seed.py`).
2. `docker compose exec backend python demo_seed.py --simulate ayse` çalıştırın
   (sentetik müşteri adına ödeme yapılan eski `/demo` sayfası 2026-10-02'de
   silindi; bugün sentetik bir müşteri yalnızca bu komutla çalıştırılır).
3. SOC'ta (`http://localhost:3100`) bu oturumu seçip görüntüyü alın.

Sentetik bir müşteriyle verilen kararda rozet "Müşteri Profili" kartında çıkar;
simüle edilmiş (`demo_seed.py --simulate`) oturumlarda ayrıca oturum listesinde
ve seçili oturumda çıkar. Görüntü bu kartı ve rozeti içermeli; rozet kırpılmamalı
ya da üstü kapatılmamalı. Sentetik müşteri kullanılmış ama rozeti görünmeyen bir
görüntü rapora girmemeli.

**Önerilen resim altı**

> Şekil X. DeepCheck SOC panosu (çalışan prototip): oturum listesi, seçili oturumun
> risk skoru ve skor geçmişi, akış skorunun SHAP açıklaması (en etkili üç öznitelik)
> ve müşteri profili kartı. Görüntüdeki müşteri geçmişi **sentetiktir**
> (simülatörle üretilmiş demo müşterisi, panoda "Sentetik demo verisi" olarak
> işaretli); gerçek müşteri verisi içermez ve bir doğruluk ölçümü değildir.

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
| **DataDome** | Her isteği sunucu tarafı bir modülle gerçek zamanlı değerlendirir. İstemci tarafındaki JavaScript etiketi fare hareketi ve tuş vuruşu gibi davranış verisi ile işletim sistemi, tarayıcı ve GPU bilgisi toplar; Headless Chrome, Puppeteer ve Selenium gibi otomasyon araçlarını tespit etmeye yönelik testler çalıştırır. Device Check sınamasını belgeler. Algılama betiğini periyodik olarak yeniden derleyerek tersine mühendisliği zorlaştırır. | [JavaScript etiketi](https://docs.datadome.co/docs/javascript-tag), [betik yenileme](https://datadome.co/bot-management-protection/dynamic-bot-detection-script-rebuilds/) |
| **HUMAN Security** | Eski adı White Ops. 2022'de PerimeterX ile birleşti; birleşme duyurusu, gelişmiş bot saldırılarına, dolandırıcılığa ve hesap kötüye kullanımına karşı koruma hedefini adlandırıyor. | [birleşme duyurusu](https://www.humansecurity.com/newsroom/human-and-perimeterx-unite-in-market-changing-merger-to-safeguard-customers-from-sophisticated-bot-attacks-fraud-and-account-abuse/) |
| **Kasada** | Kullanıcının görmediği istemci tarafı sınamalar ve otomasyon izi toplayan çok sayıda sensör kullanır. Kodunu yüksek derecede gizlenmiş, dinamik bir sanal makinede çalıştırarak saldırganı gerçek tarayıcı kullanmaya zorlar ve saldırıyı pahalılaştırmayı hedefler. Gerçek kullanıcıyı sınamayla kesmemeyi vurgular. | [Bot Defense](https://www.kasada.io/bot-defense), [ürün](https://www.kasada.io/product/) |
| **reCAPTCHA Enterprise** (Google Cloud) | Her etkileşim için 0,0–1,0 arası bir skor döndürür (1,0 düşük risk demek). Faturalandırma hesabı eklenmiş projelere gerekçe kodları verir. Site değerlendirmeleri LEGITIMATE/FRAUDULENT olarak işaretleyerek (annotate) modele geri bildirim gönderir. Güncel belgeler "Google Cloud Fraud Defense" başlığı altında yayımlanıyor. | [skor yorumlama](https://docs.cloud.google.com/recaptcha/docs/interpret-assessment-website), [annotate](https://docs.cloud.google.com/recaptcha/docs/annotate-assessment) |
| **NuData Security** (Mastercard) | Mastercard'ın satın alması 29 Mart 2017'de duyuruldu. NuDetect ürünü, pasif biyometri ve davranış analitiğiyle kullanıcıyı çevrimiçi etkileşimlerinden tanımayı hedefler. Mastercard, NuData'yı cihaz düzeyi güvenlik ve kimlik doğrulama ürünlerine entegre edeceğini duyurdu. | [Mastercard duyurusu](https://investor.mastercard.com/investor-news/investor-news-details/2017/Mastercard-Enhances-Security-of-the-Internet-of-Things-with-the-Acquisition-of-NuData-Security-Inc/default.aspx) |
| **BehavioSec** (LexisNexis Risk Solutions) | 2008'de İsveç'te kuruldu. 3 Mayıs 2022'de LexisNexis Risk Solutions tarafından satın alındı. Davranış analiziyle sürekli kimlik doğrulama yapar; mobil dokunmatik ekran ve sensör sinyallerini de işler. Duyuruya göre LexisNexis'in ThreatMetrix platformuna entegre edilecek. | [LexisNexis duyurusu](https://risk.lexisnexis.com/about-us/press-room/press-release/20220503-behaviosec) |
| **BioCatch** | Davranışsal biyometri platformu. Hesap açma dolandırıcılığını, hesap ele geçirmeyi, sosyal mühendislik dolandırıcılığını ve para katırı hesaplarını, ayrıca bot, uzaktan erişim ve zararlı yazılım saldırılarını hedefler. | [BioCatch davranışsal biyometri](https://www.biocatch.com/behavioral-biometrics) |

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
| 5 | s. 5: "saatler içinde entegre edilebilmektedir"; s. 6: "çoklu platform entegrasyonu" | Entegrasyon süresi ölçülmedi. Adımlar belli: script etiketi, `DeepCheck.init` çağrısı ve kurumun kendi sunucusundan `/api/decision` çağrısı (`README.md` "Entegrasyon" bölümü; tam sözleşme `docs/entegrasyon.md`). Yerel (native) Android/iOS SDK'sı yok; `docs/entegrasyon.md` §4 yerel bir SDK'nın üretmesi gereken yükü **yol haritası** olarak tanımlar. SDK tarayıcı JavaScript'i olduğu için bir mobil uygulamanın WebView'unda çalışabilir, ama bu yol test edilmedi ve model hiç dokunmatik veri görmedi (`backend/train_model.py`); dokunmatik oturumda müşteri profili için sentetik geçmiş de yok. | Süre yerine adımları yazın. Mobil uygulamalar için "WebView yolu test edilmedi; dokunmatik veriyle eğitim ve yerel SDK yol haritasında" yazın |
| 6 | s. 7 risk tablosu: "Güvenli API mimarisi, uçtan uca şifreleme ve dâhili anomali tespiti" | Kodda olanlar: HMAC-SHA256 imzalı ve 30 dakikada sona eren oturum anahtarı (token), iş ispatı, telemetri tekrar oynatma ve saat tutarlılığı kontrolleri, hız sınırları (`backend/main.py`). "Uçtan uca şifreleme" kodda yok (bkz. bölüm 7). "Dâhili anomali tespiti" yapan Isolation Forest skordan çıkarıldı. İş ispatının sınırı ölçüldü: tarayıcı ve SDK kullanmayan düz bir Python istemcisi 50 denemenin 50'sinde geçerli anahtar aldı, uçtan uca medyan 16 ms (2026-09-19, geliştirme dizüstü bilgisayarı; `backend/main.py`, "Runtime attestation" yorumu). | Kodda olanları yazın ve sınırı da ekleyin: iş ispatı istemcinin tarayıcı olduğunu kanıtlamaz, yalnızca oturum başına küçük bir maliyet getirir; tarayıcı süren bir bot bu kontrolü dürüstçe geçer |
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
| 3 | Oturum kimliği ve imzalı oturum anahtarı sunucudan geliyor, iş ispatı karşılığında veriliyor ve 30 dakikada sona eriyor; telemetri tekrar oynatması reddediliyor | HMAC-SHA256 oturum anahtarı, iş ispatı, yük özeti, zaman sırası ve saat ofseti kontrolleri | `backend/main.py`, `sdk/deepcheck.js` | Kod ve test. Sınırı ölçüldü: düz bir Python istemcisi 50/50 denemede anahtar aldı (medyan 16 ms); iş ispatı istemcinin tarayıcı olduğunu kanıtlamaz |
| 4 | Betikli tarayıcı koşularında betikli insan akışlarında yanlış işaret %0, bot akışlarında yakalama **%93,0** | Eğitimde görülmeyen koşular, 71 akış (2026-09-25, yeniden yakalanmış laboratuvar) | `docs/evaluation.md` | Betikli Playwright koşuları. 2026-09-06'da aynı tablo %76,1 diyordu; aradaki fark laboratuvarın ham telemetriyle yeniden yakalanmasıdır, eşik değişikliği değil |
| 5 | Bağımsız saldırgan düzeneğinde düz çizgi otomasyonu %100 bloklandı, insan persona %100 onaylandı; sınırı da ölçüldü: insanlaştırılmış bot durdurulamadı | Sınıf başına 12 oturum | `docs/evaluation.md` | Bağımsız yazılmış simülasyon |
| 6 | Model seçimi ölçüme dayanıyor: RF, 7 başka model ailesiyle karşılaştırıldı; görülmemiş insan senaryosunda ek doğrulama oranı RF %5, LightGBM %30, ExtraTrees %63 | 8 aile, 6 protokol (2026-09-25 yeniden yakalanmış laboratuvarla) | `backend/model_selection.py`, `backend/scorer.py`, `docs/juri-cevaplari.md` | Sentetik veri, betikli tarayıcı verisi ve tek bir gerçek kişi |
| 7 | Isolation Forest ve LSTM ölçüm sonucunda skordan çıkarıldı; düzeltilmiş veride **yeniden yargılandı**, ikisi de dışarıda kaldı | IF: eski "0,340 / ters" gerekçesi **geri çekildi**, düzeltilmiş veride 0,657 — ama meşru akışların %38'ini ek doğrulamaya gönderiyor. LSTM: 0,871 (sunum biçimi), RF 0,996; devir-teslimde RF %100'ü ilk akışta, LSTM %63'ünü | `backend/model_selection.py`, `backend/scorer.py` | 301 gerçek satır: 252 betikli tarayıcı akışı + tek bir gerçek kişiden 49 akış |
| 8 | Oturum ortasındaki devir-teslim ilk otomatik akışta yakalanıyor | 185 devir-teslimin 185'i yakalandı; 14.790 meşru akıştan en fazla 6'sı etkilendi | `backend/scorer.py` | Simülasyon |
| 9 | Meşru kullanıcı bloklanmıyor: beş etkileşim stilinde (yalnızca klavye kullananlar dahil) bloklama %0; LSTM çıkarıldıktan sonra yavaş yazan simüle kullanıcıda doğrudan blok %5,2'den %0,2'ye indi | Beş stil: %95 güven aralığı 0–6,0, stil başına n=60 (skorda LSTM varken ölçüldü). Yavaş yazan: stil başına 500 oturum | `docs/evaluation.md`, `backend/benchmark.py`, `TECHNICAL_GUIDE.md` §7 | Sentetik. **Bu çalışmada yeniden ölçülmedi.** Sonradan gelen ardışık test kuralının bedeli ayrıca ölçüldü: yavaş yazan 300 sentetik oturumun %1,7'si onaydan ek doğrulamaya geçti (blok değil) |
| 10 | Skorlama 17,7 ms; karar uç noktası konteynerde p95 7,4 ms (profil kapalı), en fazla 37,9 ms (profil açık) | Bkz. bölüm 4 | `backend/scorer.py`, `docs/profile-evaluation.md` §11 | Ağ hariç; geçici PostgreSQL, sentetik oturumlar |
| 11 | SDK 34 KB (gzip ile 12 KB) ve bağımlılıksız | Dosya boyutu | `sdk/deepcheck.js` | 2026-09-19 |
| 12 | Tuş içeriği hiçbir zaman okunmuyor; ham telemetri 1 saatte boşaltılıyor, 24 saatte siliniyor; veritabanında IP ve User-Agent yok | `onKeyDown` yalnızca zaman damgası kaydeder; saklama varsayılanları | `sdk/deepcheck.js`, `backend/main.py`, `backend/models.py` | Kod (erişim günlüğü notuyla, bölüm 7) |
| 13 | Açıklanabilirlik: akış başına SHAP ile en etkili 3 öznitelik, karar gerekçe kodları, profil kararlarında en çok sapan öznitelikler; açıklama skorlanan istemciye gönderilmiyor, altı iç gerekçe istemciye tek bir `step_up` olarak gidiyor | SOC panosu, `PUBLIC_REASONS` | `backend/scorer.py`, `backend/main.py`, `apps/soc/src/pages/Dashboard.jsx`, `apps/soc/src/components/ProfilePanel.jsx` | Kod |
| 14 | Müşteri profili yalnızca ek doğrulama istiyor; hiçbir zaman bloklamıyor ve skoru değiştirmiyor | 1.440 kombinasyonluk test | `backend/test_profiles.py` (`test_profile_layer_never_blocks_and_never_changes_the_score`) | Test |
| 15 | Müşteri profili aynı kişiye yanlışlıkla ek doğrulamayı fare kullanıcılarında %4,9, klavye kullanıcılarında %3,8 oranında sordu (**alt sınır**). Farklı bir kişiyi fare kullanıcılarında %47,5, klavye kullanıcılarında %26,4 oranında ek doğrulamaya gönderdi. | 200 sentetik kimlik | `docs/profile-evaluation.md` §5 | **Sentetik kimlikler; gerçek müşteri verisi yok.** Katman kart deneme botlarına karşı hiçbir şey yapmaz; yalnızca insan eliyle hesap ele geçirmeyi hedefler |
| 16 | Ek doğrulamayı geçen bir saldırganın oturumları profili zehirlemiyor: saldırganın 0 ile 6 arasında oturumu onay bekleyen olarak saklandığında, ek doğrulamaya gönderilme oranı her durumda %47,5 kaldı (düz eğri). Sınırı: kurum ödemeyi onaylarsa (`settled`) oturum referansa dönüşür ve koruma azalır; tek bir onaylı oturumla oran %25,5'e düştü. | Fare, alfa 0,05 | `docs/profile-evaluation.md` §7 | Sentetik kimlikler |
| 17 | KVKK'ya göre tasarlandı: rıza olmadan profil yok, HMAC takma ad, silme ve itiraz, insan incelemesi, tanımlı saklama süreleri | Bkz. bölüm 7 | `backend/main.py`, `backend/profiles.py` | Kod. Aydınlatma metni (`docs/kvkk-aydinlatma.md`, mağazada `/gizlilik` sayfası) ve etki değerlendirmesi (`docs/dpia.md`) **taslak** hâlde var; hukuki incelemeden geçmedi ve taslak kendi sonucunda bugünkü hâliyle bir pilotu desteklemediğini söylüyor. "KVKK uyumlu" denmemeli |
| 18 | Ekip kendi sınırlarını ölçüp yazdı: bağımsız insanlaştırılmış bot durdurulamadı, profil katmanı kart denemesine karşı etkisiz, gerçek kişi ölçülmedi | Bölüm 1, 2 ve 15. satır | `docs/evaluation.md`, `docs/profile-evaluation.md` | Jüriye önce biz söyleyelim |
| 19 | Entegrasyon sözleşmesi yazılı: script etiketi, `DeepCheck.init`, satıcının sunucusundan `POST /api/decision`, kapalı devre hata yönetimi, rıza/silme/sonuç uç noktaları ve yerel SDK'nın üretmesi gereken yük | Kod ve uç nokta sözleşmesi | `docs/entegrasyon.md`, `README.md`, `backend/main.py` | Kod. Web yolu çalışıyor; WebView yolu **test edilmedi**, yerel SDK **yok** |
| 20 | Kapasite ve maliyet kâğıt üzerinde hesaplandı: vCPU başına ~87 eşzamanlı ödeme oturumu, ödeme başına 0,71 CPU-saniye, vCPU-saat başına ~5.000 ödeme, 1.000.000 müşteri profili için ≤32,7 GB | Ölçülen istek ve satır maliyetleri üzerinde aritmetik; yöntem tek gerçek eşzamanlılık ölçümüne %2,4 yaklaşıyor | `TECHNICAL_GUIDE.md` §17, `docs/entegrasyon.md` §9 | **Ölçümden türetilmiş hesap, yük testi değil.** Bu etiket olmadan rapora girmemeli |

---

## Teslimden önce yeniden kontrol edilmesi gereken sayılar

Bu dosyadaki bazı sayılar bu çalışmanın içinde değişiyor ya da henüz kesinleşmedi.
Rapor teslim edilmeden önce aşağıdakiler, adı geçen dosyanın o günkü hâliyle
karşılaştırılmalı:

- **Model doğruluğu.** Karar katmanının iki adımı bu çalışmada değişti ve
  **yerleşti**: (a) ardışık test artık bot sınırı aşıldığında düşük skorlu
  oturumu da en az ek doğrulamaya gönderiyor (`sequential`; sentetik ölçümde
  300 devir-teslim simülasyonunun tamamı ek doğrulamaya düştü, bedeli yavaş
  yazan 300 oturumun %1,7'si), (b) konformal korumanın sunulan modelde **atıl**
  olduğu yazıldı ve model paketi bu durumu yüklenirken günlüğe geçiriyor.
  Modelin kendisi o çalışmada yeniden eğitilmedi.

  **2026-09-25 güncellemesi:** yol haritasındaki o iş yapıldı. Laboratuvar
  `lab/capture.py` ile ham telemetriyle yeniden yakalandı (46 koşuda 252 akış),
  model iki paket için de yeniden eğitildi ve model seçimi çalışması baştan
  koşturuldu. Yukarıdaki üç satırın yerine geçen güncel sayılar:
  - `docs/evaluation.md`: bot akışlarında %93,0, betikli insan akışlarında %0;
  - `backend/scorer.py`: RF tpr@0.8 = 0,81, görülmemiş insan senaryosunda
    ek doğrulama oranı 0,05 / blok oranı 0,00;
  - `docs/juri-cevaplari.md`: ROC-AUC 0,996; **eski %74 sayısı geri çekildi**
    (aynı protokol artık LightGBM için %4 veriyor — bkz. Dördüncü Soru'nun
    başındaki düzeltme kutusu).
- **Kapasite ve maliyet.** Kâğıt üzerindeki hesap yazıldı: `TECHNICAL_GUIDE.md`
  §17, `docs/entegrasyon.md` §9 ve `docs/juri-cevaplari.md` "Beşinci Soru".
  Yük testi hâlâ yapılmadı. Rapora girecek her kapasite sayısı "ölçümden
  türetilmiş hesap" etiketi taşımalı. Oradan alınabilecek sayılar: vCPU başına
  ~87 eşzamanlı ödeme oturumu, ödeme başına 0,71 CPU-saniye, vCPU-saat başına
  ~5.000 ödeme, ödeme başına ilk saatte 183 kB / 24 saat sonra 0 B, müşteri
  profili başına en fazla 32,7 kB (1.000.000 müşteri için ≤32,7 GB, tek girdi
  türü). Hesabın tek büyük varsayımı `/api/analyze`'in ~23 ms'lik süresidir: o
  uç nokta tek başına **hiç ölçülmedi** ve rapora girerken bu da söylenmeli.
  Maliyeti para birimiyle yazmayın: bir vCPU-saat fiyatı satın alınmadı,
  ödeme başına çıkarım maliyeti o fiyatın 1/5000'idir.
- **Gecikme.** Süreler ölçüm oturumları arasında oynuyor. Örneğin aynı konteyner
  yapılandırmasının arka arkaya iki koşusunda gölge modun p95 değeri 34,8 ms ve
  37,9 ms çıktı (`docs/profile-evaluation.md` §11). Rapora son ölçüm alınmalı.
  Bu dosyadaki süre satırları o sayfadan türetilir; sayfa yeniden üretildiğinde
  buradaki satırlar da yenilenmeli (bunu `backend/test_profiles.py` içindeki
  `test_report_corrections_latency_matches_the_published_evaluation` denetler).
- **Eskimiş belge ifadeleri — 2026-09-20 itibarıyla durumu.** Aşağıdakiler bu
  dosya ilk yazıldığında eskimişti; hepsi düzeltildi, ama rapora kopyalanmadan
  önce yine de kaynağından okunmalı:
  - `README.md` "Performance": artık 17,7 ms yazıyor ve 42,4 ms'nin eski üç
    modelli topluluğa ait olduğunu açıkça söylüyor — **düzeltildi**;
  - `TECHNICAL_GUIDE.md` §7 ve §16'daki "42 ms": aynı şekilde eski topluluk
    olarak etiketlendi — **düzeltildi**;
  - `TECHNICAL_GUIDE.md` §16'daki "bir işçi ~20 oturum": kaldırıldı, yerine
    §17'nin kâğıt üzerindeki hesabı ve `AUDIT.md` C-3'ün eski kod olduğu
    uyarısı geldi — **düzeltildi**. `AUDIT.md` C-3'ün kendisi tarihî bir
    kayıttır ve olduğu gibi durur;
  - `TECHNICAL_GUIDE.md` §16'daki "oturum kimliği dışında tanımlayıcı yok":
    §18 müşteri profilinin takma adını ve hukuki niteliğini anlatıyor —
    **düzeltildi**.
- **Bu turda doğrulananlar.** SDK boyutu (33.914 bayt, gzip 12.208), jeton
  ömrü (30 dakika), altı iç gerekçenin tek `step_up`a indirgenmesi, 1.440
  kombinasyonluk profil testi ve gecikme satırları koda ve yayımlanan
  ölçüm sayfasına karşı yeniden kontrol edildi.
- Bu dosya, yeni bir ölçüm yapıldığında güncellenecek.

**Son kontrol listesi**

- [ ] Her sayının yanında neyin üzerinde ölçüldüğü yazıyor (sentetik / betikli
  tarayıcı / sentetik kimlik).
- [ ] Metinde şu ifadeler kalmadı: "gerçek kullanıcı verisi", "kapalı beta", "%98",
  "%40", "Transformer", "AWS Lambda", "tüm kararlar SHAP ile", "anonim",
  "benzersiz parmak izi", "hesap askıya alınır".
- [ ] Ekran görüntüleri çalışan panodan alındı ve sentetik demo müşterileri etiketli.
- [ ] Kaynakça numaraları metindeki atıflarla eşleşiyor.
- [ ] Azerbaycan Türkçesi biçimleri ve yazım hataları düzeltildi.
