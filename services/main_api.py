# services/main_api.py
from fastapi import FastAPI

from services.health import app as health_app
from services.metadata import app as metadata_app

# import eval router
from api.routes.eval import router as eval_router

app = FastAPI(title="MACRS API")

app.mount("/health", health_app)
app.mount("/metadata", metadata_app)

# include eval router (it already has prefix="/eval")
app.include_router(eval_router)