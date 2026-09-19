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

| bileşen | ROC-AUC |
|---|---|
| Random Forest | 0.990 |
| LSTM | 0.898 |
| **Isolation Forest** | **0.340** |

0.340 zayıf değildir; **ters**tir. Rastgele tahmin 0.5 verir — bu bileşen
sistematik olarak saldırganı daha "normal" sıralıyordu. Aynı telemetri iki
ağırlıklandırmadan geçirildiğinde:

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

`backend/model_selection.py` yedi model ailesini (RF, ExtraTrees,
HistGradientBoosting, LightGBM, XGBoost, lojistik regresyon, MLP) ve dört
zamansal varyantı aynı bölmelerle karşılaştırıyor. Dört protokol var:
simülatörden simülatöre, simülatörden tarayıcıya, tarayıcı koşularında
grup-bazlı çapraz doğrulama ve **bir senaryoyu tamamen dışarıda bırakma**.
Sonuncusu en önemlisi, çünkü gerçek müşteri tanım gereği modelin görmediği
davranıştır.

| | ROC-AUC | Bot ≥80 (görülen senaryolar) | Görülmemiş H1 insanı ≥80 (blok) |
|---|---|---|---|
| **Random Forest** | 0.988 | 0.63 | **%0** |
| LightGBM | 0.983 | 0.89 | **%74** |
| XGBoost | 0.982 | 0.89 | %74 |
| HistGradientBoosting | 0.973 | 0.89 | %2 |

**Gradient boosting benimsenmedi.** Sıralama gücü aynı (AUC farkı −0.003,
%95 bootstrap GA [−0.010, +0.002]). Gördüğü senaryolarda daha çok bot yakalıyor
(+0.27, GA [+0.13, +0.40]). Ama görmediği bir insan grubunu dörtte üç oranında
blokluyor. Gerçek veri yokken ödeme kapısında doğru model, görmediğine en az
emin davranan modeldir.

**LSTM skordan çıkarıldı.** Yalnızca simülatörle eğitildiği için tarayıcı
trafiğinde çıktısı "insan"a çöküyordu (AUC 0.947, Brier 0.51). 0.4 ağırlıkla
harmanlandığında ≥60'a ulaşan bot oranı 0.90'dan 0.79'a düştü; karşılığında
tek bir ek insan yakalanmadı. Var olma sebebi olan devir teslimde RF'den
**4 akış geç** tepki verdi. Tarayıcı verisiyle yeniden eğitilmesi (AUC 0.956)
ve RF'ye geçmiş akışları öznitelik olarak vermek (AUC 0.984) de RF'yi
geçmedi.

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
modellerinin hiçbiri A2/A3/A4 saldırılarının %20'sinden fazlasını yakalamadı.
Görülmemiş bir saldırıyı yakalayan iki model (lojistik regresyon ve MLP) ise
görülmemiş insanların %48-68'ini de işaretledi. Darboğaz sinyaller ve veri.

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

# Demo Prosedürü — Müşteri profilinin ek doğrulama istemesi

## Neden sahnede sıfırdan kurulamaz

Profil katmanı bir müşteriyi yalnızca **kendi** geçmişiyle, aynı giriş türünde
(fare, klavye, dokunmatik) karşılaştırır ve bunun için en az **19 referans
oturum** ister. Bu sayı ayarlanmadı, aritmetikten geliyor: n referansla
ulaşılabilecek en küçük p-değeri 1/(n+1), yani 0.05 düzeyinde 19'un altında
katman hiç soru soramaz. Bir profile İstanbul günü başına en fazla **3** oturum
öğretilir (`PROFILE_LEARN_PER_DAY`), böylece bir saldırgan müşterinin
davranış zarfını bir öğleden sonrada yeniden kuramaz. Sonuç: bir demo
müşterisi **en erken 7 günde** olgunlaşır. Profil jüriden önce kurulur, jüri
önünde yalnızca son adım gösterilir.

**Tohum verisi yok.** Veritabanına elle vektör yazılmaz; bot laboratuvarı,
betik veya sentetik oturum kullanılmaz. Profildeki her referans, bir ekip
üyesinin demo sayfasında gerçekten yaptığı bir ödemedir.

