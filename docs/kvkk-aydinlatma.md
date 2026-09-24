# KVKK Aydınlatma Metni — DeepCheck prototipi

**Bu metin bir prototip içindir ve hukuki incelemeden geçmemiştir.** DeepCheck, Teknofest Finansal Teknolojiler Yarışması için geliştirilen bir prototiptir. Gerçek bir müşteri tabanı yoktur; jüri demosundaki "demo müşterileri" sentetiktir, yani simülatörle üretilmiştir ve gerçek kişi değildir. Bu metin, kodun bugün ne yaptığını anlatır. Bir satıcıya entegre edildiğinde veri sorumlusunun satıcı, DeepCheck'in ise satıcı adına veri işleyen olması öngörülmektedir. Bu rol dağılımı da hukuki incelemeyi beklemektedir.

## 1. Hangi veriler toplanır

Tarayıcıdaki DeepCheck SDK'sı ödeme sayfasında şu davranış verilerini toplar ve yaklaşık iki saniyede bir sunucuya gönderir:

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

- **Bot tespiti.** Her gönderimden 12 davranış özelliği çıkarılır ve bir Random Forest modeli 0–100 arası bir risk skoru hesaplar. Ödeme anında sunucu bu skora ve biriken kanıta göre `allow` (onay), `warn` (uyarı), `verify` (ek doğrulama) ya da `block` (engelleme) kararı verir. Bu, satıcının ödeme sayfasındaki otomatik kötüye kullanımı engellemek içindir.
- **Müşteri davranış profili (varsayılan olarak kapalı).** Satıcı açık rızayı aldıysa, müşterinin kendi geçmiş oturumlarından bir profil tutulabilir. Profilin **tek işlevi ek doğrulama istemektir.** Profil hiçbir zaman ödemeyi engellemez, onaylamaz ve risk skorunu değiştirmez. Profile uyan bir oturum da hiçbir avantaj kazanmaz. Amaç, hesabı ele geçirilmiş bir müşterinin yerine ödeme yapan birine ek soru sormaktır. Profilden sapma bir hüküm değildir; yalnızca ek doğrulama gerekçesidir. Örneğin büyükanne adına ödeme yapan torun, doğru kodu girerek ödemeyi tamamlar.

## 3. Hukuki dayanak

- **Müşteri davranış profili:** Bir kişinin fare ve klavye kullanım biçiminin, onu tanımak amacıyla ve kimliğine bağlı olarak saklanması biyometrik veri, dolayısıyla özel nitelikli kişisel veri sayılmaktadır (KVKK m. 6). Bu nedenle profil yalnızca **açık rıza** ile açılmalıdır. Profil yalnızca satıcının kimliği doğrulanmış rıza çağrısıyla oluşturulabilir; ödeme kararı profil yaratamaz. Tek istisna demo ad alanıdır (bölüm 6). Rıza yoksa profil satırı da yoktur, katman hiçbir şey yapmaz ve **rıza vermemek müşteriye hiçbir ek soru olarak yansımaz.** Arayüz ayrıca `contract_necessity` (sözleşmenin ifası) değerini kabul etmektedir. Özel nitelikli veri için bu dayanağın geçerli olup olmadığı hukuki incelemeyi beklemektedir ve bu değer kullanılmamalıdır.
- **Oturum düzeyindeki bot analizi:** Kişiyi tanımlamaz. Rastgele bir oturum kimliğine bağlıdır ve 24 saat sonra silinir. Dayanağını ürünü kullanan satıcı belirler.

## 4. Ne kadar süre saklanır

| Saklanan | Nedir | Saklama süresi |
|---|---|---|
| Ham davranış verisi (fare yolu, tıklamalar, tuş zamanları) | SDK'nın gönderdiği ham olaylar | 1 saat, ardından boşaltılır |
| Oturum ve özellik satırları | 12 özellik, risk skoru, etiket | 24 saat |
| `profile_id` | Satıcı kimliği ve müşteri referansının HMAC özeti. Ham referans hiçbir zaman saklanmaz. | Silme talebine ya da 180 gün hareketsizliğe kadar |
| Giriş türü başına en fazla 20 referans ve 4 onay bekleyen oturum vektörü | Oturum başına 12 normalleştirilmiş sayı ve bunların oturum içindeki dağılımı. Ham davranış verisi profile hiçbir zaman girmez. | 180 gün |
| Rıza dayanağı ve zamanı | Hukuki dayanak kaydı | Profille birlikte |
| Karar kaydı | Karar, gerekçe, sapma, en çok sapan özellikler. Sapma bulunduysa karşılaştırılan oturum vektörü de. | 90 gün |
| Erişim kaydı | Profili kimin, ne zaman incelediği | 365 gün |
| Demo ad alanındaki her şey (demo sayfasındaki ödemeler) | Öğrenilen vektör, demo profili, karar kaydı | 24 saat |

