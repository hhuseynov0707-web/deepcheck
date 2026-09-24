# Veri Koruma Etki Değerlendirmesi — TASLAK

**Durum: taslak, hukuki incelemeden geçmedi.** Bu belge müşteri davranış profili katmanı içindir. Tasarım belgesi bu değerlendirmeyi herhangi bir pilot uygulamanın **ön koşulu** sayar. Bugün pilot yoktur: DeepCheck bir Teknofest prototipidir, gerçek müşterisi yoktur ve profil katmanı varsayılan olarak kapalı gelir (`PROFILE_LAYER=0`). Aşağıdaki her oran **sentetik kimlikler** üzerinde ölçülmüştür; gerçek müşteri verisi yoktur.

## 1. Neden gerekli

Katman iki bakımdan yüksek risklidir:

- Kişisel özelliklerin otomatik işlenerek değerlendirilmesidir (GDPR m. 35(3)(a) ile kıyaslanabilir). Profil yalnızca ek doğrulama isteyebilir, ama bu bir ödemenin o an tamamlanmamasına yol açar.
- Özel nitelikli veri işler (GDPR m. 35(3)(b) ile kıyaslanabilir). Bir kişinin fare ve klavye kullanım biçiminin, onu tanımak amacıyla kimliğine bağlı saklanması biyometrik veridir (KVKK m. 6). Takma ad kullanmak bunu değiştirmez.

KVKK'da bu adıyla bir etki değerlendirmesi zorunluluğu yoktur. Bu belge, GDPR m. 35 kıyasıyla ve iyi uygulama olarak hazırlanmıştır.

## 2. İşlemenin tanımı

- **Kimin verisi:** Satıcının, profillemeye açık rıza veren giriş yapmış müşterileri. Misafir ödemesinde müşteri referansı yoktur ve bu katmana hiçbir şey ulaşmaz.
- **Ne saklanır:** Satıcı ve müşteri referansının HMAC özeti (`profile_id`), giriş türü başına en fazla 20 referans ve 4 onay bekleyen oturum vektörü (oturum başına 12 normalleştirilmiş sayı), rıza kaydı, karar kayıtları ve erişim kayıtları. Saklama süreleri için bkz. `docs/kvkk-aydinlatma.md` bölüm 4.
- **Ne yapılır:** Yeni oturum, müşterinin aynı giriş türündeki kendi geçmişiyle tam konformal bir sıralamayla karşılaştırılır. Oturum geçmişin hepsinden daha uçsa (alfa 0,05) ve karar zaten onay ya da uyarı ise, karar **ek doğrulamaya** çevrilir. Başka hiçbir çıktı yoktur: engelleme, onay, skor değişikliği yoktur. Profile uyan oturum da bir avantaj kazanmaz.

## 3. Gereklilik ve orantılılık

- **Amaç:** Hesap ele geçirmeye karşı ek doğrulama. Kart deneme botlarına karşı katkısı yoktur; onlar misafir ödemesinden gelir.
- **Veri en aza indirme:** Profile ham davranış verisi girmez; yalnızca oturum başına 12 medyan girer. Ham müşteri referansı saklanmaz, loglanmaz ve URL'ye konmaz. Profil anahtarı, oturum imzalama anahtarından, pano anahtarından ve satıcı anahtarlarından farklı olmak zorundadır. Aynı olursa süreç başlamaz.
- **Rıza:** Profil yalnızca satıcının kimliği doğrulanmış rıza çağrısıyla oluşur. Rıza vermemek müşteriye hiçbir ek soru olarak yansımaz. İtiraz kaydı profilin yeniden açılmasını engeller.
- **Alternatif:** Profil olmadan da bot skoru ve ek doğrulama çalışır. Katman, yalnızca bir müşteri referansı varsa ve açıkça açıldığında devreye girer.

## 4. Riskler ve önlemler

