# KVKK Aydınlatma Metni - DeepCheck prototipi

**Bu metin bir prototip içindir ve hukuki incelemeden geçmemiştir.** DeepCheck, Teknofest Finansal Teknolojiler Yarışması için geliştirilen bir prototiptir. Gerçek bir müşteri tabanı yoktur; sunumda adı geçen "demo müşterileri" (Ayşe, Mehmet, Zeynep) sentetiktir, yani simülatörle üretilmiştir ve gerçek kişi değildir. Bu metin, kodun bugün ne yaptığını anlatır. Bir satıcıya entegre edildiğinde veri sorumlusunun satıcı, DeepCheck'in ise satıcı adına veri işleyen olması öngörülmektedir. Bu rol dağılımı da hukuki incelemeyi beklemektedir.

Bu metin **TechStore** mağazasının ödeme sayfasında gösterilir. Bölüm 1-5 DeepCheck'in genel kurallarıdır; mağazanın kendi sunucusunun neyi aldığı bölüm 6'dadır.

## 1. Hangi veriler toplanır

Tarayıcıdaki DeepCheck SDK'sı ödeme sayfasında şu davranış verilerini toplar ve yaklaşık iki saniyede bir DeepCheck sunucusuna gönderir (TechStore'da mağazanın adresi üzerinden, bölüm 6):

- Fare ve dokunmatik işaretçi hareketleri: konum ve zaman damgası.
- Tıklama zamanları ve konumları.
- Kaydırma olayları ve hareketten önceki duraksama süreleri.
- Klavye olayları: **yalnızca tuşa basılma anı** kaydedilir. Hangi tuşa basıldığı ya da ne yazıldığı hiçbir zaman gönderilmez.
- Sekmenin ya da pencerenin odağı kaybettiği anlar.
- İşaretçi türü sayaçları (fare, kalem, dokunmatik), olayların kaynağına ilişkin sayaçlar ve tarayıcının otomasyon bayrağı (`navigator.webdriver`).
- Oturum açılırken tarayıcının bildirdiği iki çalışma zamanı ölçümü: saat çözünürlüğü ve zamanlayıcı gecikmesi.

Kart numarası, ad, e-posta ya da telefon numarası SDK tarafından **toplanmaz**. Oturum, sunucunun ürettiği rastgele bir kimlikle izlenir.

**IP adresi veritabanına yazılmaz.** Sunucu, aynı adresten kısa sürede açılan oturum sayısını sınırlamak için adresi yalnızca çalışan sürecin belleğinde tutar. Arka uç (uvicorn) ve ön yüz (nginx) erişim günlükleri kapalıdır. nginx'in hata günlüğü açıktır ve bir hata satırında istemci adresini yazabilir.

## 2. Hangi amaçla işlenir

- **Bot tespiti.** Her gönderimden 12 davranış özelliği çıkarılır ve bir Random Forest modeli 0-100 arası bir risk skoru hesaplar. Ödeme anında sunucu bu skora ve biriken kanıta göre `allow` (onay), `warn` (uyarı), `verify` (ek doğrulama) ya da `block` (engelleme) kararı verir. Bu, satıcının ödeme sayfasındaki otomatik kötüye kullanımı engellemek içindir.
- **Müşteri davranış profili (varsayılan olarak kapalı).** Satıcı açık rızayı aldıysa, müşterinin kendi geçmiş oturumlarından bir profil tutulabilir. Profilin **tek işlevi ek doğrulama istemektir.** Profil hiçbir zaman ödemeyi engellemez, onaylamaz ve risk skorunu değiştirmez. Profile uyan bir oturum da hiçbir avantaj kazanmaz. Amaç, hesabı ele geçirilmiş bir müşterinin yerine ödeme yapan birine ek soru sormaktır. Profilden sapma bir hüküm değildir; yalnızca ek doğrulama gerekçesidir. Örneğin büyükanne adına ödeme yapan torun, doğru kodu girerek ödemeyi tamamlar.