Süreler kodda varsayılan değerlerdir ve bir veri koruma etki değerlendirmesiyle (`docs/dpia.md`) onaylanmalıdır. Temizlik işlemi 10 dakikada bir çalıştığından silme en fazla 10 dakika gecikebilir. Uygulama verileri kendisi şifrelemez; koruma sunucu diskinin şifrelenmesine bırakılmıştır. Prototipte bu, çalıştırıldığı makinenin ayarına bağlıdır. Sütun düzeyinde şifrelemenin neden yapılmadığı `docs/dpia.md`'de anlatılır.

## 5. Haklarınız ve başvuru yolu

KVKK m. 11 uyarınca verilerinizin işlenip işlenmediğini öğrenme, bilgi isteme, düzeltme, silme, itiraz ve zararın giderilmesini isteme haklarınız vardır. Otomatik analiz sonucunda aleyhinize bir sonuç çıkmasına itiraz hakkınız da vardır (m. 11/1-g). Başvurular veri sorumlusu olan satıcıya yapılır. Satıcı bunları şu yollarla uygular:

- **Silme:** Satıcı `POST /api/profile/erase` çağrısını `erase` türüyle yapar. Profil ve bütün vektörleri silinir. Oturumlar, karar kayıtları ve erişim kayıtlarıyla bağı kaldırılır. Karar kayıtlarındaki karşılaştırılan oturum vektörleri de silinir.
- **İtiraz (profillemeye karşı):** Aynı çağrı `object` türüyle yapılır. Bütün veriler silinir, ancak bir itiraz kaydı tutulur. Bu kayıt, profilin daha sonra yeni bir rıza çağrısıyla yeniden açılmasını engeller.
- **İnsan incelemesi:** Profil nedeniyle ek doğrulama istenen bir karara itiraz edildiğinde yetkili bir inceleme görevlisi `GET /api/profile/review/{session_id}` ile neyin neyle karşılaştırıldığını görür. Bu uç nokta panonun ortak anahtarını kabul etmez, görevli başına ayrı bir anahtar ister ve her okuma bir erişim kaydı bırakır. Karar kaydı 90 gün tutulduğu için inceleme, oturum verisi silindikten sonra da yapılabilir.

Profil katmanı kapalıyken bir ödeme bot skoru nedeniyle engellenirse bu kararın izi yalnızca 24 saatlik oturum kaydındadır. İnsan incelemesi için ayrı bir karar kaydı tutulmaz. Bu, prototipin bilinen bir eksiğidir.

## 6. Demo sayfası

Demo sayfasındaki "Müşteri Referansı" alanı bir kısayoldur. Gerçek bir entegrasyonda bu değeri tarayıcı değil, satıcının sunucusu gönderir. Girilen değer ayrılmış `demo` ad alanında tutulur ve hiçbir gerçek satıcının müşterisiyle eşleşmez. **Bu alana gerçek kişisel bilgi girmeyin.** Demo ad alanında sizin ödemenizden kalan her şey 24 saat içinde kendiliğinden silinir: öğrenilen vektör, demo profili ve karar kaydı. Bu süreden önce tek tek silmenin bir yolu yoktur.

Seçicide listelenen demo müşterileri (Ayşe, Mehmet, Zeynep) sentetiktir. Geçmişleri simülatörle üretilmiştir ve gerçek kişi değildirler. Sentetik bir müşteri adına ödeme yaptığınızda oturumunuz onun geçmişiyle karşılaştırılır, ancak ona **öğretilmez**. Bu kararlar panoda ve kayıtlarda "Sentetik demo verisi" olarak işaretlenir. Sentetik geçmişle yapılan karşılaştırmalar hiçbir ölçüme katılmaz.

## 7. Yurt dışına aktarım

Prototip, Docker Compose ile tek bir makinede çalışır. Veriler yurt dışına aktarılmaz ve üçüncü bir tarafa gönderilmez.
