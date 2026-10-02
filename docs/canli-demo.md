# Canlı jüri demosu — iki bilgisayar, iki uygulama

Bu belge sahnedeki akışı anlatır: bir ekip arkadaşı **kendi bilgisayarından**
TechStore mağazasında elle ödeme yapar, jüri **SOC panelinde** oturumu canlı
izler; ardından **aynı bilgisayardan** bir betik (bot) aynı mağazaya saldırır ve
jüri sistemin tepkisini görür. Mimari: `docs/architecture-two-apps.md`.

> **Dürüstlük notu.** Sahnedeki bot kasıtlı olarak modelin tanıdığı **en basit**
> bot tipidir: parametreleri modelin eğitiminde kullanılan «bot» üretecinden
> alınmıştır (aşağıda "Bot ne yapıyor"). Engellenmesi, sistemin uçtan uca
> çalıştığını gösterir; gerçek botların ya da insan taklidi yapan akıllı botların
> engellendiğini göstermez. Fareyi hiç kullanmayan ya da insan hareketini taklit
> eden betikler bu modelden **geçer** (ölçüm aşağıda). Jüri sorarsa bunu açıkça
> söyleyin; sahnede böyle bir bot denemeyin.

## Ne nerede çalışır

| | Bilgisayar A (sunum) | Bilgisayar B (ekip arkadaşı) |
|---|---|---|
| Ne çalışır | Docker: çekirdek API, mağaza (sayfa + sunucu), SOC (sayfa + sunucu) | Tarayıcı (Chrome/Edge) + `cmd` |
| Projeksiyonda | **SOC paneli:** `http://localhost:3100` (anahtarla giriş) | — |
| B'nin açtığı | — | **Mağaza:** `http://IP:3000` |
| Gereken | Bu depo, Docker Desktop, `.env` | Python 3.9+ ve **tek dosya**: `live_bot.py`, Masaüstü'nde (kurulum gerekmez) |

- **Mağaza (TechStore, DemoPay ödeme sayfası)** ödeyen kişiye **hiçbir skor,
  etiket ya da analiz göstermez**: sade bir ödeme sayfasıdır. Kararı mağazanın
  kendi sunucusu, çekirdeğin `POST /api/decision` uç noktasına sorar —
  gerçek entegrasyon yolu budur.
- **SOC** her şeyi gösterir: oturumlar, skorlar, SHAP, grafik, kaydedilen
  karar. Kendi sunucusu pano anahtarını tutar; anahtar tarayıcıya hiç gitmez.
- Ağa yalnızca mağazanın **3000** portu açılır. SOC (3100) ve çekirdek API
  (8000) yalnızca bilgisayar A'dan açılır. Eski tek sayfalı demo (3200)
  2026-10-02'de silindi; o port artık kullanılmıyor.
- Mağaza bir **misafir ödemesidir**: e-posta adresi, hesap ya da başka bir
  kimlik bilgisi istemez. Sipariş özetinde yalnızca tutarlar (Ara toplam, KDV,
  Toplam) görünür.

## Bir gün önce

**Bilgisayar A**

