# Jüri Soruları — Teknik Cevaplar

## Soru

> İş ispatı ve `setTimeout` gecikme ölçümleri, SDK'yı hiç çalıştırmadan
> telemetri gönderen betikleri durduruyor. Peki arka planda gerçek bir tarayıcıyı
> (Puppeteer, Playwright) süren bir bot, önceden kaydedilmiş **gerçek insan**
> fare hareketlerini ve klavye ritimlerini enjekte ederse — yeniden oynatma
> değil, taklit — model topluluğunuz, özellikle de Isolation Forest, bunu nasıl
> tespit eder? Mevcut öznitelikler bu yeni taklit tekniklerini yakalamaya yeter
> mi?

## Kısa cevap

**Tespit edemez.** Bunu tahmin olarak değil, ölçüm olarak söylüyoruz: bu
saldırının daha zayıf bir sürümünü kendi sistemimize karşı çalıştırdık ve 25
oturumun 25'i de onaylandı. Cevabın tamamı aşağıda, çünkü *neden* tespit
edemediği, *edemediği* gerçeğinden daha önemli.

---

## 1. Isolation Forest bu saldırıda işe yaramaz — zarar verir (ve topluluktan çıkarıldı)

Isolation Forest yalnızca **insan** satırları üzerinde eğitiliyor
(`iso_forest.fit(human_only)`), yani "normal"i insan dağılımı olarak öğreniyor.
Topluluk, anomali skorunu `0.5 − decision_function` olarak hesaplıyor: bir nokta
ne kadar normalse, katkısı o kadar düşük.

Enjekte edilmiş gerçek insan izleri bu uzayda **aykırı değer değildir**. Tam
tersine, dağılımın en yoğun bölgesindedir — çünkü gerçekten oradan gelmişlerdir.
Sonuç:

| | Sonuç |
|---|---|
| Isolation Forest kararı | "en normal" |
| Anomali terimi | ≈ 0 |
| Eski toplulukta ağırlığı | %20 |
| Net etkisi | Risk skorunu **aşağı** çekiyordu |

Yani yüksek kaliteli taklide karşı Isolation Forest tarafsız biçimde
başarısız olmuyordu; saldırganın lehine oy kullanıyordu. Bu, ayarla düzelecek
bir şey değil, bileşenin tasarım amacının sonucudur: seyrek bölgeleri arar,
taklit ise yoğun bölgeye yerleşir.

### Bu cevabı yazdıktan sonra ölçtük ve bileşeni çıkardık

Yukarıdaki muhakeme doğruysa veride görünmesi gerekirdi; göründü. Eğitimde
hiç kullanılmamış **gerçek tarayıcı satırları** (n=82) üzerinde her bileşenin
tek başına ayrıştırma gücü:

| bileşen | ROC-AUC (2026-09-06, **geri çekildi**) | ROC-AUC (2026-09-25, düzeltilmiş veri) |
|---|---|---|
| Random Forest | 0.990 | 0.996 |
| LSTM | 0.898 | 0.871 |
| **Isolation Forest** | **0.340** | **0.657** |

> **Bu tablonun ilk sütunu geri çekilmiştir — sayfanın sonundaki "Isolation
> Forest dışarıda kaldı" bölümüne bakın.** 0.340 değeri, o tarihte donmuş
> laboratuvar satırları üzerinde ölçülmüştü; laboratuvar ham telemetriyle
> yeniden yakalanınca aynı model 0.657 veriyor. Yani bileşen **ters değil**.
> Yine de dışarıda: meşru akışların %38'ini, kaydedilmiş tek kişinin
> akışlarının %76'sını ek doğrulama çizgisinin üstüne koyuyor. Karar
> değişmedi, **gerekçe değişti** — ve ölçülmeyen bir gerekçe gerekçe değildir.

Aynı telemetri iki ağırlıklandırmadan geçirildiğinde (bu ölçüm de
düzeltmeden **önceye** aittir):

| persona | IsoF ile ortalama | IsoF'siz | AUC ile | AUC'siz |
|---|---|---|---|---|
| insan | 19.0 | **9.3** | — | — |
| bot_linear | 86.0 | **92.4** | 1.00 | 1.00 |
| bot_mimic | 25.0 | 16.7 | 0.83 | 0.84 |
| bot_adaptive | 13.2 | 3.0 | 0.00 | 0.02 |

Bileşen herkese — insana da bota da — aşağı yukarı aynı payı ekliyordu:
skorları şişiriyor, ayırmıyordu. Ödeme kapısında şişirilmiş bir **insan**
skoru pahalı olan hata türüdür.

Bu yüzden topluluk **0.6 RF + 0.4 LSTM** oldu (sonradan LSTM de ölçüm sonucu çıkarıldı; bkz. dördüncü soru). Model hâlâ eğitiliyor ve
pakette duruyor — karar 82 satıra dayanıyor ve daha fazla gerçek insan
kaydı biriktikçe yeniden ölçülmeli — ama istek başına çağrılmıyor.
Yan etki: bir flush'ı skorlama süresi 31.7 ms'den 17.7 ms'ye düştü.

Ve açıkçası: bu değişiklik `bot_adaptive`'i **çözmüyor** (0.00 → 0.02).
Sorunun kaynağı ağırlıklar değil, özniteliklerin kendisi — bkz. bölüm 2.

## 2. Öznitelik sayısı 6 değil, 12 — ve fazlası da yetmiyor

Sorudaki "6 öznitelik" bilgisi güncel değil; sistemde 12 öznitelik var. Ancak
sonradan eklenen 6 tanesi de bu saldırıyı çözmüyor.

**İlk 6 öznitelik** (varyans, entropi, tereddüt, tıklama yoğunluğu…) marjinal
istatistiklerdir. Taklit, tanımı gereği bunları eşler.

**Sonraki 6 öznitelik** — hız otokorelasyonu, yön tutarlılığı, zaman
kuantasyonu, duraklama dağılımı, tıklama öncesi hareket, kanal geçiş gecikmesi
— sentetik hareketin *motor yapısı olmadığını* yakalamak için eklendi. Örneğin
bağımsız Gauss gürültüsü insan gibi bir varyans üretir ama momentum üretmez.

Sorun şu: **yeniden oynatılan insan izinde o yapı zaten vardır.** Çünkü gerçek
bir elden gelmiştir. Bu öznitelikler tam da saldırının ücretsiz olarak sağladığı
özelliği ölçüyor.

## 3. Tek dikiş yeri: kanallar arası tutarlılık

`tiklama_oncesi_hareket` ve `kanal_gecis_gecikmesi` kanalların *birbirine* göre
davranışını ölçer:

- imleç tıklamadan önce hedefe vardı mı?
- el klavyeden fareye geçerken gerçek bir süre harcadı mı?

Saldırgan **bütün bir oturumu** tutarlı biçimde yeniden oynatırsa bunlar da
tutar. Ama kaydedilmiş bir fare izini sentetik tuş vuruşlarıyla birleştirirse,
ya da tek bir izi farklı zamanlamalı form doldurmalarda tekrar kullanırsa, ek
yeri görünür.

Bu dar bir açıktır ve yetkin bir saldırgan tam oturum kaydederek kapatır.
Dürüst olmak gerekirse: bunu bir savunma olarak değil, saldırganın yapması
gereken ek iş olarak sunuyoruz.

## 4. Temel sınır

Telemetri gerçekten insan dağılımından çekilmişse, **o telemetrinin hiçbir
fonksiyonu onu ayıramaz.** Bu bir modelleme eksikliği değil, bilgi-kuramsal bir
sınırdır. Sınıflandırıcı yalnızca vektörü görür; vektör insansa, karar insandır.

Ölçtüğümüz sayılar bu sınırı **hafife alıyor**. Kendi saldırı düzeneğimizdeki
`bot_mimic`, gerçek tarayıcı verisiyle eğitimden sonra AUC 0.92'ye çıktı — yani
ayırt edilebilir hale geldi. Ancak o bot insan hareketini **sentezliyor**.
Kaydedilmiş gerçek izler daha zordur ve ayırt edilebilirliği tekrar rastgeleye
(0.5) doğru iter.

## 5. Ne işe yarar

Cevap "daha çok davranış özniteliği" değil. Bildirilen koordinatların değil,
**giriş yığınının** özelliği olan sinyaller gerekir.

