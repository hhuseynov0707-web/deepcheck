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

## 1. Isolation Forest bu saldırıda işe yaramaz — zarar verir

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
| Topluluktaki ağırlığı | %20 |
| Net etkisi | Risk skorunu **aşağı** çeker |

Yani yüksek kaliteli taklide karşı Isolation Forest tarafsız biçimde
başarısız olmuyor; saldırganın lehine oy kullanıyor. Bu, ayarla düzelecek bir
şey değil, bileşenin tasarım amacının sonucudur: seyrek bölgeleri arar, taklit
ise yoğun bölgeye yerleşir.

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

*İlgili ölçümler: [`docs/evaluation.md`](evaluation.md).*
