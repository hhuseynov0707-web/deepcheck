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

Bu yüzden topluluk artık **0.6 RF + 0.4 LSTM**. Model hâlâ eğitiliyor ve
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

---

*İlgili ölçümler: [`docs/evaluation.md`](evaluation.md).*