1. `.env` içinde şunlar olsun:
   - `DEBUG=0`, gerçek `DEEPCHECK_SECRET` ve `DASHBOARD_KEY`, `DEMO_ENDPOINTS=1`
     ve **`BIND_ADDR=0.0.0.0`** (yalnızca mağazanın 3000 portunu ağa açar).
   - **Profil katmanı:** `PROFILE_LAYER=1`, `DEEPCHECK_PROFILE_KEY=<token>` ve
     `DEEPCHECK_MERCHANT_KEYS=demo-magaza:<başka bir token>`. Kurallar
     `.env.example` içindeki profil katmanı bloğunda (`DEEPCHECK_MERCHANT_KEYS`:
     anahtarlar birbirinden ve diğer sırlardan farklı olmalı, satıcı anahtarı en
     az 32 karakter; `demo` kimliği ayrılmıştır), örnek değerler ve anahtar
     üretme komutu **"Jury prototype"** bloğunda. **Neden gerekli:** SOC
     panelindeki **"Son kaydedilen karar"** satırı `decision_audit` tablosundan
     okunur ve bu tabloya yalnızca profil katmanı açıkken yazılır.
   - **Mağaza sunucusu:** `CHECKOUT_MERCHANT_ID=demo-magaza`,
     `CHECKOUT_MERCHANT_KEY=<DEEPCHECK_MERCHANT_KEYS'teki aynı anahtar>` ve
     `CHECKOUT_CUSTOMER_REF_KEY=<ayrı bir token: en az 32 karakter, satıcı
     anahtarından farklı>`. Mağaza kararı bu kimlikle sorar. Müşteri referansını
     her ödeme oturumu için ayrı, oturum kimliğinden bu ayrı anahtarla türetir
     (`misafir-...`); referans ödeyen kişi hakkında hiçbir şey taşımaz. Üç satırdan
     biri eksikse mağaza sunucusu başlamaz.
   - **SOC sunucusu:** `SOC_SESSION_SECRET=<yeni bir token>` (SOC giriş
     çerezini imzalar).
   - `PROFILE_ESCALATION` 0 ya da 1 olabilir. Mağazada fark etmez: mağaza
     müşterileri için profil oluşturulmaz (profil yalnızca açık rızayla oluşur),
     karar "profil yok" durumuyla verilir ve kaydedilir. Sentetik müşteri
     komutu (aşağıda) için de zorunlu değildir: 0 iken karşılaştırma yine
     yapılır ve gölge modda kaydedilir.
