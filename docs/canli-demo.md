# Canlı jüri demosu — iki bilgisayarla adım adım

Bu belge sahnedeki akışı anlatır: bir ekip arkadaşı **kendi bilgisayarından** elle
ödeme yapar, jüri SOC panelinde skoru canlı izler; ardından **aynı bilgisayardan**
bir betik (bot) saldırır ve jüri sistemin tepkisini görür.

> **Dürüstlük notu.** Sahnedeki bot kasıtlı olarak modelin tanıdığı **en basit**
> bot tipidir: parametreleri modelin eğitiminde kullanılan «bot» üretecinden
> alınmıştır (aşağıda "Bot ne yapıyor"). Engellenmesi, sistemin uçtan uca
> çalıştığını gösterir; gerçek botların ya da insan taklidi yapan akıllı botların
> engellendiğini göstermez. Fareyi hiç kullanmayan ya da insan hareketini taklit
> eden betikler bu modelden **geçer** (ölçüm aşağıda). Jüri sorarsa bunu açıkça
> söyleyin; sahnede böyle bir bot denemeyin.

## Roller

| | Bilgisayar A (sunum) | Bilgisayar B (ekip arkadaşı) |
|---|---|---|
| Ne çalışır | Docker: veritabanı, backend, frontend | Tarayıcı (Chrome/Edge) + `cmd` |
| Projeksiyonda | SOC paneli: `http://localhost:3000/dashboard` | — |
| Gereken | Bu depo, Docker Desktop, `.env` | Python 3.9+ ve **tek dosya**: `live_bot.py`, Masaüstü'nde (kurulum gerekmez) |

## Bir gün önce

**Bilgisayar A**

1. `.env` içinde şunlar olsun:
   - `DEBUG=0`, gerçek `DEEPCHECK_SECRET` ve `DASHBOARD_KEY`, `DEMO_ENDPOINTS=1`
     ve **`BIND_ADDR=0.0.0.0`** (yalnızca 3000 portunu ağa açar; 8000 kapalı
     kalır). `VITE_API_URL` **boş** kalsın.
   - **Profil katmanı:** `PROFILE_LAYER=1`, `DEEPCHECK_PROFILE_KEY=<token>` ve
     `DEEPCHECK_MERCHANT_KEYS=ornek-satici:<başka bir token>`. Kurallar
     `.env.example` içindeki profil katmanı bloğunda (`DEEPCHECK_MERCHANT_KEYS`:
     anahtarlar birbirinden ve diğer sırlardan farklı olmalı, satıcı anahtarı en
     az 32 karakter; `demo` kimliği ayrılmıştır), örnek değerler ve anahtar
     üretme komutu **"Jury prototype"** bloğunda. **Neden gerekli:** SOC
     panelindeki **"Son kaydedilen karar"** satırı `decision_audit` tablosundan
     okunur ve bu tabloya yalnızca profil katmanı açıkken yazılır. Bu üç satır
     yoksa panel her iki perdede de "Karar kaydı yok" gösterir.
   - `PROFILE_ESCALATION` 0 ya da 1 olabilir (sentetik müşteri gösterimi için 1
     gerekir). Elle ödemede fark etmez: sayfanın varsayılan referansı
     `demo-musteri-1` hiçbir zaman olgunlaşamaz. Karşılaştırma için 19 referans
     oturum gerekir, profil günde en fazla 3 oturum öğrenir ve bir demo
     ziyaretçisinin vektörleri 24 saatte silinir; olgun olmayan profil sapma
     nedeniyle ek doğrulama istemez. Tek istisna, `PROFILE_ESCALATION=1` iken:
     aynı referansla bir saat içinde 60'tan fazla Onayla basılırsa ek doğrulama
     istenebilir. Provada bu sayıya yaklaşmayın.
