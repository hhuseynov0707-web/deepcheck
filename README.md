# DeepCheck MVP

Kullanıcı davranışını gerçek zamanlı analiz edip bot/insan ayrımını 0-100 risk skoruyla yapan SDK + backend + dashboard.

## Deployment

**Birincil (test edilmiş) deployment: Docker Compose + Uvicorn.**

```bash
cd backend && python train_model.py   # model.pkl + lstm_model.pt üretir
docker-compose up --build
```

Bu, `docker-compose.yml` ve `backend/entrypoint.sh` üzerinden `main.py`'yi doğrudan Uvicorn ile çalıştırır (`backend/Dockerfile`). Demo ve SOC Dashboard bu şekilde çalışır ve test edilmiştir.

**İsteğe bağlı: AWS Lambda deployment path.**

`backend/lambda_handler.py` (Mangum adapter) ve `backend/template.yaml` (AWS SAM) dosyaları, aynı FastAPI `app`'i Lambda + API Gateway üzerinde barındırmak isteyenler için hazırlanmıştır:

```bash
cd backend && sam build --use-container && sam deploy --guided
```

Bu path bu repoda **deploy edilmemiş ve test edilmemiştir** — sadece kod olarak mevcuttur. Gerçek bir Lambda dağıtımı için:
- `torch` + `shap` paket boyutu nedeniyle container-image tipi Lambda paketleme gerekir (zip limiti aşılabilir).
- `DATABASE_URL` erişilebilir bir Postgres örneğine (ör. RDS/Aurora) işaret etmelidir — Lambda'da docker-compose'daki `db` servisi gibi gömülü bir veritabanı yoktur.

Repodaki tek çalışan ve doğrulanmış kurulum Docker Compose'dur; Lambda dosyaları alternatif bir dağıtım seçeneği olarak eklenmiştir.
