from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.documents import router as documents_router
from app.api.routes.export_templates import router as export_templates_router
from app.api.routes.item_master import router as item_master_router
from app.api.routes.reports import router as reports_router
from app.core.config import get_settings
from app.services.ocr import provider_health

settings = get_settings()

app = FastAPI(title=settings.app_name)
allow_all_origins = "*" in settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")


@app.get("/health")
def health() -> dict:
    providers = provider_health()
    providers["openai_vision_configured"] = bool(settings.openai_api_key)
    return {"status": "ok", "providers": providers}


app.include_router(documents_router, prefix=settings.api_prefix)
app.include_router(export_templates_router, prefix=settings.api_prefix)
app.include_router(item_master_router, prefix=settings.api_prefix)
app.include_router(reports_router, prefix=settings.api_prefix)
