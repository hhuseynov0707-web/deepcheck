"""AWS Lambda entrypoint for the DeepCheck API (optional deployment path).

This wraps the same FastAPI `app` used by the Docker/uvicorn deployment
(main.py) with Mangum, so it can run behind API Gateway on Lambda without
duplicating any route logic.

Not used by the default `docker-compose up` flow in this repo -- that runs
main.py directly via uvicorn (see backend/entrypoint.sh). This file only
matters if you deploy via `template.yaml` (AWS SAM) or an equivalent Lambda
packaging step.

Caveat: torch + shap are heavy dependencies. A real Lambda deployment of this
handler needs a container-image Lambda (not a zip) to fit within Lambda's
package size limits, and the DATABASE_URL must point at a reachable Postgres
instance (e.g. RDS/Aurora) since there is no bundled database in Lambda.
"""

from mangum import Mangum

from main import app

handler = Mangum(app)