2. Bir kez derleyip başlatın: `docker compose up -d --build`
3. `http://localhost:3000/api/health` → `"model_loaded": true` görülmeli.
   **502 Bad Gateway** görürseniz backend henüz istek kabul etmiyor:
   `docker compose logs -f backend` ile bakın. «Modeller egitiliyor» yazıyorsa
   bekleyin (backend'in kendi tahmini 4–8 dakika, makineye göre değişir) ve
   **yeniden başlatmayın, yeniden derlemeyin**: eğitim baştan başlar. Bunu
   sahnede yaşamayın.
4. Windows güvenlik duvarı: **PowerShell'de** `Get-NetConnectionProfile` ile ağ
   profilini görün (bu komut cmd'de yoktur). **Public** ise içeri bağlantı
   engellenir. Ya ağı **Private** yapın (Ayarlar > Ağ ve İnternet > Wi-Fi > ağ >
   Ağ profili türü), ya da **yönetici** PowerShell'de kuralı kendiniz ekleyin
   (demodan sonra silin):

   ```powershell
   New-NetFirewallRule -DisplayName "DeepCheck demo 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet
   Remove-NetFirewallRule -DisplayName "DeepCheck demo 3000"
   ```

   Kuralı eklediğiniz hâlde B'den `http://IP:3000/api/health` açılmıyorsa
   Docker için bir **engelleme (Block) kuralı** olabilir: Windows'ta Block kuralı
   her Allow kuralından önce gelir. Docker'ın güvenlik duvarı sorusu yalnızca
   özel ağ işaretlenerek yanıtlandıysa Windows Public profil için böyle bir
   kural oluşturur. PowerShell'de bakın:

   ```powershell
   Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue | Where-Object DisplayName -like "*docker*"
   ```

   Çıktı boşsa böyle bir kural yok. Bir kural listeleniyorsa en hızlı çözüm ağı
   **Private** yapmaktır.

**Bilgisayar B**

1. `python --version` → 3.9 veya üstü. Yoksa python.org'dan kurun ve kurulumun
   ilk ekranında **"Add python.exe to PATH"** kutusunu işaretleyin. İşaretlenmediyse
   `python` Microsoft Store'u açar; o zaman her komutta `python` yerine `py`
   yazın (`py --version`, `py live_bot.py --url ...`).
2. `lab\live_bot.py` dosyasını B'nin **Masaüstü'ne** kopyalayın (yalnızca
   standart kütüphane kullanır). Kontrol, yeni bir `cmd` penceresinde:

   ```bat
   cd %USERPROFILE%\Desktop
   dir live_bot.py
   ```

   Dosya listelenmiyorsa Masaüstü OneDrive'a taşınmıştır: komutu yazarken
   `python ` yazdıktan sonra dosyayı Masaüstü'nden cmd penceresine sürükleyip
   bırakın (tam yolu kendisi yazar), ardından ` --url http://IP:3000` ekleyin.
3. **Harici USB fare** takın. Dokunmatik ekran ve dokunmatik yüzey kullanmayın:
   model dokunmatik girdiyle hiç eğitilmedi, dokunmatik yüzey ise hiç ölçülmedi.
4. Ekran yenileme hızını sabit **60 Hz** yapın (Windows "Dinamik yenileme hızı"
   kapalı). 165/240 Hz eğitim karışımında yok.

## Mekânda (sahneden en az 15 dakika önce)

1. İki bilgisayar **aynı ağda** olsun. Mekân Wi-Fi'ı cihazları birbirinden
   ayırıyorsa (çok yaygın) bir **telefon hotspot'u** kullanın.
2. A'da `ipconfig` → **Wi-Fi** bağdaştırıcısının IPv4 adresi (vEthernet/WSL
   satırları değil). Aşağıda `IP` diye geçer. IP değişirse yeniden derleme
   gerekmez.
3. B'nin tarayıcısında `http://IP:3000/api/health` → `model_loaded: true`.
   Açılmıyorsa sorun ağda ya da güvenlik duvarındadır, uygulamada değil
   (yukarıdaki Block kuralı notuna bakın).
4. **Prova:** B'de `cmd` açıp botu bir kez çalıştırın (aşağıdaki komut).
   `ENGELLENDİ — ödeme alınmadı` görmelisiniz. Botu art arda çok çalıştırmayın:
   dakikada ~10 oturum açma sınırı var (aşılırsa 60 saniye bekleyin). Elle
   ödeme provası yapılacaksa onu da **şimdi** yapın.
5. A'da SOC panelini açın, anahtarı girin, **sekmeyi kapatmayın**.
   "Canlı takip: Açık" olsun. Provaları ekrandan kaldırmak için
   **"Görünümü sıfırla"** (veri silinmez, yalnızca görünüm).
6. **"Görünümü sıfırla"ya basmadan ÖNCE B'de prova için açılmış bütün `/demo`
   sekmelerini kapatın** (etkin sekmede **Ctrl+W**; sekmeler arasında gezinmeyin).
   Panel sıfırlamadan sonra *başlayan* oturumları gösterir, ayrıca sıfırlamadan
   sonra yeniden etkinleşen eski bir oturumu da ~12 saniye sonra geri getirir.
   Bir sayfa yüklemesi 30 dakikaya kadar aynı oturumu kullanır ve sekmeyi arka
   plana almak bile bir olay sayılır. Açık kalan bir prova sekmesi bu yüzden
   panelde, prova pencereleri ve provadaki kararıyla birlikte yeniden belirir
   ve metriklere sayılır.
7. Projektör 1280 pikselden darsa tarayıcı yakınlaştırmasını %80–90 yapın.

## Sahne akışı

### 1. İnsan ödemesi (B, tarayıcı)

1. B'de **yeni bir sekmede** `http://IP:3000/demo` açılır; prova sekmesi asla
   kullanılmaz (emin değilseniz sayfayı yenileyin: her yükleme yeni bir
   oturumdur). "Müşteri Referansı (demo)" alanına **dokunmayın**, varsayılan
   `demo-musteri-1` kalmalı: müşteri referansı taşımayan bir onay kaydedilmez,
   yani alan boşaltılırsa insanın onayı SOC'ta "Son kaydedilen karar" olarak
   görünmez. Demo müşteri listesinden Ayşe/Mehmet/Zeynep seçmeyin: onların
   sentetik geçmişi vardır, ekip arkadaşının davranışı o geçmişle karşılaştırılır
   ve ek doğrulama istenebilir.
2. Ekip arkadaşı kartı **fare ve klavyeyle, elle** doldurur: önce 2–3 saniye
   doğal fare hareketi, sonra alanlar. **15–25 saniye** sürsün. Yapıştırma ve
   otomatik doldurma yok; Backspace'i **basılı tutmayın** (tekrar eden tuş, bot
   zamanlamasına benzer).
