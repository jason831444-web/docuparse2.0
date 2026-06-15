from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DocuParse API"
    api_prefix: str = "/api"
    database_url: str = "postgresql+psycopg://docuparse:docuparse@localhost:5433/docuparse"
    upload_dir: Path = Path("uploads")
    backend_base_url: str = "http://localhost:8001"
    cors_origins: list[str] = ["http://localhost:3001"]
    max_upload_mb: int = 12
    storage_backend: str = "local"
    processing_mode: str = "inline"
    queue_backend: str = "local"
    ai_provider: str = "auto"
    ai_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    ai_interpretation_enabled: bool = True
    ai_interpretation_provider: str = "gemma"
    ai_interpretation_model: str = "google/gemma-4-E4B-it"
    ai_interpretation_fallback_model: str = "google/gemma-2-2b-it"
    ai_interpretation_enable_model_fallback: bool = True
    ai_interpretation_local_prefer_small_model: bool = False
    ai_interpretation_force_small_model: bool = False
    ai_interpretation_max_chars: int = 12000
    ai_interpretation_skip_trivial: bool = True
    ai_interpretation_min_chars: int = 80
    llama_cpp_model_path: Path | None = None
    llama_cpp_context_window: int = 4096
    llama_cpp_threads: int = 0
    llama_cpp_gpu_layers: int = 0
    llama_cpp_max_tokens: int = 700
    llama_cpp_temperature: float = 0.1
    gemma_model_name: str = "google/gemma-4-E4B-it"
    gemma_model_dir: Path | None = None
    gemma_device: str = "auto"
    huggingface_token: str | None = None
    ai_primary_provider: str = "paddleocr_vl_1_6_gguf"
    ai_secondary_provider: str = "heuristic_fallback"
    ai_enable_second_pass: bool = False
    ai_second_pass_confidence_threshold: float = 0.80
    ai_model_dir: Path = Path("models")
    ocr_fallback_provider: str = "paddleocr_ppocrv4"
    enable_paddleocr_vl_gguf: bool = True
    paddleocr_vl_gguf_repo_id: str = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
    paddleocr_vl_gguf_model_dir: Path = Path("/app/models/paddleocr_vl_1_6_gguf")
    paddleocr_vl_gguf_model_file: str = "PaddleOCR-VL-1.6-GGUF.gguf"
    paddleocr_vl_gguf_mmproj_file: str = "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
    paddleocr_vl_gguf_server_url: str = "http://vl-worker-gguf:8080/v1"
    paddleocr_vl_gguf_worker_url: str | None = None
    paddleocr_vl_gguf_timeout_seconds: float = 240.0
    paddleocr_vl_gguf_max_pages: int = 1
    paddleocr_vl_gguf_concurrency: int = 1
    paddleocr_vl_gguf_n_predict: int = 512
    paddleocr_vl_gguf_smoke_passed: bool = False
    paddleocr_vl_gguf_primary_reader_enabled: bool = True
    paddleocr_vl_gguf_upload_pipeline_enabled: bool = True
    paddleocr_vl_gguf_in_process_enabled: bool = False
    enable_paddleocr_vl: bool = False
    paddleocr_vl_model_dir: Path | None = None
    paddleocr_vl_layout_model_dir: Path | None = None
    paddleocr_vl_hf_repo: str = "PaddlePaddle/PaddleOCR-VL-1.6"
    paddleocr_vl_model_name: str = "PaddleOCR-VL-1.6"
    paddleocr_vl_device: str | None = "cpu"
    paddleocr_vl_engine: str | None = None
    paddleocr_vl_timeout_seconds: float = 180.0
    pdf_ocr_max_pages: int = 3
    ocr_worker_url: str | None = None
    prefer_ocr_worker: bool = False
    ocr_worker_timeout_seconds: float = 120.0
    local_paddleocr_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator(
        "llama_cpp_model_path",
        "gemma_model_dir",
        "paddleocr_vl_gguf_model_dir",
        "paddleocr_vl_model_dir",
        "paddleocr_vl_layout_model_dir",
        mode="before",
    )
    @classmethod
    def empty_path_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.ai_model_dir.mkdir(parents=True, exist_ok=True)
    return settings