## 3. Hukuki dayanak

- **Müşteri davranış profili:** Bir kişinin fare ve klavye kullanım biçiminin, onu tanımak amacıyla ve kimliğine bağlı olarak saklanması biyometrik veri, dolayısıyla özel nitelikli kişisel veri sayılmaktadır (KVKK m. 6). Bu nedenle profil yalnızca **açık rıza** ile açılmalıdır. Profil yalnızca satıcının kimliği doğrulanmış rıza çağrısıyla oluşturulabilir; ödeme kararı profil yaratamaz. Tek istisna ayrılmış `demo` ad alanıdır: orada yalnızca sunum bilgisayarında çalıştırılan `demo_seed.py` aracının oluşturduğu sentetik demo müşterileri bulunur ve ödeme sayfasından bu ad alanına hiçbir şey yazılmaz (bölüm 4). Rıza yoksa profil satırı da yoktur, katman hiçbir şey yapmaz ve **rıza vermemek müşteriye hiçbir ek soru olarak yansımaz.** Arayüz ayrıca `contract_necessity` (sözleşmenin ifası) değerini kabul etmektedir. Özel nitelikli veri için bu dayanağın geçerli olup olmadığı hukuki incelemeyi beklemektedir ve bu değer kullanılmamalıdır.
- **Oturum düzeyindeki bot analizi:** Kişiyi tanımlamaz. Rastgele bir oturum kimliğine bağlıdır; oturum ve özellik satırları 24 saat sonra silinir. Profil katmanı açıkken satıcının istediği ödeme kararı ayrıca karar kaydına yazılır (müşteri referansı taşımayan bir onay hariç) ve 90 gün saklanır (bölüm 4). Dayanağını ürünü kullanan satıcı belirler.

## 4. Ne kadar süre saklanır

| Saklanan | Nedir | Saklama süresi |
|---|---|---|
| Ham davranış verisi (fare yolu, tıklamalar, tuş zamanları) | SDK'nın gönderdiği ham olaylar | 1 saat, ardından boşaltılır |
| Oturum ve özellik satırları | 12 özellik, risk skoru, etiket | 24 saat |
| `profile_id` | Satıcı kimliği ve müşteri referansının HMAC özeti. Ham referans hiçbir zaman saklanmaz. | Silme talebine ya da 180 gün hareketsizliğe kadar |
| Giriş türü başına en fazla 20 referans ve 4 onay bekleyen oturum vektörü | Oturum başına 12 normalleştirilmiş sayı ve bunların oturum içindeki dağılımı. Ham davranış verisi profile hiçbir zaman girmez. | 180 gün |
| Rıza dayanağı ve zamanı | Hukuki dayanak kaydı | Profille birlikte |
| Karar kaydı (yalnızca profil katmanı açıkken yazılır) | Oturum kimliği, satıcı kimliği, karar, iç gerekçe ve satıcıya söylenen genel gerekçe, karar anındaki risk skoru, satıcının bildirdiği tutar bandı (düşük, orta, yüksek), profil durumu. Profil varsa profil kimliği, sapma ve en çok sapan özellikler; sapma bulunduysa karşılaştırılan oturum vektörü de. Ham müşteri referansı hiçbir zaman yazılmaz. | 90 gün |
| Erişim kaydı | Profili kimin, ne zaman incelediği | 365 gün |
| Demo ad alanı (yalnızca `demo_seed.py` aracının oluşturduğu sentetik demo müşterileri; ödeme sayfasından buraya yazılmaz, TechStore ödemeleri bu ad alanında değildir) | Simülatörle üretilmiş geçmiş vektörleri, sentetik profil ve bu müşteriler adına simüle edilmiş oturumların karar kaydı. Gerçek bir kişiye ait veri içermez. | Karar kaydı 24 saat; sentetik geçmiş, araç sıfırlanana (`--reset`) ya da en fazla 180 güne kadar |