3. Paneldeki "Analiz yanıtı" sayısı **6 veya üstü** olunca fareyle
   **Onayla**'ya basılır.

SOC panelinde (A): yeni oturum otomatik seçilir. İlk birkaç saniye
**"Değerlendiriliyor"** görünür; bu bilinçli: karar için en az 3 gözlenen pencere
gerekir. Ardından skor ve **"Son kaydedilen karar: Onaylandı"**.

### 2. Bot saldırısı (B, yeni cmd penceresi)

Tarayıcı sekmesini kapatın, yeni bir `cmd` açın:

```bat
cd %USERPROFILE%\Desktop
python live_bot.py --url http://IP:3000
```

(`python` Microsoft Store'u açıyorsa: `py live_bot.py --url http://IP:3000`.)

**Bot çalışırken cmd penceresinin içine tıklamayın.** Klasik Windows konsolunda
pencereye tıklamak metin seçimi başlatır ve seçim sürdükçe program durur; bot
pencere gönderemez ve sonuç `VERİ GÜNCEL DEĞİL` ya da `KARAR İÇİN VERİ YETERSİZ`
olabilir. Yanlışlıkla tıklandıysa hemen **Esc**'e basın. Pencereyi öne
getirmek gerekirse görev çubuğunu kullanın.

Konsol her 2 saniyede bir pencere skorunu yazar; yaklaşık 18 saniye sonra bot
ödeme ister ve sonuç büyük harflerle görünür: **ENGELLENDİ — ödeme alınmadı**.
SOC panelinde (A): yeni oturum otomatik seçilir, **Bot Tespit Edildi** ve
**"Son kaydedilen karar: Engellendi"**.

### Bot ne yapıyor

Tarayıcı açmaz. SDK'nın protokolünü kendisi konuşur (oturum, iş kanıtı,
doğrulama) ve sunucuya doğrudan, betikli bir form doldurmanın davranışını
gönderir:
- fare, betiğin kendi ~80 ms zamanlayıcısıyla noktalı bir yol izler;
- her denemede CVV alanına tıklar ve 3 rakamı 1–4 ms arayla yazar;
- 2 saniyede bir yeni CVV dener, sonra ödeme ister.

**Bu botun parametreleri, modelin eğitiminde «bot» sınıfı için kullanılan
sentetik üreteçten alınmıştır** (`backend/train_model.py`: `_background_motion`
ve `_phase_bot`): aynı 80±10 ms fare adımı ve x/y artışları, tıklamadan sonra
aynı 150±8 ms bekleme (eğitimde bu aralık art arda iki tıklamayı ayırır) ve
eğitimdeki headless varyantın 1–4 ms tuş aralığı. Pencere bileşimi farklıdır:
sürekli fare akışı (10 saniyelik pencerede ~125 nokta, eğitim penceresinde
10–20), 2 saniyede bir tıklama, kaydırma yok. Model bu parametrelerle üretilmiş
davranışı eğitimde gördü. Engellenmesi,
sistemin uçtan uca çalıştığını gösterir; modelin genellediğini göstermez.
Gerçek kart deneme botlarının ne kadarının böyle davrandığını **ölçmedik**.
Fareyi hiç kullanmayan bir kart deneme döngüsü çevrimdışı 120 çalıştırmanın
**111'inde onaylandı** (aşağıda "Ölçülenler"). Sunucu bu botu da herhangi bir
oturum gibi aynı modelle skorlar ve aynı kodla karar verir.