## Hazırlık — yalnızca demo makinesinin `.env` dosyası

Ürünün varsayılanı katmanın kapalı olmasıdır ve öyle kalır.

```
DEEPCHECK_PROFILE_KEY=<python -c "import secrets; print(secrets.token_urlsafe(32))">
DEEPCHECK_MERCHANT_KEYS=yerel-satici:<ikinci, farklı bir token>
PROFILE_LAYER=1
PROFILE_ESCALATION=0
```

- Satıcı anahtarı demo ödemesinde kullanılmaz (demo sayfası ayrılmış `demo`
  ad alanına yazar), ama katman en az bir satıcı tanımlı olmadan açılmaz.
- Kurulum **gölge modunda** yapılır (`PROFILE_ESCALATION=0`): katman hesaplar,
  kaydeder ve öğrenir ama kimseden kod istemez. Profil olgunlaştıktan sonra ekip
  üyesinin kendi ödemelerinden biri sapma sayılırsa sorgulama bütçesi harcanmaz
  ve o oturum profile öğretilmez.
- Kurulum boyunca değişmemesi gerekenler: `DEEPCHECK_PROFILE_KEY` ve
  `DEEPCHECK_PROFILE_KEY_VERSION` (anahtar değişirse her profil sessizce
  sıfırlanır), veritabanı hacmi (**`docker-compose down -v` çalıştırılmaz**,
  profilleri siler) ve `FEATURE_SCHEMA_VERSION` (artıran bir kod güncellemesi
  mevcut profili karşılaştırma dışı bırakır).

## 1. Adım — Kurulum (jüriden en az 7 gün önce başlar)

Ekip üyesi A, **her gün 3 ödeme** yapar. Her seferinde:

1. `http://localhost:3000/demo` sayfasını yeniden yükler. Her yükleme yeni bir
   oturumdur ve bir oturum profile yalnızca bir kez öğretilir.
2. "Müşteri Referansı (demo)" alanında `demo-musteri-1` yazdığını kontrol eder.
3. Aynı dizüstü bilgisayarda, alanlara **fare veya touchpad ile tıklayarak**
   formu gerçekten doldurur. Hiç işaretçi olayı olmayan oturum "Klavye",
   telefon "Dokunmatik" sayılır ve ayrı bir referans kümesi biriktirir, ama
   günlük 3 hakkı yine tüketir.
4. Onayla'ya basar.

Profile yalnızca **onaylanan** (`allow`) ödeme öğretilir: doğrudan onay ya da
doğrulama kodu girilerek alınan onay. "Şüpheli" bandında uyarıyla geçen
(`warn`), reddedilen ya da en az 3 ölçülmüş davranış penceresi toplanamamış
(`PROFILE_MIN_FLUSHES`) ödeme öğretilmez. Böyle bir ödeme o gün yeni bir
oturumla tekrarlanabilir.

**İlerleme SOC panosunda izlenir.** Oturum seçildiğinde "Müşteri Profili"
kartında `Fare: n / 19` görünür. Bu, ödeme kararı **verildiği andaki**
referans sayısıdır. Bu yüzden bir ödemenin katkısı ancak bir sonraki ödemenin
kartında görünür. Onayla'ya basılmadan önce kart "Profil yok" der; bu beklenen
davranıştır. Kurulum boyunca kartta "Gölge modu — karar etkilenmedi" rozeti
durur.

Hedef 19 değil **20** referanstır. Kalibrasyon kümesi, bu oturumla
karşılaştırılamayacak kadar az özellik ölçmüş referansları dışarıda bırakır.
Küme 19'un altına düşerse katman yine susar; kart bu durumda "Karşılaştırılabilir
referans yetersiz" yazar. 7 gün × 3 = 21 ödeme bunu karşılar, tampon en fazla
20 oturum tutar.

## 2. Adım — Jüri oturumundan hemen önce