2. Bir kez derleyip başlatın: `docker compose up -d --build`
3. Kontrol (A'nın tarayıcısında):
   - `http://localhost:3000/api/health` → `"core": true`
   - `http://localhost:3000/deepcheck/api/health` → `"model_loaded": true`
   - `http://localhost:3100` → SOC giriş ekranı

   **502 Bad Gateway** görürseniz çekirdek henüz istek kabul etmiyor:
   `docker compose logs -f backend` ile bakın. «Modeller egitiliyor» yazıyorsa
   bekleyin (backend'in kendi tahmini 4–8 dakika) ve **yeniden başlatmayın,
   yeniden derlemeyin**: eğitim baştan başlar. Bunu sahnede yaşamayın.
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
   her Allow kuralından önce gelir. PowerShell'de bakın:

   ```powershell
   Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue | Where-Object DisplayName -like "*docker*"
   ```

   Çıktı boşsa böyle bir kural yok. Bir kural listeleniyorsa en hızlı çözüm ağı
   **Private** yapmaktır.

**Bilgisayar B**

1. `python --version` → 3.9 veya üstü. Yoksa python.org'dan kurun ve kurulumun
   ilk ekranında **"Add python.exe to PATH"** kutusunu işaretleyin. İşaretlenmediyse
   `python` Microsoft Store'u açar; o zaman her komutta `python` yerine `py`
   yazın.
2. `lab\live_bot.py` dosyasını B'nin **Masaüstü'ne** kopyalayın (yalnızca
   standart kütüphane kullanır). Kontrol, yeni bir `cmd` penceresinde:

   ```bat
   cd %USERPROFILE%\Desktop
   dir live_bot.py
   ```

   Dosya listelenmiyorsa Masaüstü OneDrive'a taşınmıştır: komutu yazarken
   `python ` yazdıktan sonra dosyayı cmd penceresine sürükleyip bırakın,
   ardından ` --url http://IP:3000` ekleyin.
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
3. B'nin tarayıcısında `http://IP:3000/api/health` → `"core": true`. Açılmıyorsa
   sorun ağda ya da güvenlik duvarındadır, uygulamada değil.
4. **Prova:** B'de mağazada bir kez elle ödeyin, sonra `cmd`'de botu bir kez
   çalıştırın (aşağıdaki komut); `ÖDEME REDDEDİLDİ` görmelisiniz. Botu art arda
   çok çalıştırmayın: dakikada ~10 oturum açma ve 10 ödeme isteği sınırı var
   (aşılırsa 60 saniye bekleyin). Provalar sahnedeki ödemenin müşteri başına
   karar bütçesini tüketemez: her ödeme kendi misafir referansıyla gelir, yani
   her provanın bütçesi kendisinindir. Yeni bir adres ya da kimlik gerekmez.
5. A'da SOC'u açın (`http://localhost:3100`), pano anahtarını (`DASHBOARD_KEY`)
   girin. Giriş 8 saat geçerlidir; **"Oturumu Kapat"a basmayın.**
   "Canlı takip: Açık" olsun.
6. **Provadaki bütün mağaza sekmelerini B'de kapatın, sonra SOC'ta
   "Görünümü sıfırla"ya basın** (veri silinmez, yalnızca görünüm). Panel
   sıfırlamadan sonra *başlayan* oturumları gösterir; açık kalan bir prova
   sekmesi yeniden kullanılırsa ~12 saniye sonra panelde geri belirir.
7. Projektör 1280 pikselden darsa tarayıcı yakınlaştırmasını %80–90 yapın.

## Sahne akışı

### 1. İnsan ödemesi (B, tarayıcı)

1. B'de **yeni bir sekmede** `http://IP:3000` açılır (her yükleme yeni bir
   oturumdur).
2. Ekip arkadaşı formu **fare ve klavyeyle, elle** doldurur: önce 2–3 saniye
   doğal fare hareketi, sonra kart numarası, kart üzerindeki isim (yalnızca
   harf; alan rakamları ve sembolleri yazıldığı anda atar), son kullanma, CVV. Gerçek kart bilgisi girmeyin.
   **15–25 saniye** sürsün. Yapıştırma ve otomatik doldurma yok; Backspace'i **basılı tutmayın**.
3. Fareyle **"₺2.038,80 Öde"** düğmesine basılır. Mağaza kararı sorarken düğme
   "İşleniyor…" der; kanıt azsa mağaza sunucusu birkaç saniye bekleyip yeniden
   sorar (en fazla ~6 saniye). Sonuç: **"Ödeme alındı"** makbuzu.

Mağaza sayfası skor göstermez; jüri sonucu **SOC'ta** görür: yeni oturum
otomatik seçilir, ilk birkaç saniye **"Değerlendiriliyor"** görünür (karar için
en az 3 gözlenen pencere gerekir), ardından **Gerçek Kullanıcı** ve
**"Son kaydedilen karar: Onaylandı"**.

### 2. Bot saldırısı (B, yeni cmd penceresi)

Tarayıcı sekmesini kapatın, yeni bir `cmd` açın:

```bat
cd %USERPROFILE%\Desktop
python live_bot.py --url http://IP:3000
```

**Bot çalışırken cmd penceresinin içine tıklamayın.** Klasik Windows konsolunda
pencereye tıklamak metin seçimi başlatır ve seçim sürdükçe program durur. Yanlışlıkla
tıklandıysa hemen **Esc**'e basın.

Bot yaklaşık 18 saniye davranış gönderir, sonra mağazadan ödeme ister. Sonuç:
**`MAĞAZA YANITI : ÖDEME REDDEDİLDİ - satıcı sebep söylemez; sebep yalnızca SOC panelinde`**.
SOC panelinde (A): yeni oturum otomatik seçilir, **Bot Tespit Edildi** ve
**"Son kaydedilen karar: Engellendi"**.

### Bot ne yapıyor

Tarayıcı açmaz. SDK'nın protokolünü kendisi konuşur (oturum, iş kanıtı,
doğrulama), mağazanın sayfasının yaptığı gibi davranış penceresi gönderir, sonra
mağazanın `POST /api/checkout` uç noktasından ödeme ister. Davranışı:
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
davranışı eğitimde gördü. Engellenmesi, sistemin uçtan uca çalıştığını gösterir;
modelin genellediğini göstermez. Gerçek kart deneme botlarının ne kadarının böyle
davrandığını **ölçmedik**.

### Söylenebilecek kısa metin

- İnsan: "Ekip arkadaşımız kendi bilgisayarından, sıradan bir ödeme sayfasında
  ödüyor; sayfada hiçbir skor yok. Sistem kart bilgisini hiç görmüyor: fare
  hareketini, tıklamaları, kaydırmayı ve tuşlara basılma zamanlarını ölçüyor,
  hangi tuşa basıldığını değil. Kararı mağazanın sunucusu bizim API'mize soruyor."
- Bot: "Şimdi aynı bilgisayardan bir otomasyon betiği aynı ödemeyi deniyor. Bu
  kasıtlı olarak modelin tanıdığı en basit bot tipi. Mağaza ödemeyi reddediyor
  ama nedenini söylemiyor; nedeni yalnızca güvenlik ekibi SOC'ta görüyor."