### Söylenebilecek kısa metin

- İnsan: "Ekip arkadaşımız kendi bilgisayarından ödüyor. Sistem kart bilgisini
  hiç görmüyor: fare hareketini, tıklamaları, kaydırmayı ve tuşlara basılma
  zamanlarını ölçüyor, hangi tuşa basıldığını değil. Karar tarayıcıda değil,
  sunucuda veriliyor."
- Bot: "Şimdi aynı bilgisayardan bir otomasyon betiği aynı ödemeyi deniyor.
  Bu kasıtlı olarak modelin tanıdığı en basit bot tipi. Sistem onu ödeme anında
  engelliyor; SOC panelinde nedenini görüyoruz."
- Her iki sonuç için: "Bu bir canlı gösterim, bir doğruluk ölçümü değil.
  Gerçek kullanıcı verimiz henüz yok; ölçtüğümüz tek gerçek kişi var."

## Bir şey ters giderse

| Durum | Ne yapılır |
|---|---|
| İnsan "Karar için biraz daha veri gerekiyor" alırsa | Formda 4–5 saniye doğal biçimde devam edip bir kez daha Onayla |
| İnsan "Ek Doğrulama Gerekli" alırsa | Ekrandaki kodu (482913) girip Doğrula. Söylenecek: "Sistem emin olmadığında kartı çekmiyor, 3-D Secure gibi ikinci kanıt istiyor. Gerçek kişi bunu saniyeler içinde geçer; bot telefona gelen kodu alamaz." |
| İnsan "Ödeme reddedildi" alırsa | SOC'ta SHAP özelliklerini gösterin; "gerçek kullanıcı verisi toplamamızın sebebi tam olarak bu" deyin. Sayfayı yenileyip tekrar denemeyin, jüri önünde aynı şeyi zorlamayın |
| SOC panelinde seçili oturum insanınki değilse | Ödeme büyük olasılıkla bir prova sekmesinde yapıldı: o oturum sıfırlamadan önce başladığı için ~12 saniye sonra listede belirir, ama canlı takip en son *başlayan* oturumu seçer. İnsanın oturumunu listeden **elle** seçin (gerekirse "Tümünü göster"). Elle seçim canlı takibi kapatır; bot perdesinden önce "Canlı takip"i yeniden açın |
| Bot `EK DOĞRULAMA İSTENDİ` derse | Sistem kartı çekmedi ama engellemedi de: ikinci kanıt istedi. Gerçek nedeni SOC'ta "Son kaydedilen karar" altında okuyun. Söylenecek: "Bu sefer engel yerine ek doğrulama istedi. Gerçek bir 3-D Secure akışında kod kart sahibinin telefonuna gider ve bot onu alamaz; bu demoda kod ekranda yazılı. Bunu bir engelleme gibi anlatmıyoruz." |
| Bot `KARAR İÇİN VERİ YETERSİZ`, `OTURUM VERİSİ SUNUCUYA ULAŞMADI` ya da `VERİ GÜNCEL DEĞİL` derse | Bu bir tespit **değil**. Ya botun davranış verisi sunucuya yeterince ya da zamanında ulaşmadı (ağ gecikmesi, tıklanıp donmuş konsol), ya da — yalnızca `KARAR İÇİN VERİ YETERSİZ` için — sunucunun ardışık testi bu pencere sayısıyla henüz sonuç vermedi. Tespit gibi anlatmayın; botu bir kez daha çalıştırın |
| Bot `ONAYLANDI — ödeme alındı` ya da `UYARI İLE ONAYLANDI — ödeme alındı` derse | Gizlemeyin. Söylenecek: "Bu betik provalarda çevrimdışı 200/200, canlı 23/23 engellendi; bu çalıştırma geçti. Bu bir doğruluk ölçümü değil; model eğitimde gördüğü bot tipini bile her seferinde yakalamıyor." SOC'ta düz bir onay için "Karar kaydı yok" görünür (müşteri referansı taşımayan bir onay kaydedilmez); uyarıyla onay ise kaydedilir ve "Uyarıyla onaylandı" görünür |
| Bot "Bağlantı koptu" derse | Ağ kesintisi; bu çalıştırmanın sonucu yok (engellendi sayılmaz). Ağı kontrol edip bir kez daha çalıştırın |
| Bot "Bu adreste DeepCheck API'si yok" derse | Sunucuya ulaşıldı ama yanlış yere: adres 3000 portunu göstermeli (`http://IP:3000`), ve A'daki frontend imajı güncel olmalı (A'da `docker compose up -d --build`) |
| Bot "Sunucuya ulaşılamadı" derse | Ağ/güvenlik duvarı. Yedek: botu A'da, deponun kök klasöründe çalıştırın: `python lab\live_bot.py --url http://localhost:3000` |
| Bot "Sunucu hazır değil (HTTP 502)" derse | A'da `docker compose logs -f backend`; model eğitiliyorsa bekleyin, yeniden başlatmayın |
| Bot "HTTP 429" derse | 60 saniye bekleyin (oturum açma sınırı) |
| `python` "can't open file" derse | cmd yanlış klasörde: `cd %USERPROFILE%\Desktop`, ya da dosyayı cmd penceresine sürükleyip bırakın |
| `python` Microsoft Store'u açarsa | Aynı komutu `py` ile yazın: `py live_bot.py --url http://IP:3000` |
| SOC paneli anahtar isterse | Anahtarı girin; sekmeyi kapatmayın (anahtar yalnızca o sekmede tutulur) |
| SOC "Karar kaydı yok" derse (insan ya da engellenen bot için) | A'nın `.env`'inde profil katmanı satırları eksik (yukarıda A, adım 1). Yalnızca insanın onayı için ikinci bir neden: "Müşteri Referansı (demo)" alanı boşaltılmış (bot referans göndermez; engelleme her durumda kaydedilir) |
| İki bilgisayar birbirini görmezse | Her şeyi A'da yapın: insan A'nın tarayıcısında `http://localhost:3000/demo`, bot A'nın cmd'sinde, deponun kök klasöründe |