`.env` içinde `PROFILE_ESCALATION=1` yapılır ve `docker-compose up -d`
çalıştırılır. Değişen konteyner yeniden oluşturulur, veritabanı hacmi korunur.
Bu ayar **yalnızca bu demo içindir**: zorunlu mod, gerçek bir kişi farklı
oturum ve cihazlarda ölçülmeden üründe açılmamalıdır (bkz.
[`docs/profile-evaluation.md`](profile-evaluation.md) §12). Zorunlu mod
açılmak istenmezse 3. adım gölge modunda da yapılabilir. O zaman pencere
açılmaz, ama pano sapmayı yine gösterir.

## 3. Adım — Jüri önünde

1. SOC panosu açık tutulur.
2. **Başka bir kişi (B)**, aynı bilgisayarda ve aynı fareyle, **yeni
   yüklenmiş** demo sayfasında `demo-musteri-1` referansıyla formu doldurur ve
   Onayla'ya basar. Sayfa bu oturumda daha önce doğrulama kodu almış
   olmamalıdır. Taze bir doğrulama, sonraki profil sorgusunu da karşılar. Bu
   bilinçli bir tercih: aksi, doğru kodu giren müşteriye tekrar tekrar kod
   soran döngüyü geri getirirdi.
3. Katman B'nin oturumunu, A'nın kalibrasyon kümesindeki oturumların
   **hepsinden** daha uç bulursa ödeme sayfasında doğrulama kodu penceresi
   açılır. Sayfa nedenini söylemez. İstemciye giden gerekçe, küme ve konformal
   kontrollerle aynı genel `step_up`'tır. Risk skoru ve etiket değişmez.
4. **Kod girilmeden önce** panoda B'nin oturumu seçilir: "Değerlendirildi",
   "Ek doğrulama istendi", p-değeri (20 referansla en küçüğü 1/21 ≈ 0.0476) ve
   en çok sapan özellikler görünür. İstemcinin görmediği gerekçe yalnızca SOC
   tarafındadır. Kartta "Ek doğrulama istendi" yoksa pencere profil yüzünden
   değil, skor gibi başka bir kontrol yüzünden açılmıştır.
5. B kodu girer ve ödeme alınır. Bu, "torun büyükannesi adına ödüyor"
   senaryosudur: katman yalnızca ek doğrulama ister, engellemez. O gün 3
   öğrenme hakkı dolmamışsa B'nin oturumu profile **deneme (probation)**
   olarak kaydedilir. Son karar artık `verified` olduğu için kartta "Ek
   doğrulama istendi" rozeti kalkar.
6. **Bilinçli olarak kabul edilen bedel.** Deneme olarak kaydedilen oturum
   referans **değildir**: sapma hesabına ve olgunluk sayısına katılmaz.
   Bu yüzden B aynı şekilde bir kez daha öderse yine doğrulama kodu istenir.
   Bu bir engelleme değil, fazladan bir doğrulamadır. Oturum ancak satıcı
   ödemeyi onayladığında (`POST /api/outcome`, `settled`) ya da üst üste 3
   başarılı doğrulama profili yenilediğinde referans olur. Demo ad alanında
   satıcı anahtarı olmadığı için demoda yalnızca ikinci yol vardır. Panoda
   kart bu oturumu "Onay bekleyen oturum: 1" olarak referanslardan ayrı
   gösterir. Nedeni ölçüldü: deneme oturumları referans sayıldığında, kodu
   bir kez ele geçirip geçen saldırganın sonraki oturumlarını da koruyordu.
   Sentetik kimliklerde tek bir saldırgan oturumu, saldırganın sonraki
   oturumlarında ek doğrulama oranını %48.5'ten %25.0'a düşürdü (2026-09-16
   ölçümü, [`docs/profile-evaluation.md`](profile-evaluation.md) başındaki
   önce/sonra tablosu). Bugünkü kuralla, onay bekleyen oturum olarak saklanan
   0, 1, 2, 4 ya da 6 saldırgan oturumunda oran her seferinde %47.5 kaldı
   (§7). Bu kuralın bedeli de ölçüldü: sorgulanıp doğrulamayı geçen farklı bir
   kişi aynı şekilde geri geldiğinde, sonraki oturumu farede %71.7, klavyede
   %62.4 oranında yeniden sorgulandı (§7). Her biri bir engelleme değil, bir
   doğrulamadır ve bir profil 30 günde en fazla 3 kez sorar.