- Her iki sonuç için: "Bu bir canlı gösterim, bir doğruluk ölçümü değil.
  Gerçek kullanıcı verimiz henüz yok; ölçtüğümüz tek gerçek kişi var."

### Sentetik müşteri komutu (yalnızca A, isteğe bağlı)

Sentetik müşterilerin (Ayşe/Mehmet/Zeynep) artık bir sayfası yok: onları
gösteren eski tek sayfalı demo 2026-10-02'de silindi, mağaza ise hiçbir zaman
sentetik bir müşteri adına ödemez (her ödeme kendi misafir referansıyla gelir).
Komut satırından çalışır, A'da:

```bat
docker compose exec backend python demo_seed.py
docker compose exec backend python demo_seed.py --simulate ayse
```

İlk komut sentetik geçmişi yükler (tekrar çalıştırılabilir). İkincisi aynı
sentetik kimlikten **yeni** bir oturumu çekirdeğin `/api/demo/charge` yolundan,
ayrılmış `demo` ad alanında geçirir ve kararı yazdırır; SOC oturumu "Sentetik
demo verisi" rozetiyle gösterir. Beklenen sonuç ek doğrulama istenmemesidir.
Bunun karşıtı, yani gerçek bir kişinin sentetik bir müşteri adına ödeyip
geçmişten saptığı için ek doğrulamaya düşmesi, bugünkü yapıda **gösterilemez**:
o yalnızca silinen sayfada vardı. Ayrıntı: `docs/juri-cevaplari.md`.

## Bir şey ters giderse

| Durum | Ne yapılır |
|---|---|
| İnsan doğrulama penceresi ("Bankanız ödemeyi doğrulamak istiyor") alırsa | Ekrandaki demo kodunu (482913) girip Doğrula. Söylenecek: "Sistem emin olmadığında kartı çekmiyor, 3-D Secure gibi ikinci kanıt istiyor. Gerçek bir 3-D Secure akışında kod kart sahibinin telefonuna gider; bu demoda kod ekranda yazılı." |
| İnsan "Ödemeniz tamamlanamadı" alırsa | SOC'ta SHAP özelliklerini gösterin; "gerçek kullanıcı verisi toplamamızın sebebi tam olarak bu" deyin. Jüri önünde aynı şeyi zorlamayın |
| İnsan "Ödeme şu anda işlenemiyor" alırsa | Mağaza çekirdeğe ulaşamadı: A'da `docker compose ps` ve `docker compose logs backend` |
| SOC'ta seçili oturum insanınki değilse | Ödeme bir prova sekmesinde yapılmış olabilir. İnsanın oturumunu listeden **elle** seçin; bot perdesinden önce "Canlı takip"i yeniden açın |
| Bot `EK DOĞRULAMA İSTENDİ - bot kodu bilmiyor` derse | Mağaza kartı çekmedi ama engellemedi de. Gerçek nedeni SOC'ta "Son kaydedilen karar" altında okuyun; "Karar ertelendi" yazıyorsa bu bir tespit değil, botun verisi yetersiz kaldı. Engelleme gibi anlatmayın; botu bir kez daha çalıştırın |
| Bot `ÖDEME ALINDI` derse | Gizlemeyin. Söylenecek: "Bu betik provalarda engellendi (aşağıda sayılar); bu çalıştırma geçti. Bu bir doğruluk ölçümü değil; model eğitimde gördüğü bot tipini bile her seferinde yakalamıyor." |
| Bot "Bağlantı koptu" derse | Ağ kesintisi; bu çalıştırmanın sonucu yok (engellendi sayılmaz). Ağı kontrol edip bir kez daha çalıştırın |
| Bot "Bu adreste DeepCheck API'si yok" derse | Adres yanlış ya da A'daki imajlar eski: adres `http://IP:3000` olmalı; A'da `docker compose up -d --build` |
| Bot "Sunucuya ulaşılamadı" derse | Ağ/güvenlik duvarı. Yedek: botu A'da, deponun kök klasöründe çalıştırın: `python lab\live_bot.py --url http://localhost:3000` |
| Bot "HTTP 429" derse | 60 saniye bekleyin (oturum açma/ödeme sınırı) |
| `python` "can't open file" derse | cmd yanlış klasörde: `cd %USERPROFILE%\Desktop`, ya da dosyayı cmd penceresine sürükleyip bırakın |
| SOC giriş ekranına dönerse | Anahtarı yeniden girin (giriş 8 saat geçerli) |
| SOC "Karar kaydı yok" derse | A'nın `.env`'inde profil katmanı satırları eksik (yukarıda A, adım 1) ya da ödeme isteği henüz gönderilmedi |
| İki bilgisayar birbirini görmezse | Her şeyi A'da yapın: insan A'nın tarayıcısında `http://localhost:3000`, bot A'nın cmd'sinde, deponun kök klasöründe |