## Ölçülenler (2026-10-01; modeller 2026-09-25'te eğitildi)

- **Çevrimdışı:** botun kendi zaman çizelgesi, sunucunun skorlama ve karar
  kurallarından geçirildi, **ana bilgisayardaki bundle** ile
  (`backend/model-sklearn1.8.0.pkl`, scikit-learn 1.8.0): **200 rastgele
  çalıştırmanın 200'ü engellendi**, en düşük oturum skoru 93,2, medyan 94,8.
  (Bu bir laboratuvar ölçümüdür; insan değil, betik.)
- **Canlı:** Docker'daki sunucu `backend/model-sklearn1.5.0.pkl`'i servis eder
  (aynı eğitimin scikit-learn 1.5.0 derlemesi). Bilgisayar A'da `--url
  http://localhost:8000` ile **20 çalıştırmanın 20'si engellendi**, karar
  anındaki skor 94,3–95,8. Sonra nginx üzerinden (`:3000`) ve cmd.exe'den 3
  çalıştırma daha, hepsi engellendi (93,8; 93,9; 95,1). Bu, bu botun bu modele
  karşı provasıdır; "her bot engellenir" anlamına gelmez.
- **Eğitim verisiyle ilişkisi:** botun parametreleri modelin eğitimdeki «bot»
  üretecinden alınmıştır (yukarıda "Bot ne yapıyor"). Yukarıdaki sonuçlar modelin eğitimde
  gördüğü davranışa karşıdır. Gerçek kart deneme botlarının ne kadarının böyle
  davrandığı **ölçülmedi**.
- **Bilinen boşluk:** aynı bot zaman çizelgesinin fare akışı çıkarılmış hâli
  (yalnızca tıklama + 1–4 ms aralıklı tuş; 6 farklı ayar × 20 çalıştırma,
  çevrimdışı, ana bilgisayardaki bundle) **120'nin 111'inde onaylandı**,
  9'unda karar verilemedi; ayar başına medyan oturum skoru 2,5–21. Gerçek
  Chromium'da (Playwright, görünür pencere) fareyi kullanmadan yazan betik
  canlı sunucuda 2/2 onaylandı (skor 4,4 ve 13,3). İnsan hareketini taklit eden
  betikler de geçiyor (docs/evaluation.md). Bu senaryolar sahnede gösterilmez;
  sorulursa açıkça söylenir.
- Gerçek insan tarafı: tek bir gerçek kişi ölçüldü (p01). Yanlış-pozitif oranı
  iddia etmiyoruz.