## Beklenti — dürüst hali

**Ek doğrulama garanti değildir.** Farklı bir kişi A'nın kendi oturumlarından
daha uç görünmüyorsa ödeme doğrudan geçer ve panoda p-değeri 0.05'in üstünde
görünür. Bu bir arıza değil, kalibre edilmiş davranıştır; jüriye de böyle
anlatılır. Ne sıklıkla olacağına dair elimizdeki tek sayılar sentetiktir
([`docs/profile-evaluation.md`](profile-evaluation.md) §5 ve §9, fare, 20
referans):

| | Oran |
|---|---|
| Farklı kişi ek doğrulamaya düştü | %47.5 (%95 GA %44.0–50.8) |
| Aynı kişi yanlışlıkla sorgulandı (**alt sınır**) | %4.9 (%95 GA %4.0–5.9) |
| Farklı kişi, kişi-içi değişkenlik varsayımı 0.6 yerine 0.3 / 1.0 olsaydı | %68.0 / %27.3 |

Üç satırın üçü de şu an kullanılan kodun, yani **tam konformal** sıralamanın
2026-09-18 ölçümüdür (§5 ve §9). Onun yerine geçtiği birini-dışarıda-bırakan
sıralama, aynı oturumlarda aynı kişiyi %6.0 oranında sorguluyordu: aday ile
referanslar aynı fonksiyonla puanlanmadığı için 0.05 düzeyi bir oran olarak
tutmuyordu (sayfanın başındaki önce/sonra tablosu).

> **Sentetik kimlikler üzerinde ölçüldü; gerçek müşteri verisi yoktur.** Tek
> bir gerçek kişinin çok sayıda oturumu henüz ölçülmedi ve bu demo da bir
> ölçüm değildir: demo müşterisi `is_demo` olarak işaretlenir ve raporlanan
> hiçbir ölçüme katılmaz.

## Prova kuralları — jüri gününün hakkını harcamamak için

- **Sorgulama bütçesi.** Bir profil 30 günde en fazla **3** kez ek doğrulama
  ister (`PROFILE_MAX_ESCALATIONS`). Sonrasında katman susar ve kart
  "Sorgulama bütçesi doldu" der. Her prova bir hak harcar. Aynı müşteride en
  fazla bir prova yapılır. Daha fazlası gerekiyorsa A, aynı 7 gün içinde ikinci
  bir demo müşterisini (ör. `demo-musteri-prova`) paralel kurar; günlük sınır
  müşteri başınadır.
- **Kendi kendini onarma.** Üst üste **3** kez sorgulanıp doğru kodu girilen
  profil yanlış kabul edilir: bu 3 deneme oturumu referansa terfi eder ve
  profil en yeni 10 oturuma indirilir (`PROFILE_HEAL_AFTER`,
  `PROFILE_HEAL_KEEP`). Profil yeniden olgunlaşana kadar, yani en az 3 gün,
  katman susar. Tek bir başarılı doğrulama hiçbir oturumu terfi ettirmez.
  Provada B kodu **girmez**, pencereyi kapatır.

## Bu demo neyi gösterir, neyi göstermez

**Gösterir:** Katmanın tek çıktısı ek doğrulamadır; skor, etiket ve engelleme
değişmez. Gerekçe istemciye söylenmez, yalnızca SOC panosunda ve denetim
kaydında durur. Doğru kodu giren kişi ödemeyi tamamlar.

**Göstermez:**

- Gerçek müşterilerde yanlış sorgulama ve yakalama oranını; bunlar ölçülmedi.
- Kodu oltalama ile ele geçirmiş saldırgana karşı korumayı. Katmanın gücü,
  satıcının ek doğrulama kanalının gücü kadardır; demoda kod sayfada yazılıdır.
- Kart deneme botlarına karşı bir etkiyi. Müşteri referansı olmayan misafir
  ödemesi bu katmana hiç ulaşmaz.

---

*İlgili ölçümler: [`docs/evaluation.md`](evaluation.md), [`docs/profile-evaluation.md`](profile-evaluation.md), [`backend/model_selection.py`](../backend/model_selection.py).*