## Ölçülenler (2026-10-01; modeller 2026-09-25'te eğitildi)

- **Mağaza yolu, canlı** (yeni iki uygulamalı yapı, bilgisayar A, `--url
  http://127.0.0.1:3000`, Docker'daki `backend/model-sklearn1.5.0.pkl`):
  **11 çalıştırmanın 11'i reddedildi**; her biri çekirdekte `block` olarak,
  `demo-magaza` satıcısı adına kaydedildi, karar anındaki skor 93,8–95,5.
- **Eski yol, canlı** (`/api/demo/charge`, çekirdek `:8000`, aynı bundle):
  20/20 engellendi (94,3–95,8), sonra o zamanki eski demonun nginx'i üzerinden
  ve cmd.exe'den 3/3. Eski demo 2026-10-02'de silindi; bu yol bugün yalnızca
  `live_bot.py --legacy` ile, çekirdeğe doğrudan (`http://localhost:8000`,
  yalnızca A) çalıştırılabilir.
- **Çevrimdışı:** botun kendi zaman çizelgesi, sunucunun skorlama ve karar
  kurallarından geçirildi, **ana bilgisayardaki bundle** ile
  (`backend/model-sklearn1.8.0.pkl`): **200 rastgele çalıştırmanın 200'ü
  engellendi**, en düşük oturum skoru 93,2, medyan 94,8.
- **Eğitim verisiyle ilişkisi:** botun parametreleri modelin eğitimdeki «bot»
  üretecinden alınmıştır. Gerçek kart deneme botlarının ne kadarının böyle
  davrandığı **ölçülmedi**.
- **Bilinen boşluk:** aynı bot zaman çizelgesinin fare akışı çıkarılmış hâli
  (yalnızca tıklama + 1–4 ms aralıklı tuş; 6 farklı ayar × 20 çalıştırma,
  çevrimdışı, ana bilgisayardaki bundle) **120'nin 111'inde onaylandı**, 9'unda
  karar verilemedi; ayar başına medyan oturum skoru 2,5–21. Gerçek Chromium'da
  fareyi kullanmadan yazan betik canlı sunucuda 2/2 onaylandı (skor 4,4 ve 13,3).
  İnsan hareketini taklit eden betikler de geçiyor (docs/evaluation.md). Bu
  senaryolar sahnede gösterilmez; sorulursa açıkça söylenir.
- **Mağaza akışının tesisatı** (bir betikle, insan değil; e-posta alanı
  kaldırılmadan önceki sürümde): doğrulama penceresi, yanlış kod reddi, doğru
  kodla onay ve ödeme uçtan uca çalıştı; ödeyen sayfada hiçbir durumda
  skor/analiz kelimesi görünmedi; tarayıcı mağazaya yalnızca oturum kimliği,
  jeton, o zamanki e-posta alanı ve kartın son 4 hanesi/markası/son kullanma
  tarihini gönderdi. Bugünkü gövde e-postasızdır (oturum kimliği, jeton,
  kartın görünen alanları); bunu mağazanın birim testleri sabitler
  (`apps/checkout/src/pages/Checkout.test.jsx`,
  `apps/checkout-server/test_checkout_api.py`), bu ölçüm değil.
- **Gerçek insan tarafı:** tek bir gerçek kişi ölçüldü (p01). Yeni mağaza
  sayfasında henüz gerçek bir kişi ödemedi; ilk prova bunu ölçecek.
  Yanlış-pozitif oranı iddia etmiyoruz.