Süreler kodda varsayılan değerlerdir ve bir veri koruma etki değerlendirmesiyle (`docs/dpia.md`) onaylanmalıdır. Temizlik işlemi 10 dakikada bir çalıştığından silme en fazla 10 dakika gecikebilir. Uygulama verileri kendisi şifrelemez; koruma sunucu diskinin şifrelenmesine bırakılmıştır. Prototipte bu, çalıştırıldığı makinenin ayarına bağlıdır. Sütun düzeyinde şifrelemenin neden yapılmadığı `docs/dpia.md`'de anlatılır.

## 5. Haklarınız ve başvuru yolu

KVKK m. 11 uyarınca verilerinizin işlenip işlenmediğini öğrenme, bilgi isteme, düzeltme, silme, itiraz ve zararın giderilmesini isteme haklarınız vardır. Otomatik analiz sonucunda aleyhinize bir sonuç çıkmasına itiraz hakkınız da vardır (m. 11/1-g). Başvurular veri sorumlusu olan satıcıya yapılır. Satıcı bunları şu yollarla uygular:

- **Silme:** Satıcı `POST /api/profile/erase` çağrısını `erase` türüyle yapar. Profil ve bütün vektörleri silinir. Oturumlar, karar kayıtları ve erişim kayıtlarıyla bağı kaldırılır. Karar kayıtlarındaki karşılaştırılan oturum vektörleri de silinir.
- **İtiraz (profillemeye karşı):** Aynı çağrı `object` türüyle yapılır. Bütün veriler silinir, ancak bir itiraz kaydı tutulur. Bu kayıt, profilin daha sonra yeni bir rıza çağrısıyla yeniden açılmasını engeller.
- **İnsan incelemesi:** Profil nedeniyle ek doğrulama istenen bir karara itiraz edildiğinde yetkili bir inceleme görevlisi `GET /api/profile/review/{session_id}` ile neyin neyle karşılaştırıldığını görür. Bu uç nokta panonun ortak anahtarını kabul etmez, görevli başına ayrı bir anahtar ister ve her okuma bir erişim kaydı bırakır. Karar kaydı 90 gün tutulduğu için inceleme, oturum verisi silindikten sonra da yapılabilir.

Profil katmanı kapalıyken bir ödeme bot skoru nedeniyle engellenirse bu kararın izi yalnızca 24 saatlik oturum kaydındadır. İnsan incelemesi için ayrı bir karar kaydı tutulmaz. Bu, prototipin bilinen bir eksiğidir.

## 6. TechStore mağazası: hangi sunucu neyi alır

TechStore kurgusal bir mağazadır. Ödeme sayfası (DemoPay), mağazanın kendi sunucusu ve DeepCheck çekirdeği bu prototipte aynı ekip tarafından, sunum bilgisayarında çalıştırılır; gerçek bir entegrasyonda mağaza sunucusu satıcınındır. Ödeme sayfası bir **misafir ödemesidir**: e-posta adresi, hesap ya da başka bir kimlik bilgisi istemez. Bu sayfada ödeme yaptığınızda veriler şöyle akar:

- **Davranış verisi** (bölüm 1: işaretçi ve kaydırma hareketi, tıklama ve tuşa basılma zamanları; hangi tuşa basıldığı asla) tarayıcıdan mağazanın adresine gider ve mağazanın yönlendiricisi (nginx) üzerinden DeepCheck çekirdeğine iletilir. Çekirdek sayfaya yalnızca bir alındı yanıtı verir: risk skoru, etiket ve gerekçe sayfaya hiç gönderilmez, tarayıcının geliştirici araçlarında da görünmez.
- **Mağaza sunucusuna giden:** oturum kimliği, oturum jetonu ve kartın görünen alanları (son 4 hane, kart türü, son kullanma tarihi). Başka bir şey gönderilmez. Mağaza sunucusu kartın görünen alanlarını DeepCheck'e iletmez.
- **Tam kart numarası, CVV ve kart üzerindeki isim** tarayıcıdan hiç çıkmaz: ne mağaza sunucusuna ne DeepCheck'e gönderilir. Gerçek bir mağazada kart bilgilerini ödeme kuruluşu alır; bu demoda hiçbir yere gitmezler.
- **DeepCheck'e giden:** oturum kimliği ve oturum jetonu, sepet tutarının bandı (düşük, orta, yüksek; tutarın kendisi değil) ve takma adlı bir müşteri referansı. Mağaza sunucusu bu referansı **her ödeme oturumu için ayrı** üretir: oturum kimliğinden, yalnızca kendisine verilen ayrı bir anahtarla (HMAC-SHA256), `misafir-` önekiyle. Referans sizin hakkınızda hiçbir bilgi taşımaz; kişiyi değil, o tek ödeme oturumunu adlandırır ve iki ödeme bu referans üzerinden birbirine bağlanmaz. Çekirdeğe bu anahtar verilmez. Referans, çekirdeğin bu ödemenin kararını karar kaydına yazabilmesi için gönderilir. Bu prototipte iki sunucunun ayarları aynı bilgisayardaki aynı dosyadadır; ayrım, anahtarın yalnızca mağaza sunucusuna verilmesiyle sağlanır.
- **Karar kaydı.** Mağaza sunucusu ödeme anında çekirdeğe karar sorar. Kanıt yetersizse 2 saniye arayla en fazla 3 kez daha sorar; ek doğrulama kodu girilirse bir kez daha sorar. Profil katmanı açıkken (canlı jüri demosunda açıktır) her yanıt karar kaydına yazılır; yani bir ödeme denemesi 4 kayda kadar, kod adımı bir kayıt daha bırakır. Bu kayıtlar mağazanın satıcı kimliğiyle tutulur, ayrılmış demo ad alanında değil; bu yüzden demo ad alanının 24 saatlik silmesi (bölüm 4) bunlar için **geçerli değildir**. Kodda varsayılan saklama süresi **90 gündür** (`DECISION_AUDIT_RETENTION_DAYS`). İçerikleri bölüm 4'teki "Karar kaydı" satırındadır; müşteri referansı ve profil kimliği bulunmaz.
- **Profil yok.** Mağaza rıza çağrısı yapmaz: TechStore müşterileri için davranış profili oluşturulmaz ve hiçbir oturum bir profile öğretilmez. Çekirdek, aynı referans için bir saatte istenen karar sayısını sınırlamak üzere referanstan türetilen özeti yalnızca çalışan sürecin belleğinde tutar; bu sayaç veritabanına yazılmaz ve süreç yeniden başlatıldığında silinir.
- **Silme.** Ödeme sayfası sizden kimlik bilgisi almadığı için karar kayıtlarını size bağlayan bir bilgi (ad, e-posta adresi, müşteri referansı, profil kimliği) yoktur; kayıtlar yalnızca rastgele oturum kimliğini taşır. Bu yüzden bir silme başvurusunda bu kayıtlar size ait olarak bulunamaz; 90 günün sonunda kendiliğinden silinir. Ham davranış verisi ve oturum satırları bölüm 4'teki sürelerle silinir.
- **Bağlantı.** Canlı demoda bu sayfa yerel ağ üzerinden şifresiz `http` ile açılır. Kartın görünen alanları, doğrulama kodu, oturum bilgileri ve davranış verisi ağda şifrelenmeden taşınır ve tarayıcı adres çubuğunda "Güvenli değil" uyarısı gösterir. Gerçek bir ödeme sayfası `https` kullanır. Bu sayfaya gerçek kart bilgisi ya da gerçek kişisel bilgi girmeyin.

## 7. Yurt dışına aktarım

Prototipin sunucuları Docker Compose ile tek bir makinede çalışır; canlı demoda ödeme sayfası aynı yerel ağdaki ikinci bir bilgisayarın tarayıcısından açılır. Veriler yurt dışına aktarılmaz ve üçüncü bir tarafa gönderilmez.