| Risk | Önlem | Kalan risk |
|---|---|---|
| Aynı kişinin yanlışlıkla sorgulanması | Olgunluk eşiği 19 referans. Alfa 0,05 ile tam konformal sıralama. Kişi başına bütçe: 30 günde en fazla 3 **geçilen** sorgulama. Üst üste 3 geçilen sorgulamada profilin yeniden kurulması. | Sentetik kimliklerde aynı kişi %4,9 oranında sorgulandı (%95 GA %4,0–5,9). Bu bir **alt sınırdır**; gerçek kişilerdeki oran bilinmiyor. |
| Engellilik ya da sağlık durumunun dolaylı çıkarımı (titreme, yardımcı giriş), ayrımcılık | Katman hiçbir zaman engellemez. Giriş türleri ayrı tutulur. Bütçe ve yeniden kurma, sürekli sorgulanan kişiyi korur. | Giriş türü başına gerçek kişi ölçümü yok. Dokunmatik için sentetik ölçüm de yok. |
| Hesabı ele geçiren saldırganın katmanı kapatması | Bütçe yalnızca geçilen sorgulamalarla harcanır; yanıtlanmayan sorgulama bir onaya dönüşemez. Devre kesici farklı müşterileri sayar, tekrarlanan denemeleri saymaz. Müşteri başına karar sınırı aşıldığında profil okunmaz ve karar ek doğrulamaya gider. | Kodu oltalamayla ele geçirmiş bir saldırgan sorgulamayı geçer. Katmanın gücü satıcının ek doğrulama kanalının gücü kadardır. |
| Profil zehirleme | Onay bekleyen vektörler referans sayılmaz; yalnızca satıcı "ödeme doğru" dediğinde ya da 3 geçilen sorgulamalık bir seri olduğunda referansa terfi eder. | Ek doğrulama kanalını 3 kez üst üste kontrol eden saldırgan profili yeniden kurdurabilir. |
| Yeniden tanımlama | `profile_id` anahtarlı bir HMAC'tir. Anahtar diğer sırlardan ayrı tutulmak zorundadır. | Anahtar ele geçerse ve ham referanslar biliniyorsa bütün tablo yeniden tanımlanır. |
| Eksik silme | Silme ve itiraz; vektörleri ve profili siler, oturum, karar kaydı ve inceleme erişim kaydı bağlarını kaldırır, karar kayıtlarındaki oturum vektörlerini de siler. Silmeyle aynı anda verilen bir karar, silinen takma adı kendi kaydına yazamaz (PostgreSQL 16 üzerinde iki sırayla da doğrulandı). | Satıcının silme talebini iletmesine bağlıdır. |
| Demo sayfasında rıza olmadan veri | Demo ad alanında ziyaretçiden kalan her şey 24 saat içinde silinir. Sentetik demo müşterilerine hiçbir şey öğretilmez. | 24 saatten önce tek tek silme yolu yoktur. |
| Veritabanının çalınması | Sütun düzeyinde şifreleme yoktur. İstatistik her kararda yeniden hesaplandığı için anahtar aynı süreçte duracaktı ve kazanç yalnızca çevrim dışı disk hırsızlığıyla sınırlı kalacaktı. Koruma disk şifrelemesine bırakılmıştır. | Prototipte disk şifrelemesi çalıştırılan makinenin ayarına bağlıdır. |
| Profil olmadan otomatik engelleme | Profil katmanı açıkken onay dışındaki her karar 90 gün tutulan bir karar kaydı yazar. | Katman kapalıyken bot skoruyla engellenen bir ödemenin izi yalnızca 24 saatlik oturum kaydındadır. |

## 5. Pilot öncesinde yapılması gerekenler

1. Rol dağılımının (satıcı veri sorumlusu, DeepCheck veri işleyen) ve hukuki dayanakların hukuki incelemesi. Arayüzün kabul ettiği `contract_necessity` dayanağının özel nitelikli veri için geçerli olup olmadığı bu incelemede karara bağlanmalıdır.
2. Tasarım belgesinin §10.3 protokolü: bir gönüllünün en az 20 oturumu, 3 cihaz türünde. Bugüne kadar hiçbir eşik tek bir gerçek kişide doğrulanmadı.
3. Satıcının kendi ek doğrulama sonucunu bildirebileceği bir uç nokta. Bütçe ve yeniden kurma yalnızca **geçilen** sorgulamaları sayar; bugün geçme bilgisi yalnızca demo uç noktasından gelir. Bu uç nokta olmadan, demo dışında bu iki koruma devreye girmez.
4. Aydınlatma metninin (`docs/kvkk-aydinlatma.md`) satıcının kendi metnine eklenmesi ve satıcının rıza akışının incelenmesi.
5. Gerçek trafikte giriş türü başına sorgulama oranı raporu. Katman önce gölge modda (`PROFILE_ESCALATION=0`) ölçülmelidir.

## 6. Sonuç

Taslak haliyle bu değerlendirme bir pilotu **desteklemez**. Katman bir mekanizma olarak uçtan uca çalışıyor, ancak doğruluğu yalnızca sentetik kimliklerde ölçüldü ve 5. bölümdeki maddeler açık. Katman bu yüzden kapalı gelir.