**a) Cihaz düzeyinde köken kanıtı.** En güçlü aday `getCoalescedEvents()`:
yüksek örnekleme hızlı gerçek bir fare, kare altı ölçümleri toplu halde iletir;
hata ayıklama protokolüyle gönderilen olaylar çağrı başına tek olay üretir.
Pointer basıncı ve `movementX/Y` tutarlılığı aynı fikrin daha zayıf sürümleri.

> Bunu henüz ölçmedik ve ölçmeden iddia etmiyoruz. Bu hafta kâğıt üzerinde iyi
> görünüp ölçümde başarısız olan üç mekanizmadan sonra, bu ihtiyat kazanılmış
> bir alışkanlıktır.

**b) Kayıt havuzunun tekrar kullanımını tespit etmek.** Saldırgan sonlu bir iz
kütüphanesinden besleniyorsa, oturumlar arasında **tekrar eder**. Davranışsal
kümeleme bir üretece karşı işe yaramaz (ölçtük: rastgeleleştiren bir bot 25
oturumda 30 farklı kova üretti), ama bir *kütüphaneye* karşı işe yarar. Kova
altyapısı bu yüzden kodda duruyor.

**c) Davranış dışı katmanlar.** Cihaz, ağ, hız kuralları, kart geçmişi. Her biri
tek başına aşılabilir; birlikte aşılması ucuz değildir.

## 6. Jüriye verilecek özet

> Bu saldırıyı model topluluğumuz yakalayamaz ve nedenini tam olarak
> biliyoruz. Telemetriyi istemci üretiyor; gerçek insan verisi enjekte edilirse
> vektör gerçekten insandır ve hiçbir öznitelik bunu değiştiremez. Isolation
> Forest bu durumda saldırganın lehine çalışır, çünkü normalliği insan dağılımı
> olarak öğrenmiştir.
>
> Bu yüzden DeepCheck'i tek başına ödeme kapısı olarak değil, cihaz kimliği,
> ağ, hız kuralları ve kart geçmişi ile birlikte **risk sinyallerinden biri**
> olarak konumlandırıyoruz. Betik tabanlı otomasyonu güvenilir biçimde
> durduruyoruz; yüksek kaliteli taklidi durdurmuyoruz ve bunu ölçtük.

Bu cevabın gücü, iddiada değil ölçümdedir: sistemi kendi bağımsız saldırı
düzeneğimizle test ettik, nerede başarısız olduğunu sayılarla biliyoruz ve
mekanizmasını açıklayabiliyoruz.

---

# İkinci Soru

## Soru

> Piyasada sizin yakalayamadığınız botları yakalayan sistemler var. O zaman
> sizin çözümünüz ne işe yarıyor? Boşuna uğraşılmış bir proje olduğunu kabul
> ediyor musunuz?

## Kısa cevap

**Hayır.** Gerekçesi aşağıda — teselli değil, karşılaştırma.

## 1. Ticari sistemlerin gerçekte sahip olduğu şey daha iyi bir model değil

Aradaki fark algoritma farkı değil, **veri ölçeği ve ağ konumu** farkı:

| Sistem | Asıl avantajı |
|---|---|
| DataDome | Müşteriler arası trilyonlarca sinyalden oluşan korpus |
| HUMAN (PerimeterX) | Yüzlerce siteye yayılmış cihaz parmak izi ağı |
| Cloudflare | İnternetin büyük bir kısmında **TLS sonlandırma noktasında** oturur; el sıkışmayı sizin sunucunuzdan önce görür |

Bunların hiçbiri "altı yerine on iki öznitelik kullanıyoruz" demiyor. Hiçbir
öğrenci ekibi trilyonlarca sinyallik bir ağı yeniden üretemez. Bu bir **kaynak
asimetrisidir**, mühendislik açığı değil.

## 2. Onlar da çözmüş değil

Bunun kanıtı ticari olarak sağlıklı bir **atlatma (bypass) endüstrisinin**
varlığıdır. Bu hafta yaptığımız araştırmada DataDome, Turnstile ve Kasada için
güncel 2026 atlatma rehberleri mevcut; Anubis'in savunma sorusu yayınlanmasından
aylar sonra tarayıcılar tarafından çözülmüştü.

Bu sistemler her şeyi yakalasaydı, o rehberler bir iş modeli olamazdı.
Ellerindeki şey **daha iyi bir oran ve daha hızlı yineleme**, çözüm değil.

## 3. Bu projenin gerçekten sağladığı

- **Betik tabanlı otomasyon her seferinde bloklanıyor**, test edilen her
  popülasyonda **%0 yanlış pozitif** ile. Kart deneme saldırılarının hacimli
  kısmı tam olarak budur.
- Modelin altında **gösteri değil üretim biçiminde bir uygulama katmanı** var:
  karar tarayıcıda değiştirilemez, telemetri yeniden oynatılamaz, karar için
  biriken kanıt gerekir, belirsizlik asla tahsil edilmez, jeton kanıt ister.

Bunların çoğu sınıflandırıcının kalitesinden **bağımsız olarak** ayakta kalır.

## 4. Nadir olan şey

Kendi eğitim verinizden bağımsız yazılmış bir saldırganla kendi
başarısızlığınızı ölçmüş ve mekanizmasını açıklayabiliyor olmak. Yarışmalarda
alışılmış olan, detektörle aynı yazarın yazdığı bir simülatörden çıkan doğruluk
oranını sunmaktır — bu proje tam olarak o tuzaktan çıktı.

## 5. Eksik olan bir veri problemi, çıkmaz değil

Detektör bugüne kadar **hiç insan görmedi**; öğrendiği bütün "insanlar" birer
betik. Gerçek tarayıcı verisiyle ilk kez eğitildiğinde, insanlaştırılmış botun
ayırt edilebilirliği tek eğitim turunda yazı-turadan **0.92'ye** çıktı. Sinyal
orada. Eksik olan, eşikleri dürüstçe yerleştirmeye yetecek kadar gerçek oturum.

## 6. Dürüst sonuç

Bu sistem satılmaya hazır mı? Hayır. Kendi sınırlarını bilen, ölçen ve bir
sonraki adımı net olan savunulabilir bir mühendislik projesi mi? Evet — ve bu
dürüstlük bir itiraf değil, bir varlıktır.

Savunmayacağımız sürüm, yakalayamadığını yakalıyorum diyen sürümdür. Elimizdeki
o sürüm değil.

---

# Üçüncü Soru — ve verdiğimiz cevap

## Soru

> Oturum ortasında devir teslim (hijack) olursa: Random Forest son pencereye
> bakıp %95 bot derken, LSTM oturumun başı insan olduğu için hâlâ düşük risk
> gösterirse, sabit ağırlıklı ansambl (0.5 RF + 0.2 IF + 0.3 LSTM) bu keskin
> çelişkiyi nasıl yönetiyor? Doğrusal ortalama yerine modeller arasındaki
> **uyuşmazlığı** tek başına bir risk sinyali olarak kullanmayı düşündünüz mü?

## Ölçüm

Soru haklıydı ve varsayımla değil deneyle cevapladık. Beş insan penceresi,
ardından beş otomasyon penceresi — aynı oturum, devir teslim 6. akışta:

| Akış | Kaynak | RF | LSTM | Harman | Yumuşatılmış | Karar |
|---|---|---|---|---|---|---|
| 5 | insan | 0.09 | 0.00 | 15.4 | 15.4 | allow |
| **6** | **BOT** | **0.99** | **0.01** | 61.2 | **15.4** | **allow** |
| 8 | BOT | 0.82 | 0.00 | 53.5 | 53.5 | warn |
| 10 | BOT | 0.99 | 1.00 | 91.0 | 61.2 | verify |

**Asıl suçlu ansambl ağırlıkları değildi.** 6. akışta harmanlanmış skor zaten
61.2 idi; medyan yumuşatma bunu 15.4'e bastırdı. Devir teslimden `verify`'a
kadar yaklaşık **10 saniye** geçiyordu.

Bir düzeltme: LSTM sorudaki gibi %15 demiyor, üç pencere boyunca **%0** deyip
sonra **%100'e** sıçrıyor. Yani ilk üç pencerede tahminden daha zararlı.

## Yapılan iki değişiklik

**1. Uyuşmazlık ayrı bir risk terimi.** d = |RF − LSTM| olmak üzere:

```
birleşik = (1 − d) × harman + d × max(RF, LSTM)
```

d ≈ 0'da eski harmanın aynısı, yani uyuşan oturumlar değişmiyor. d ≈ 1'de
telaşlı bileşen. Bilerek asimetrik: yalnızca yükseltir, indirmez.

**2. Yumuşatma keskin uyuşmazlıkta devre dışı.** Yumuşatma *tek* anormal
okumaya karşıdır; devir teslim tek okuma değil, seviye kaymasıdır. Artık
yumuşatma bir sıçramayı azaltabilir ama alarmı veren okumanın altına indiremez.

**Üçüncüsünü kurmadık.** Ayrı bir `max(RF, LSTM)` tavanı istenmişti; uyuşmazlık
formülü d → 1 iken zaten ona yakınsıyor (6. akışta 99.0'a karşı 99.4). İkisini
birden koymak aynı kanıtı iki kez saymak olurdu.

## Sonuç

| | 6. akışta karar |
|---|---|
| Önce | 15.4 → allow |
| + yumuşatma baypası | 61.2 → verify |
| + uyuşmazlık yükseltmesi | **99.0 → block** |

Devir teslim artık **ilk otomasyon penceresinde** yakalanıyor.

**Maliyeti ölçtük, çünkü bu kural bir model insan hakkında yanılınca da
tetiklenir.** 40 oturum, kural açık ve kapalı:

| Sınıf | Ortalama (kapalı → açık) | En yüksek | %0 eşik aşımı |
|---|---|---|---|
| insan | 19.5 → 19.7 | 32.3 → 36.7 | **%0 yanlış pozitif** |
| bot_linear | 85.3 → 85.3 | — | %100 blok |

Canlı API üzerinden de aynı sonuç: insan sınıfında **%0 yanlış pozitif**.

**Bunun çözmediği şey:** taklit. Insanlaştırılmış bot %0'dan %8 durdurmaya
çıktı — 12 oturumda 1, yani gürültü. Bu bir hijack düzeltmesidir, taklit
düzeltmesi değildir ve öyle sunmuyoruz.

## Sonradan: ansambl değişti, senaryo yeniden ölçüldü

Sorudaki "sabit ağırlıklı ansambl (0.5 RF + 0.2 IF + 0.3 LSTM)" artık
**0.6 RF + 0.4 LSTM**; Isolation Forest ölçüm sonucu skordan çıkarıldı
(gerekçesi 1. soruda). Ağırlıklar değiştiği için aynı hijack senaryosu
birebir tekrar çalıştırıldı:

| Akış | Kaynak | RF | LSTM | Uyuşmazlık | Harman | Birleşik | Karar |
|---|---|---|---|---|---|---|---|
| 5 | insan | 0.09 | 0.00 | 0.09 | 5.2 | 5.2 | allow |
| **6** | **BOT** | **0.99** | **0.01** | **0.99** | 59.9 | **99.0** | **block** |
| 8 | BOT | 0.82 | 0.00 | 0.82 | 49.3 | 76.3 | verify |
| 10 | BOT | 0.99 | 1.00 | 0.01 | 99.6 | 99.6 | block |

Devir teslim yine **ilk otomasyon penceresinde** bloklanıyor — uyuşmazlık
terimi RF ile LSTM'i okur, IF'i hiç okumuyordu, dolayısıyla düzeltme
ağırlık değişikliğinden etkilenmiyor. Değişen taraf insan penceresi:
5. akışta 15.4 yerine **5.2**. Yani blok eşiğine olan payı büyüdü,
yakalama hızı aynı kaldı.

**Sonradan (2):** LSTM de skordan çıkarıldı; gerekçesi dördüncü soruda. Devir
teslimi yakalayan şey zaten RF'ydi. Uyuşmazlık kuralının yaptığı iş artık
açık bir kuralla yapılıyor: son akışlar medyanının 35 puan üstüne sıçrama
yumuşatılmıyor. 185 simüle devir tesliminin 185'i ilk otomasyon akışında
yakalandı.

---

# Dördüncü Soru — Model seçimi ne kadar doğru?

## Soru

> Random Forest + LSTM seçimi nasıl gerekçelendirildi? Gradient boosting veya
> derin öğrenme neden değil? Rakipler ne kullanıyor?

## Ölçüm

> **ÖNEMLİ DÜZELTME (2026-09-25).** Bu bölümün önceki sürümü şunu yazıyordu:
> *"H1 insan senaryosu eğitimden çıkarıldığında LightGBM görülmemiş insanların
> %74'ünü blokladı, Random Forest hiçbirini bloklamadı."* **Bu sayı geri
> çekilmiştir.** O ölçüm, `lab/real_telemetry.json` içinde yalnızca
> *normalize edilmiş* vektör olarak saklanan 234 satır üzerinde yapılmıştı; o
> vektörler yakalandıkları andaki ölçekleme sabitlerine donmuştu ve aradaki
> yeniden eğitim o sabitleri değiştirmişti. Laboratuvar ham telemetriyle
> yeniden yakalandıktan sonra aynı protokol LightGBM için %4, RF için %0
> veriyor. **Sonuç değişmedi, ama gerekçe olarak gösterilen sayı yanlıştı** ve
> yanlış bir sayının doğru bir sonucu desteklemesi, ölçüm sayılmaz.

`backend/model_selection.py` sekiz model ailesini (RF, ExtraTrees,
HistGradientBoosting, LightGBM, XGBoost, lojistik regresyon, MLP ve tek-sınıflı
Isolation Forest) ve LSTM varyantlarını aynı bölmelerle karşılaştırıyor. Altı
protokol var; kararı verenler ikisi:

* **D — bir senaryoyu tamamen dışarıda bırakma.** Gerçek müşteri de, ciddi bir
  saldırı da tanım gereği modelin görmediği davranıştır.
* **R — bir *kişiyi* tamamen dışarıda bırakma.** Kaydedilmiş tek gerçek kişinin
  akışları, o kişiyi hiç görmemiş bir modelle skorlanıyor ve `/api/decision`
  hangi değeri okuyorsa o değere (yumuşatılmış oturum skoru) kadar götürülüyor.

Ölçüm: 301 gerçek satır (46 koşuda 252 betikli tarayıcı akışı + tek kişiden 49
akış) + 6.400 simüle oturum.

| | ROC-AUC | Bot ≥.8 | Görülmemiş insan ≥.6 / ≥.8 | Görülmemiş A2 botu ≥.8 | R: tek kişinin oturum skoru |
|---|---|---|---|---|---|
| **Random Forest** | 0.996 | 0.81 | **0.05** / **0.00** | 0.00 | 40.9 |
| ExtraTrees | 0.998 | 0.92 | 0.63 / 0.00 | 0.00 | 46.2 |
| HistGradientBoosting | 0.995 | 0.95 | 0.15 / 0.02 | 0.00 | 97.9 |
| LightGBM | 0.994 | 0.93 | 0.30 / 0.04 | 0.00 | 39.2 |
| XGBoost | 0.996 | 0.94 | 0.14 / 0.00 | 0.00 | 72.6 |
| Lojistik regresyon | 0.982 | 0.79 | 0.42 / 0.14 | 0.12 | 0.0 |
| MLP | 0.996 | 0.92 | **0.04** / 0.02 | **0.75** | 1.1 |
| Isolation Forest | 0.657 | 0.45 | 0.89 / 0.45 | 0.00 | 97.6 |

**Gradient boosting gerçekten daha çok bot yakalıyor** ve bu gürültü değil:
tpr@0.8 üzerinde +0.11 ila +0.15, 47 gerçek grubun bootstrap'ı sıfırı dışarıda
bırakıyor. Laboratuvardaki ek yanlış-işaret bedeli (fpr@0.6 üzerinde +0.014 ila
+0.020) ise **gürültünün içinde**.

**Rahatsız edici sütun sonuncudan bir öncekidir ve suçladığı bizim modelimiz.**
Rastgeleleştirilmiş bot senaryosu eğitimden çıkarıldığında Random Forest onun
**hiçbirini** yakalamıyor; aynı on iki öznitelik üzerinde sıradan bir MLP
%75'ini yakalıyor. Bu tek bir şanslı tohum değil: beş farklı `random_state` ile
yeniden eğitildiğinde RF her seferinde 0.00, MLP her seferinde 0.75 veriyor
(naif bot senaryosunda RF 0.57, MLP 1.00 — yine her tohumda).

**Peki MLP neden benimsenmedi?** Aynı tohum taraması nerede kararsız olduğunu da
gösteriyor: görülmemiş **klavye-ağırlıklı meşru kullanıcı** senaryosunun ek
doğrulamaya düşen oranı beş tohumda 0.00 / 0.00 / 0.07 / 0.16 / 0.33, kaçamaklı
saldırı ailesindeki yakalama oranı ise 0.06 / 0.12 / 0.33 / 0.44 / 0.61. RF'nin
karşılık gelen aralıkları 0.00–0.05 ve 0.00–0.00. Ödeme kapısında, meşru
kullanıcıyı yanlış işaretleme oranı `random_state`'e göre yazı-tura olan bir
model tutulamaz; en iyi görünen tohumu seçmek ise bu projenin dürüstlük
kuralının tam olarak engellemek için var olduğu harekettir. Tohum ortalaması
almak veya topluluk kurmak bariz bir sonraki deney — ama ölçülebilmesi için
gerçek insan lazım, bir laboratuvar koşusu daha değil.

**Tek gerçek kişi de beraberliği bozamıyor:** kişi bazında dışarıda
bırakıldığında yedi model aynı oturuma 0.0, 1.1, 39.2, 40.9, 46.2, 72.6 ve 97.9
veriyor — üç "geçir", üç "ek doğrulama", bir "blok". Bir kişi, bir makine, bir
oturum, yedi karar.

**LSTM skordan çıkarıldı — ve yeniden yargılandıktan sonra dışarıda kaldı.**
Eskiden gönderildiği biçimde (yalnızca simüle tam diziler) gerçek satırlarda
ROC-AUC 0.848, Brier 0.440. **Sunum yolunun gerçekten ürettiği biçimde** —
`build_sequence()` ilk akıştan itibaren dolgulu önek üretir — 0.871'e çıkıyor,
Brier 0.424: sıralaması kalibrasyonundan çok daha iyi. Gerçek satırlar da
harmanlandığında kalibrasyon düzeliyor (Brier 0.113) ama sıralama farkı
kapanmıyor (RF 0.996). Var olma sebebi olan **devir teslimde** hâlâ kaybediyor:
6. akışta el değiştiren oturumlarda, yalnızca o anki akışa bakan RF %100'ünü
6. akışta yakalıyor; dolgulu-önek LSTM %63'ünü (bir akış sonra %100), eski
biçimdeki LSTM ise 10. akışa kadar hiçbirini. İkisi de tek bir saf insan
oturumunu yükseltmiyor. RF'ye geçmiş akışları öznitelik olarak vermek de hiçbir
şey katmıyor (0.996'ya karşı 0.996).

**Isolation Forest dışarıda kaldı — ama eski gerekçe geri çekildi.** Eskiden
*ters* olduğu için reddedilmişti: gerçek tarayıcı satırlarında ROC-AUC 0.340,
yani rastgeleden de kötü, sistematik olarak saldırgandan yana oy veriyordu.
Düzeltilmiş veride artık ters değil (0.657). Sadece önemli olan yönde işe
yaramıyor: meşru akışların %38'ini, kaydedilmiş kişinin akışlarının %76'sını ve
görülmemiş H1 insan akışlarının %87'sini ek doğrulama çizgisinin üstüne
koyuyor; kayıtlı kartla yapılan bir ödemeye RF'nin 0.8 verdiği yerde 54.0
veriyor. Tek-sınıflı bir dedektörün burada başka türlü olması da mümkün değil:
"normal"i insan dağılımı olarak öğreniyor, bu üründe ise **saldırı zaten insana
benziyor**.

**Bedeli, ölçüldüğü haliyle:** LSTM meşru skorları da bastırıyordu. Karar
katmanından geçirilen tam form doldurmalarında:

| Simüle kullanıcı | Önce | Sonra |
|---|---|---|
| Tipik kullanıcı, doğrudan onay | %95.0 | %89.4 (kalanı ek doğrulama) |
| Yavaş yazan kullanıcı, **blok** | %5.2 | **%0.2** |

Yani daha az müşteri reddediliyor, daha çok müşteriden ek doğrulama isteniyor.
(Önce/sonra karşılaştırması, değişiklik anında önceki sürümle — `705a63f` —
`benchmark.py` form doldurma üreteciyle, kişi başına 500 oturumla ölçüldü.)

**Asıl sınır model ailesi değil.** Senaryo dışarıda bırakıldığında ağaç
modellerinin hiçbiri A2/A3/A4 saldırılarının anlamlı bir kısmını yakalamıyor
(RF: A2 %0, A3 %0, A4 %2 — ek doğrulama çizgisinde). Görülmemiş saldırıyı
yakalayan modeller ise görülmemiş insanı da, tohuma bağlı olarak %33'e varan
oranda işaretliyor. Darboğaz model ailesi değil, **sinyal ve veri**: bu ödünleşi
çözecek olan şey daha iyi bir topluluk değil, kişi bazında ayrılabilecek kadar
çok gerçek kayıt (20–30 kişi, kişi başına birkaç oturum) ve bir de dokunmatik
yüzey, dokunmatik ekran, kalem ve kısıtlanmış oturum kayıtları — bunların
hiçbiri bugüne kadar bir kez bile kaydedilmedi.

## Rakipler ne yapıyor

- **Cloudflare:** Bot Management motorlarının belgelerinde fare/klavye
  davranışı yok. Heuristik parmak izleri, JA3/JA4 TLS parmak izi, istek
  özellikleri üzerinde ML ve müşteri başına trafik tabanına göre anomali
  tespiti kullanıyor.
- **DataDome:** Sunucu tarafı (HTTP/TLS parmak izi, IP itibarı) ile istemci
  tarafı sinyalleri (fare, dokunma, sensör) birleştiriyor. Her site için
  ayrı model eğitiyor ve tarayıcıda WASM tabanlı bir sınama çalıştırıyor.
- **Kasada:** Her yüklemede değişen, gizlenmiş bir JavaScript sanal
  makinesinde iş ispatı kullanıyor. Amaç, istemci kodunun taklit edilmesini
  pahalı kılmak.
- **BioCatch, NuData (Mastercard), BehavioSec (LexisNexis):** Davranışsal
  biyometriyi cihaz ve ağ zekâsıyla birleştiriyor. BehavioSec, geri dönen
  kullanıcıyı **kendi geçmiş profiliyle** karşılaştırıyor.
- **reCAPTCHA Enterprise:** Eylem başına eşik ve gerekçe kodları sunuyor.
  Sitenin gerçek sonuçları geri bildirimle modele işleniyor.

Ortak nokta şu: **hiçbiri yalnızca davranış skoruna dayanmıyor.** Hepsi
kimlik/cihaz/ağ sinyali, istemci bütünlüğü ve gerçek trafikten gelen geri
bildirim döngüsüyle çalışıyor. Akademide de benzer: BeCAPTCHA-Mouse'ta
Random Forest, LSTM ve GRU'yu geçti. El yapımı öznitelikler ise istatistiksel
taklide karşı zayıf kaldı. DMTG gibi difüzyon tabanlı üreteçler insan benzeri
imleç yolları üretiyor. Bu, DeepCheck'in kendi taklit ölçümüyle aynı sonucu
söylüyor.

---

# Demo Prosedürü — Sentetik demo müşterileri

> **Önce bunu söyleyin.** Jüri üyesinin sentetik bir müşteri adına ödemesi
> katmanın **mekanizmasını** gösterir, gerçek kişilerdeki doğruluğunu değil.
> Gerçek bir kişi, tanımı gereği hiçbir simülatör kimliğine benzemez; bu yüzden
> ek doğrulama beklenir ve bu beklenti bir başarı ölçümü değildir. Gerçek
> müşterilerde yanlış sorgulama ve yakalama oranı ölçülmedi.

## Neden sentetik müşteri

Profil katmanı bir müşteriyi yalnızca **kendi** geçmişiyle ve aynı giriş
türünde (fare, klavye, dokunmatik) karşılaştırır. Bunun için en az **19
referans oturum** gerekir. Bu sayı ayarlanmadı, aritmetikten geliyor: n
referansla ulaşılabilecek en küçük p-değeri 1/(n+1)'dir, yani 0.05 düzeyinde
19'un altında katman hiç soru soramaz. Bir profile günde en fazla **3** oturum
öğretilir, dolayısıyla gerçek bir müşteri en erken 7 günde olgunlaşır. Ekibin
bir müşteri tabanı yok.

Bu yüzden prototip üç **sentetik demo müşterisiyle** çalışır: Ayşe, Mehmet ve
Zeynep. Her biri simülatörün bir kimliğidir (`train_model.simulate_identity_sessions`).
Her birine **20 fare + 20 klavye** oturumluk bir geçmiş yüklenir. Geçmiş,
canlı bir oturumun geçtiği yoldan geçer: her akış `/api/analyze` gövdesi gibi
doğrulanır, `compute_risk` ile ölçülür ve öğrenme yolu (`_learn_session`) ile
kaydedilir. Müşteriler ayrılmış `demo` satıcı ad alanında durur ve hiçbir
gerçek satıcının müşterisiyle eşleşemez.

## Sentetik veri nerede ve nasıl işaretlenir

| Yer | İşaret |
|---|---|
| Veritabanı | `customer_profiles.is_synthetic`, `customer_profile_vectors.is_synthetic`; sentetik bir profille verilen her karar için `decision_audit.is_synthetic`; `--simulate` oturumları için `sessions.is_synthetic` |
| Müşteri referansı | `sentetik-ayse`, `sentetik-mehmet`, `sentetik-zeynep` |
| Referans oturum kimlikleri | `sentetik-ayse-mouse-01` … (böyle bir oturum hiç yaşanmadı) |
| Demo sayfası | Seçicide "Ayşe — sentetik geçmiş (fare 20, klavye 20)" ve altında "Ayşe, Mehmet ve Zeynep sentetik demo müşterileridir…" satırı |
| SOC panosu | "Sentetik demo verisi" rozeti: simüle edilmiş oturumlarda ve sentetik profille verilen kararlarda. Metrik kartları simüle edilmiş oturumları saymaz ve kaç tanesinin dışarıda kaldığını yazar |
| Ölçümler | Hiçbirine girmez. `profile_lab.py` sunulan veritabanını açmaz. `record_session.py` simüle edilmiş bir oturumu değerlendirme kümesine yazmaz. `backend/test_demo.py` ikisini de denetler |

Sentetik bir müşteriye **hiçbir oturum öğretilmez**: karşılaştırma için
kullanılır ama gerçek kişinin oturumu onun geçmişine eklenmez. Aksi hâlde
"sentetik" denen bir profilin içinde gerçek bir kişinin davranış verisi
dururdu.

## Hazırlık — yalnızca demo makinesinin `.env` dosyası

Ürünün varsayılanı katmanın kapalı olmasıdır ve öyle kalır.

```
DEEPCHECK_PROFILE_KEY=<python -c "import secrets; print(secrets.token_urlsafe(32))">
DEEPCHECK_MERCHANT_KEYS=ornek-satici:<ikinci, farklı bir token>
PROFILE_LAYER=1
PROFILE_ESCALATION=1
DEMO_ENDPOINTS=1
```

- Satıcı anahtarı demo ödemesinde kullanılmaz, ama katman en az bir satıcı
  tanımlı olmadan açılmaz. `demo` kimliği ayrılmıştır, bir satıcıya verilemez.
- `PROFILE_ESCALATION=1` **yalnızca bu demo içindir**. Zorunlu mod, gerçek
  bir kişi farklı oturum ve cihazlarda ölçülmeden üründe açılmamalıdır.
  `0` bırakılırsa pencere açılmaz, ama pano sapmayı yine gösterir.

## Kurulum — tek komut

```bash
docker compose up -d --build
docker compose exec backend python demo_seed.py
```

Çıktının her satırı "SENTETİK demo müşterisi" der ve iki giriş türü için
`20/20` gösterir. Komut tekrar çalıştırılabilir: sağlam bir müşteriye
dokunmaz. Durumu görmek için `--status`, baştan kurmak için `--reset`
kullanılır. `--reset` ayrıca sorgulama bütçelerini sıfırlar. Yükleme bu
dizüstü bilgisayarda yaklaşık 20 saniye sürdü.

## Jüri önünde

1. SOC panosu (`/dashboard`) ayrı bir sekmede açık tutulur.
2. Demo sayfası **yeniden yüklenir**; her yükleme yeni bir oturumdur.
   "Demo Müşterisi" seçicisinden bir sentetik müşteri seçilir (ör. Ayşe).
   Altındaki referans alanında `sentetik-ayse` görünür.
3. Jüri üyesi kart bilgilerini **fareyle** alanlara tıklayarak doldurur ve
   hemen Onayla'ya basar. Klavyeyle ya da telefonla ödeme için aşağıdaki
   sınırlara bakın.
4. Beklenen sonuç doğrulama kodu penceresidir. Sayfa nedenini söylemez:
   istemciye giden gerekçe, diğer kontrollerle aynı genel `step_up`'tır.
   Risk skoru ve etiket değişmez.
5. **Kod girilmeden önce** panoda bu oturum seçilir. "Müşteri Profili"
   kartında şunlar görünür: "Sentetik demo verisi", "Değerlendirildi",
   "Ek doğrulama istendi", "Fare: 20 / 19 — Olgun", p-değeri **0.0476** ve en
   çok sapan üç özellik. 0.0476, 20 referansla ulaşılabilecek en küçük
   p-değeridir (1/21). Yani bu oturum, geçmişteki 20 oturumun **hepsinden**
   daha uçtur. Kartta "Ek doğrulama istendi" yoksa pencere profil yüzünden
   değil, skor gibi başka bir kontrol yüzünden açılmıştır.
6. Jüri üyesi kodu girer ve ödeme alınır. Katman yalnızca ek doğrulama
   ister: engellemez, onaylamaz, skoru değiştirmez. Bu, "torun büyükannesi
   adına ödüyor" durumudur: sapma bir hüküm değil, kanıt istemek için bir
   nedendir.
7. **Karşıt örnek:**
   `docker compose exec backend python demo_seed.py --simulate ayse`.
   Aynı sentetik kimlikten **yeni** bir oturum, gerçek HTTP yolundan geçer:
   oturum açma, iş kanıtı, 10 `/api/analyze` akışı ve ödeme. Çıktı
   "SİMÜLE EDİLMİŞ OTURUM — gerçek bir kişi değil" başlığını taşır. Beklenen
   sonuç "ek doğrulama istenmedi" ve büyük bir p-değeridir. Panoda bu oturum
   "Sentetik demo verisi" rozetiyle görünür ve metrik kartlarına katılmaz.
   Aynı kimlik bile ara sıra sorgulanır: sentetik kimliklerde bu oran %4.9'dur
   ve çıktı böyle bir durumda bunu yazar.

## 2026-09-19 doğrulaması

Yalıtılmış bir Docker yığınında yapıldı: `DEBUG=0`, zorunlu mod ve sunulan
model. "Jüri üyesi" satırları gerçek demo sayfasında, laboratuvarın insan
hareket modeliyle (`lab/bot_lab.py`) sürülen bir Playwright oturumudur. Bu bir
kişi değil, bir betiktir.

| Oturum | Oturum skoru | Karar | Profil katmanı |
|---|---|---|---|
| Jüri üyesi betiği, Ayşe | 29.5 (Gerçek Kullanıcı) | ek doğrulama; kodla ödeme alındı | değerlendirildi, fare, 20 referans, p 0.0476 |
| Jüri üyesi betiği, Mehmet | 23.0 (Gerçek Kullanıcı) | ek doğrulama | değerlendirildi, fare, 20 referans, p 0.0476 |
| `--simulate ayse` (fare) | 0.0 | ödeme alındı | değerlendirildi, p 0.6667 |
| `--simulate mehmet` (fare, 2 kez) | 0.0, 0.0 | ödeme alındı | değerlendirildi, p 0.7143 / 0.8095 |
| `--simulate zeynep --modality keyboard` (2 kez) | 0.7 (ilk çalıştırma) | ödeme alındı | **karşılaştırma yapılmadı** (olgunlaşmadı) |

Aynı konteynerde, her müşterinin kendi kimliğinden giriş türü başına 30 yeni
oturum daha ölçüldü. Bunlar HTTP yolundan değil, kararın kullandığı aynı
istatistikle (`profiles.evaluate_profile`) yüklenen geçmişe karşı
değerlendirildi:

- **Fare:** 90 oturumun 88'i karşılaştırıldı ve geçti, 2'si sorgulandı.
- **Klavye:** karşılaştırılan oturum sayısı Ayşe'de 30/30, Mehmet'te 20/30,
  Zeynep'te 0/30 oldu. Klavye oturumu 12 özelliğin yalnızca 6–7'sini ölçer.
  Karşılaştırılabilir referans ya da özellik yetmediğinde katman tahmin
  yürütmez, susar.

> **Sentetik kimlikler ve bir tarayıcı betiği üzerinde ölçüldü; gerçek
> müşteri verisi yoktur.** Bu tablo bir doğruluk ölçümü değil, mekanizmanın
> uçtan uca çalıştığının kaydıdır. İki jüri üyesi betiğinin ikisinin de
> sorgulanması, gerçek tarayıcı telemetrisinin simülatörden ayrışmasının
> sonucudur (bkz. `lab/README.md`), bir yakalama oranı değildir.

## Sınırlar ve prova kuralları

- **Giriş türü.** Fareyle ödeyin. Klavyeyle ödeme gösterilecekse Ayşe
  seçilir. Zeynep'in klavye geçmişiyle karşılaştırma yapılamıyor ve bu,
  katmanın kanıt yetersizken susmasını göstermek için kullanılabilir.
  Telefonda (dokunmatik) sentetik geçmiş yoktur, katman karşılaştırma
  yapmaz.
- **Sorgulama bütçesi yalnızca geçilen sorgulamaları sayar.** Bir müşteri 30
  gün içinde **3** ek doğrulamayı doğru kodla geçtiyse, profil o pencerede bir
  daha sormaz ve kart "Sorgulama bütçesi doldu" der. Kod girilmeden kapatılan
  pencere hak harcamaz. Önceden her sorgulama, yanıtlanmasa bile, bir hak
  harcıyordu. Bu yüzden kodu bilmeyen bir saldırgan Onayla'ya dört kez basınca
  dördüncüde ödeme geçiyordu; bu açık kapatıldı. Sentetik müşteriler öğrenmediği
  için geçilen sorgulama onlara kaydedilmez ve gösterim sayısı bütçeyle sınırlı
  değildir. `demo_seed.py --reset` jüriden önce yine de çalıştırılabilir.
- **Karar sınırı.** Bir müşteri adına bir saatte işçi başına 60 ödeme kararından
  sonra (varsayılan 2 işçiyle 120) o saat içinde profil okunmaz ve o müşterinin
  her ödemesi, zorunlu modda, ek doğrulamaya gider. Kart bunu "Bu müşteri için karar
  sınırı aşıldı — profil okunmadı" diye gösterir. Sınır bir müşterinin davranış
  zarfının deneme yanılmayla taranmasını önler. Diğer müşterileri etkilemez ve
  ödemeyi hiçbir zaman hata ile reddetmez. Bir provada aynı sentetik müşteriyle
  bu kadar ödeme yapılmaz; kalabalık bir stantta `demo-musteri-1` için olabilir.
- **Demo verisi 24 saat tutulur.** Demo ad alanında ziyaretçiden kalan her şey
  (öğrenilen vektör, demo profili, karar kaydı) oturumla birlikte 24 saatte
  silinir. Sayfadaki "KVKK Aydınlatma Metni" bağlantısı `docs/kvkk-aydinlatma.md`
  taslağını uygulamanın içinde (`/kvkk`) açar; internet bağlantısı gerekmez.
- **Karar anı.** Form doldurulunca beklemeden ödenir. Ödemeden sonra sayfa
  açık kaldıkça oturum skoru değişebilir: doğrulamada bir oturumun skoru
  ödemeden sonra 29.5'ten 78.8'e çıktı. Ödeme kararı ise verildiği andaki
  kayıtla SOC'ta durur.
- **Sentetik müşteri öğrenmez.** Bu yüzden deneme (probation) kaydı ve üst
  üste 3 başarılı doğrulamayla kendi kendini onarma sentetik müşterilerle
  gösterilmez. Bu kurallar gerçek profiller için geçerlidir ve
  [`docs/profile-evaluation.md`](profile-evaluation.md) §7'de sentetik
  kimliklerle ölçülmüştür.
- **Anahtar ve veritabanı.** `DEEPCHECK_PROFILE_KEY` değişirse ya da
  `docker compose down -v` çalıştırılırsa sentetik müşteriler kaybolur.
  `demo_seed.py` yeniden çalıştırılır.

## Beklenti — dürüst hali

Ne sıklıkla sorgulandığına dair elimizdeki tek sayılar sentetiktir
([`docs/profile-evaluation.md`](profile-evaluation.md) §5 ve §9, fare, 20
referans, tam konformal sıralama):

| | Oran |
|---|---|
| Farklı sentetik kişi ek doğrulamaya düştü | %47.5 (%95 GA %44.0–50.8) |
| Aynı sentetik kişi yanlışlıkla sorgulandı (**alt sınır**) | %4.9 (%95 GA %4.0–5.9) |

> **Sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur.** Tek
> bir gerçek kişinin çok sayıda oturumu henüz ölçülmedi. Bu demo da bir ölçüm
> değildir: sentetik müşteriler ve onlarla verilen kararlar `is_synthetic`
> olarak işaretlenir ve raporlanan hiçbir ölçüme katılmaz.

## Bu demo neyi gösterir, neyi göstermez

**Gösterir:**

- Katmanın tek çıktısı ek doğrulamadır; skor, etiket ve engelleme değişmez.
- Gerekçe istemciye söylenmez, yalnızca SOC panosunda ve denetim kaydında
  durur.
- Doğru kodu giren kişi ödemeyi tamamlar.
- Profili oluşturan kimliğin yeni bir oturumu sorgulanmadan geçer.

**Göstermez:**

- Gerçek müşterilerde yanlış sorgulama ve yakalama oranını; bunlar ölçülmedi.
- Kodu oltalama ile ele geçirmiş saldırgana karşı korumayı. Katmanın gücü,
  satıcının ek doğrulama kanalının gücü kadardır; demoda kod sayfada yazılıdır.
- Kart deneme botlarına karşı bir etkiyi. Müşteri referansı olmayan misafir
  ödemesi bu katmana hiç ulaşmaz.

---

# Beşinci Soru — Veri toplama, verimlilik, entegrasyon ve eşzamanlılık

## Soru

> Müşteri verisi nasıl toplanıyor da sistem müşteriyi tanıyor? Bu kadar veri
> sunucuyu yormuyor mu, maliyeti artırmıyor mu? Bu SDK her tür e-cüzdana
> entegre edilebilir mi? Aynı anda çok sayıda istek gelirse sunucu çöker mi?

Dördü de aşağıda ayrı ayrı cevaplanıyor. Sayıların hepsi ölçümden geliyor;
ölçülmemiş olan her yerde "ölçülmedi" yazıyor.

> **Bu bölümdeki hiçbir sayı gerçek müşteri verisinden gelmiyor.** Profil
> katmanının oranları sentetik kimliklerle, tarayıcı ölçümleri betikle
> yürütülen Playwright trafiğiyle ölçüldü.

## 1. Müşteri verisi nasıl toplanıyor, sistem müşteriyi nasıl tanıyor?

**Toplanan şey davranış, kimlik değil.** SDK sayfada çalışırken 2 saniyede bir,
son 10 saniyelik pencereyi gönderir (`sdk/deepcheck.js`): işaretçi hareketi,
tıklama zamanlaması, kaydırma, tuş **basma anları** (hangi tuş olduğu asla),
odak kayıpları. Tuş içeriği, alan içeriği, DOM ve pano okunmaz. IP adresi
veritabanına yazılmaz; uvicorn ve nginx erişim kayıtları kapalıdır
(`backend/entrypoint.sh`, `frontend/nginx.conf`).

**Akıştan 12 öznitelik çıkarılır** (`backend/lstm_model.py` `FEATURE_NAMES`) ve
Random Forest her akışı skorlar. Buraya kadarı müşteriyi tanımaz; yalnızca
"bu davranış insana mı benziyor" sorusunu cevaplar.

**Müşteriyi tanıyan kısım ayrı bir katmandır ve takma adla çalışır.** Satıcı
ödeme kararını sorarken kendi müşteri referansını gönderir. DeepCheck bu
referansı saklamaz: ondan `HMAC-SHA256(satıcı, referans)` ile bir `profile_id`
türetir (`backend/profiles.py`, `derive_profile_id`). Ham referans hiçbir
tabloda, hiçbir yanıtta ve hiçbir kayıt satırında bulunmaz.

**Geçmiş nasıl birikiyor.** Yalnızca **kanıtın tek başına onayladığı** bir
oturum öğrenilir (`allow`, gerekçe `score`). Oturumun 12 özniteliğinin medyanı
bir vektör olarak saklanır. Girdi türü başına (fare / klavye / dokunmatik) en
fazla **20 referans** ve ayrıca **4 deneme (probation)** vektörü tutulur; günde
en fazla 3 oturum öğrenilir.

**Ne zaman karşılaştırmaya başlar.** `PROFILE_ALPHA = 0.05` seçildiği için
olgunluk eşiği hesapla çıkar: 1/(n+1) ≤ 0,05 ⇒ **n ≥ 19 referans**
(`backend/profiles.py`). Bunun altında katman hiçbir şey söylemez.

**Sapma ne yapar — ve ne yapmaz.** Bu, ürünün en önemli kuralıdır: sapma
**asla** bir dolandırıcılık hükmü değildir. Katmanın yapabildiği tek şey,
`allow` veya `warn` olan bir kararı `verify`ye çevirmektir. Skoru, etiketi ve
engelleme kararını değiştiremez; hiçbir koşulda `block` üretemez. Torununa
ödeme yaptıran yaşlı müşteri engellenmez, **ek doğrulama istenir**; doğru kodu
girince ödeme tamamlanır ve o oturum deneme vektörü olarak saklanır.

**Hukuki taraf.** Profil, KVKK anlamında **özel nitelikli (biyometrik)** veridir
ve yalnızca açık rızayla, satıcı tarafından açılır
(`POST /api/profile/consent`). Silme ve itiraz için `POST /api/profile/erase`
vardır; silme, takma adı denetim tablolarından da düşürür. Saklama süresi 180
gün hareketsizliktir. Demo sayfasında bırakılan her şey **24 saatte** silinir.
Ayrıntı: [`docs/kvkk-aydinlatma.md`](kvkk-aydinlatma.md),
[`docs/dpia.md`](dpia.md).

**Jüriye tek cümle:** sistem müşteriyi bir kimlikten değil, satıcının verdiği
takma addan tanır; öğrendiği şey o takma ada bağlı **en fazla 24 küçük sayı
vektörüdür**; ve bu geçmişin yapabildiği tek şey ek doğrulama istemektir.
Toplamanın teknik ayrıntısı [`TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §18,
satıcı tarafındaki akış [`entegrasyon.md`](entegrasyon.md) §5'tedir.

## 2. Bu kadar veri sunucuyu yormuyor mu? Maliyet?

Hayır — ve nedenini aritmetikle gösterebiliyoruz.
[`TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §17 bu hesabın tamamıdır.

**Saklanan veri küçüktür.** Saklanan şey ham telemetri değil, 12 sayıdır. Ham
bloklar **1 saat** sonra boşaltılır, satırlar **24 saatte** silinir. Uzun süre
saklanan tek şey küçük ve sınırlıdır: karar başına bir denetim satırı (90 gün)
ve müşteri başına, girdi türü başına en fazla 24 vektör.

**Yük, işlem sayısıyla değil, eşzamanlı ziyaretçi sayısıyla artar.** SDK sayfa
açık olduğu sürece 2 saniyede bir gönderir, yani bir aktif oturum saniyede
**0,5 istek** demektir; 60 saniyelik bir ödeme ≈ **30 akış + 1 karar**.

**İstek başına maliyet** (ölçüm satırları: `docs/profile-evaluation.md` §11,
konteyner, Linux, n=300; ve `backend/scorer.py`):

| yol | değer | türü |
|---|---|---|
| bir akışın skorlanması (`compute_risk`) | 17,7 ms | **ölçüm** |
| `/api/decision`, profil katmanı kapalı | p50 5,0 / p95 7,4 ms | **ölçüm** |
| `/api/decision`, profil katmanı açık (gölge) | p50 24,0 / p95 34,8 ms | **ölçüm** |
| `/api/decision`, profil ek doğrulama istiyor | p50 16,0 / p95 21,8 ms | **ölçüm** |
| `/api/analyze` uçtan uca | ~23 ms | **hesap** (17,7 skorlama + 5,0 istek yolu) |

`/api/analyze` tek başına hiç ölçülmedi; bu kurgu, aşağıdaki hesabın en büyük
hata kaynağıdır ve öyle söylenmelidir.

**Çekirdek başına kaç kullanıcı — aritmetik:**

```
  1000 ms / 23 ms          = 43 analiz/sn/vCPU
  43 / 0,5 (oturum başına)  = ~87 eşzamanlı aktif ödeme oturumu/vCPU
```

Yöntemin tek gerçek eşzamanlılık ölçümüne uzaklığı **%2,4**: `AUDIT.md` C-3
(eski üç modelli topluluk, istek başına ~74 ms, tek işçi) 20 eşzamanlı çağrıda
13,2 istek/sn ölçtü; aynı hesap 1000/74 = 13,5 verir. Bu, eski kodda tek bir
noktadır — bugünkü sayının doğrulaması değil, yöntemin neden alıntılandığının
gerekçesidir.

**Ödeme başına maliyet — aritmetik.** 30 × 23 ms + 1 × 24 ms = **0,71
CPU-saniye** ⇒ bir vCPU-saat ≈ **5.000 ödeme** (3600 / 0,71). Bir bulut
fiyatı yazmıyoruz, çünkü satın almadık: ödeme başına çıkarım maliyeti, sizin
vCPU-saat fiyatınızın **1/5000**'idir. Profil katmanı bu 0,71'in içindedir,
üstüne değil: otuz bir istekten birine 19 ms ekler, yani **+%2,7**.

**Depolama.** Satır boyutları Postgres 16'da ölçüldü (`pg_total_relation_size`,
indeksler dâhil); bölme ve çarpma işlemleri aritmetiktir:

| ne | boyut | türü |
|---|---|---|
| bir akış satırı, ham telemetriyle | 6,1 kB | ölçüm |
| aynı satır, 1 saatlik boşaltmadan sonra | 644 B | ölçüm |
| bir profil vektörü | 1.363 B | ölçüm |
| bir ödeme, ilk saat (30 akış) | 183 kB | hesap |
| aynı ödeme, 1.–24. saat | 19 kB | hesap |
| bir müşteri profili, üst sınır (20 + 4 vektör, tek girdi türü) | 32,7 kB | hesap |
| **1.000.000 müşteri, tek girdi türü** | **32,7 GB** | hesap |
| 1.000.000 müşteri, iki girdi türü | 65,4 GB | hesap |
| çalışma kümesi, saatte 1.000 ödemede (183 MB ham + 444 MB boşaltılmış) | ~630 MB | hesap |

İki şey bu tabloyu okurken önemlidir. (1) Profil tablosunda **büyüme terimi
yoktur** — tampon 20 referans + 4 onay bekleyen vektörde tahliye eder, yani
32,7 kB bir tavandır, beklenti değil; beş oturumu olan müşteri 6,8 kB tutar ve
müşterilerin çoğunun hiç olgunlaşmaması beklenir. (2) Çalışma kümesi bir
geçmiş değil, bir saatlik trafiktir: büyümez, orada durur.

> **Etiket:** yukarıdaki "hesap" satırları **ölçümlerden türetilmiş
> aritmetiktir, yük testi değildir**. Rapora bu etiketle girmelidir. Hesabın
> tamamı ve girdilerinin her biri:
> [`TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §17,
> [`entegrasyon.md`](entegrasyon.md) §9.

## 3. Bu SDK her tür e-cüzdana entegre edilebilir mi?

**Dürüst cevap: her web tabanlı ödeme akışına evet, "her tür"e henüz hayır.**
Adımların tamamı, yük sözleşmesi ve hata yönetimiyle birlikte
[`entegrasyon.md`](entegrasyon.md) belgesindedir.

| ortam | bugün | not |
|---|---|---|
| web ödeme sayfası | **çalışıyor** | bu depodaki demo tam olarak bu desen |
| WebView tabanlı e-cüzdan | **aynı SDK** | uçtan uca test edilmedi; dokunmatik için hiçbir oran ölçülmedi |
| yerel Android / iOS | **yol haritası** | yerel SDK yok; API platformdan bağımsız REST, yükü uygulamanın kendisi üretmeli |
| müşteri referansı olmayan misafir ödemesi | bot skoru çalışır | müşteri profili katmanına hiç ulaşmaz |

**Bugün çalışan.** Satıcının kontrol ettiği herhangi bir web ödeme sayfası: bir
`<script>` etiketi, bir `DeepCheck.init({ apiUrl })` çağrısı ve sunucu tarafında
işlemi onaylamadan önce `POST /api/decision`. Adımlar
[`README.md`](../README.md) "Entegrasyon" bölümünde, ayrıntısı
[`entegrasyon.md`](entegrasyon.md) §2'de. Backend, satıcının kendi
ağında çalışan bir konteynerdir; veri çevre dışına çıkmaz. Ödeme sağlayıcısına,
kart şemasına veya cüzdanın muhasebesine hiç dokunulmaz — katman yalnızca
"bu ödemeyi onaylamadan önce ek doğrulama iste" der.

**Bugün çalışmayan, açıkça:**

- **Yerel mobil uygulamalar (iOS/Android).** SDK tarayıcı JavaScript'idir ve
  Pointer Events kullanır. Uygulama içi WebView çalışır; yerel bir SDK
  **yoktur** ve yazılmadı.
- **Dokunmatik geçmiş ölçülmedi.** Kod `touch` girdi türünü tanır, ama sentetik
  simülatörün parmak modeli olmadığı için dokunmatik için hiçbir oran ölçülmedi
  (`backend/train_model.py` dokunmatik talebini reddeder). Telefonda katman
  çalışır, ama kalitesi hakkında elimizde sayı yoktur.
- **Müşteri referansı olmayan misafir ödemesi** bu katmana hiç ulaşmaz. Bot
  skoru çalışmaya devam eder; müşteri geçmişi diye bir şey yoktur.
- **Ek doğrulama kanalı satıcınındır.** Katmanın gücü o kanalın gücü kadardır.
  Satıcının kendi OTP'sini geçen bir doğrulamayı DeepCheck'e bildireceği bir uç
  nokta **henüz yok**; bugün bunu yalnızca demo akışı yapabiliyor.

## 4. Aynı anda çok sayıda istek gelirse sunucu çöker mi?

**CPU yüzünden hayır — ve önemlisi, yoğunlukta kapı açılmaz.**
[`TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §17.5'in özeti:

**Bugün ne oluyor:**

- **Tavan aşıldığında istekler kuyruğa girer ve yavaşlar**, düşürülmez ve süreç
  ölmez. Kâğıt üzerinde tavan, vCPU başına ~87 eşzamanlı ödeme oturumudur
  (yukarıdaki aritmetik).
- **Yavaşlama onaya dönüşmez.** Her başarısızlık yolu `verify` ile biter:
  oturum kaydı yoksa, kanıt 3 akıştan azsa, telemetri bayatsa, skor yoksa ya da
  sonlu değilse, profil okuması başarısızsa — hiçbirinde `allow` çıkmaz. Yani
  doymuş bir DeepCheck "herkesten ikinci faktör iste" hâline düşer: dönüşüm
  kaybettirir, dolandırıcılık geçirmez. Bir ödeme kapısı için doğru yön budur
  ve yük testinin eksikliği bu yüzden bir **boyutlandırma** açığıdır, güvenlik
  açığı değil.
- **İstek boyutu sınırlıdır.** Analiz yükündeki her liste `max_length` taşır
  (2000 işaretçi noktası, 1000 kaydırma, 1000 tuş olayı…), yani tek bir çağrı
  keyfî olarak pahalı yapılamaz.
- **Olay döngüsü bloklanmaz.** `compute_risk` iş parçacığı havuzunda çalışır
  (`run_in_threadpool`). Önceden satır içi çalışıyordu ve her şeyi bloklardı:
  `AUDIT.md` C-3, 20 eşzamanlı akışta olay döngüsünde p95 762 ms, en fazla
  1510 ms gecikme ölçtü. Asıl tehlikeli olan buydu — zaman aşımına uğrayan bir
  sağlık kontrolü, **sağlıklı** bir konteyneri yeniden başlattırır.
- **Çağıran başına hız sınırı vardır** (`RATE_LIMITS`, işçi başına ve bellekte):
  IP başına oturum açma 10/dk, oturum başına akış 60/dk, oturum başına karar
  20/dk, müşteri başına profilli karar 60/saat, satıcı başına profil yönetimi
  600/dk.

**Gerçekten devirebildiğimiz tek şey bellekti, yük değil.** Dört işçi;
forestların, SHAP açıklayıcısının ve torch'un dörder kopyasını tutar, her biri
~300–400 MB. Docker Desktop'ın öntanımlı ~2 GB'lık sanal makinesinde, yanında
Postgres ve nginx varken makine takas alanına düşer: `/api/health`, CPU %1'deyken
**25 saniyede** cevap verdi (`backend/entrypoint.sh`). `UVICORN_WORKERS`
öntanımlı olarak bu yüzden 2'dir. Ana makineyi yalnızca çekirdek sayısına göre
boyutlandırmak bu hatayı tekrarlar.

**Yük boşaltma tasarımı — yazıldı, uygulanmadı** (§17.7; bir özellik sanılmasın
diye açıkça tasarım olarak duruyor):

1. **Kuyruğa almak yerine boşalt:** skorlama havuzunun etrafında sabit
   derinlikte bir semafor ve dolduğunda `Retry-After` ile hızlı 503.
   Boşaltılacak olan `/api/analyze`'dir — düşen bir akış 2 saniyelik bir kanıt
   penceresi kaybettirir ve SDK'nın kayan tamponu onu bir sonraki akışta zaten
   yeniden gönderir. `/api/decision` **boşaltılmamalıdır**: ödeme başına tek
   istektir ve başarısızlık modu zaten `verify`dir.
2. **SDK'da geri çekilme:** 503/429'da `Retry-After`'a uymak, yoksa aralığı 16
   saniyeye kadar ikiye katlamak, başarıdan sonra 2 saniyeye dönmek — tam
   jitter ile. Jitter olmadan yeniden başlatılan bir dağıtım bütün açık
   sayfalara aynı yeniden deneme anını verir.
3. **Yatay ölçekleme:** konteynerler oturum durumu tutmaz, yani daha fazla
   konteyner + yük dengeleyici. Durumsuz olmayan iki şey var: bellekteki hız
   sınırları ve profil devre kesicisinin önbelleği; ikisi de örnek sayısı
   arttıkça **zayıflar**, yanlışlamaz.
4. **Paylaşılan sınır deposu (Redis):** bir sınırın işçi sayısından bağımsız
   olarak aynı anlama gelmesi için.
5. **Havuz zaman aşımı ve eşzamanlılık tavanı:** sistemin birikmek yerine hızlı
   başarısız olması için. Üçü de `get_engine()` ve uygulama fabrikasında
   birer satırlık değişiklik; eksik olan, sayıları belirleyecek yük testidir.

**Söylemediğimiz şey.** Bu kodla **yük testi yapılmadı**. Eşzamanlı istek sayısı
sınırlanmıyor ve veritabanı bağlantı havuzunda zaman aşımı ayarlı değil
(işçi başına 5 + 10 bağlantı, 30 saniyelik bekleme: bir ödeme kapısında 30
saniyelik bekleme, askıda kalmaktan ayırt edilemez). Dürüst iş sırası: önce
havuz zaman aşımı ve eşzamanlılık tavanı (madde 5), sonra boşaltma ve geri
çekilme (1–2), **sonra** bu bölümü ölçümle değiştirecek bir yük testi.

---

*İlgili ölçümler: [`docs/evaluation.md`](evaluation.md), [`docs/profile-evaluation.md`](profile-evaluation.md), [`backend/model_selection.py`](../backend/model_selection.py).*
*Entegrasyon ayrıntısı: [`docs/entegrasyon.md`](entegrasyon.md). Kapasite hesabının tamamı: [`TECHNICAL_GUIDE.md`](../TECHNICAL_GUIDE.md) §17.*
