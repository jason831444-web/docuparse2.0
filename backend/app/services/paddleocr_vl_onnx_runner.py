from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROMPT = "OCR:"


class PaddleOCRVLOnnxRunnerError(RuntimeError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def validate_generated_text(text: str, *, prompt: str = "") -> str:
    stripped = text.strip()
    if not stripped:
        return "output_empty"
    normalized_prompt = prompt.strip()
    if normalized_prompt and stripped.lower().count(normalized_prompt.lower()) >= 2:
        return "prompt_echo"
    if "Use null for missing values" in stripped:
        return "prompt_echo"
    chars = [char for char in stripped if not char.isspace()]
    if chars:
        most_common = max(chars.count(char) for char in set(chars))
        if most_common / max(1, len(chars)) > 0.45:
            return "degenerate_generation"
    if "\ufffd" in stripped:
        return "degenerate_generation"
    return "candidate_text_generated"


@dataclass(frozen=True)
class PaddleOCRVLOnnxBundle:
    model_path: Path
    decoder_path: Path
    embed_tokens_path: Path
    vision_encoder_path: Path
    tokenizer_path: Path
    config_path: Path
    preprocessor_config_path: Path


def inspect_model_bundle(model_path: str | Path) -> dict[str, Any]:
    path = Path(model_path)
    required = {
        "decoder": path / "onnx" / "decoder_model_merged.onnx",
        "embed_tokens": path / "onnx" / "embed_tokens.onnx",
        "vision_encoder": path / "onnx" / "vision_encoder.onnx",
        "tokenizer": path / "tokenizer.json",
        "tokenizer_model": path / "tokenizer.model",
        "config": path / "config.json",
        "processor_config": path / "processor_config.json",
        "preprocessor_config": path / "preprocessor_config.json",
    }
    missing = [name for name, file_path in required.items() if not file_path.exists()]
    return {
        "model_path": str(path),
        "path_exists": path.exists(),
        "missing": missing,
        "files": {
            name: {
                "path": str(file_path),
                "exists": file_path.exists(),
                "size_bytes": file_path.stat().st_size if file_path.exists() else None,
            }
            for name, file_path in required.items()
        },
        "usable": path.exists() and not missing,
    }


def predict(
    *,
    image_path: str,
    model_path: str,
    model_files: list[str] | None = None,
    device: str = "cpu",
    timeout_seconds: float = 60,
    max_pages: int = 1,
    prompt: str | None = None,
    max_new_tokens: int = 64,
) -> dict[str, Any]:
    _ = model_files, max_pages
    runner = PaddleOCRVLOnnxRunner(model_path=model_path, device=device, timeout_seconds=timeout_seconds)
    return runner.predict_path(Path(image_path), prompt=prompt, max_new_tokens=max_new_tokens)


class PaddleOCRVLOnnxRunner:
    def __init__(self, model_path: str | Path, *, device: str = "cpu", timeout_seconds: float = 60) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.timeout_seconds = timeout_seconds
        self.bundle = self._bundle_or_raise(self.model_path)
        self.config = json.loads(self.bundle.config_path.read_text(encoding="utf-8"))
        self.preprocessor_config = json.loads(self.bundle.preprocessor_config_path.read_text(encoding="utf-8"))
        self._sessions: dict[str, Any] | None = None
        self._tokenizer: Any | None = None

    def predict_path(self, path: Path, *, prompt: str | None = None, max_new_tokens: int = 64) -> dict[str, Any]:
        started = time.perf_counter()
        image = self._load_image(path)
        result = self.predict_image(image, prompt=prompt, max_new_tokens=max_new_tokens)
        result["input_path"] = str(path)
        result["inference_time_ms"] = int((time.perf_counter() - started) * 1000)
        return result

    def predict_image(self, image: Any, *, prompt: str | None = None, max_new_tokens: int = 64) -> dict[str, Any]:
        started = time.perf_counter()
        sessions = self._load_sessions()
        tokenizer = self._load_tokenizer()
        pixel_values, image_grid_thw, preprocessing = self._preprocess_image(image)

        try:
            vision_outputs = sessions["vision_encoder"].run(None, {"pixel_values": pixel_values, "image_grid_thw": image_grid_thw})
            image_embeds = vision_outputs[0]
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("vision_encoder_failed", str(exc)) from exc
        if image_embeds.ndim != 2 or image_embeds.shape[-1] != int(self.config.get("hidden_size", 1024)):
            raise PaddleOCRVLOnnxRunnerError(
                "vision_encoder_failed",
                f"Unexpected image embedding shape: {tuple(image_embeds.shape)}",
            )

        prompt_ids, image_token_positions, prefill_position_ids = self._build_prompt_token_ids(
            tokenizer,
            image_token_count=int(image_embeds.shape[0]),
            image_grid_thw=image_grid_thw,
            prompt=prompt or DEFAULT_PROMPT,
        )
        inputs_embeds = sessions["embed_tokens"].run(None, {"input_ids": prompt_ids})[0]
        for offset, position in enumerate(image_token_positions):
            inputs_embeds[0, position, :] = image_embeds[offset]

        generated_ids = self._generate(
            sessions["decoder"],
            sessions["embed_tokens"],
            tokenizer,
            inputs_embeds=inputs_embeds,
            prefill_position_ids=prefill_position_ids,
            prompt_length=int(inputs_embeds.shape[1]),
            max_new_tokens=max_new_tokens,
            started=started,
        )
        text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
        return {
            "text": text,
            "raw_output": {"generated_token_ids": generated_ids},
            "inference_time_ms": int((time.perf_counter() - started) * 1000),
            "model": "PaddleOCR-VL-1.5-ONNX-quantized",
            "runtime": "onnxruntime",
            "onnxruntime_version": self._onnxruntime_version(),
            "provider": "paddleocr_vl_onnx_quantized",
            "line_candidates": [],
            "table_candidates": [],
            "layout_elements": [],
            "preprocessing": preprocessing,
            "prompt_length": int(inputs_embeds.shape[1]),
            "image_embed_count": int(image_embeds.shape[0]),
            "generated_token_count": len(generated_ids),
            "error": None,
        }

    @staticmethod
    def _bundle_or_raise(model_path: Path) -> PaddleOCRVLOnnxBundle:
        info = inspect_model_bundle(model_path)
        if not info["path_exists"]:
            raise PaddleOCRVLOnnxRunnerError("model_path_missing", f"Model path does not exist: {model_path}")
        if info["missing"]:
            raise PaddleOCRVLOnnxRunnerError("model_bundle_incomplete", f"Missing model files: {', '.join(info['missing'])}")
        return PaddleOCRVLOnnxBundle(
            model_path=model_path,
            decoder_path=model_path / "onnx" / "decoder_model_merged.onnx",
            embed_tokens_path=model_path / "onnx" / "embed_tokens.onnx",
            vision_encoder_path=model_path / "onnx" / "vision_encoder.onnx",
            tokenizer_path=model_path / "tokenizer.json",
            config_path=model_path / "config.json",
            preprocessor_config_path=model_path / "preprocessor_config.json",
        )

    def _load_sessions(self) -> dict[str, Any]:
        if self._sessions is not None:
            return self._sessions
        try:
            import onnxruntime as ort
        except Exception as exc:  # pragma: no cover - dependency optional in default runtime
            raise PaddleOCRVLOnnxRunnerError("onnxruntime_missing", str(exc)) from exc

        opts = ort.SessionOptions()
        opts.enable_mem_pattern = False
        providers = ["CPUExecutionProvider"]
        try:
            self._sessions = {
                "vision_encoder": ort.InferenceSession(str(self.bundle.vision_encoder_path), sess_options=opts, providers=providers),
                "embed_tokens": ort.InferenceSession(str(self.bundle.embed_tokens_path), sess_options=opts, providers=providers),
                "decoder": ort.InferenceSession(str(self.bundle.decoder_path), sess_options=opts, providers=providers),
            }
        except PaddleOCRVLOnnxRunnerError:
            raise
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("decoder_session_failed", str(exc)) from exc
        return self._sessions

    def _load_tokenizer(self) -> Any:
        if self._tokenizer is not None:
            return self._tokenizer
        try:
            from tokenizers import Tokenizer
        except Exception as exc:  # pragma: no cover - dependency optional in default runtime
            raise PaddleOCRVLOnnxRunnerError("tokenizer_load_failed", str(exc)) from exc
        try:
            self._tokenizer = Tokenizer.from_file(str(self.bundle.tokenizer_path))
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("tokenizer_load_failed", str(exc)) from exc
        return self._tokenizer

    def _load_image(self, path: Path) -> Any:
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - pillow is part of backend runtime
            raise PaddleOCRVLOnnxRunnerError("image_preprocess_failed", str(exc)) from exc
        try:
            if path.suffix.lower() == ".pdf":
                try:
                    import fitz
                except Exception as exc:
                    raise PaddleOCRVLOnnxRunnerError("image_preprocess_failed", "PyMuPDF is required for PDF smoke input") from exc
                document = fitz.open(path)
                if not document.page_count:
                    raise PaddleOCRVLOnnxRunnerError("image_preprocess_failed", "PDF has no pages")
                page = document.load_page(0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            return Image.open(path).convert("RGB")
        except PaddleOCRVLOnnxRunnerError:
            raise
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("image_preprocess_failed", str(exc)) from exc

    def _preprocess_image(self, image: Any) -> tuple[Any, Any, dict[str, Any]]:
        try:
            import numpy as np
            from PIL import Image
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("image_preprocess_failed", str(exc)) from exc

        patch = int(self.preprocessor_config.get("patch_size", 14))
        merge = int(self.preprocessor_config.get("merge_size", 2))
        target_patches = 576
        original_width, original_height = image.size
        grid_h, grid_w = self._select_fixed_patch_grid(
            target_patches=target_patches,
            aspect_ratio=original_height / max(1, original_width),
        )
        width = grid_w * patch
        height = grid_h * patch
        resized = image.convert("RGB").resize((width, height), Image.Resampling.BICUBIC)

        array = np.asarray(resized).astype("float32")
        array = array * float(self.preprocessor_config.get("rescale_factor", 1 / 255))
        mean = np.asarray(self.preprocessor_config.get("image_mean", [0.5, 0.5, 0.5]), dtype="float32")
        std = np.asarray(self.preprocessor_config.get("image_std", [0.5, 0.5, 0.5]), dtype="float32")
        array = (array - mean) / std
        patches = array.reshape(grid_h, patch, grid_w, patch, 3)
        patches = patches.transpose(0, 2, 4, 1, 3).reshape(1, grid_h * grid_w, 3, patch, patch).astype("float32")
        image_grid_thw = np.asarray([[1, grid_h, grid_w]], dtype="int64")
        return patches, image_grid_thw, {
            "original_size": [original_width, original_height],
            "resized_size": [width, height],
            "patch_size": patch,
            "merge_size": merge,
            "fixed_patch_count": target_patches,
            "grid_thw": image_grid_thw[0].tolist(),
            "num_patches": int(grid_h * grid_w),
        }

    @staticmethod
    def _select_fixed_patch_grid(*, target_patches: int, aspect_ratio: float) -> tuple[int, int]:
        candidates: list[tuple[float, int, int]] = []
        for grid_h in range(2, target_patches + 1, 2):
            if target_patches % grid_h:
                continue
            grid_w = target_patches // grid_h
            if grid_w % 2:
                continue
            score = abs((grid_h / max(1, grid_w)) - aspect_ratio)
            candidates.append((score, grid_h, grid_w))
        if not candidates:
            root = int(target_patches**0.5)
            return root, max(1, target_patches // max(1, root))
        _, grid_h, grid_w = min(candidates, key=lambda item: item[0])
        return grid_h, grid_w

    def _build_prompt_token_ids(
        self,
        tokenizer: Any,
        *,
        image_token_count: int,
        image_grid_thw: Any,
        prompt: str,
    ) -> tuple[Any, list[int], Any]:
        try:
            import numpy as np
        except Exception as exc:
            raise PaddleOCRVLOnnxRunnerError("embed_tokens_failed", str(exc)) from exc
        image_token = tokenizer.token_to_id("<|IMAGE_PLACEHOLDER|>")
        if image_token is None:
            raise PaddleOCRVLOnnxRunnerError("tokenizer_load_failed", "Image special tokens are missing")
        prompt_ids = tokenizer.encode("\n" + prompt).ids
        ids = [image_token] * image_token_count + prompt_ids
        positions = list(range(image_token_count))
        position_ids = self._build_multimodal_position_ids(
            sequence_length=len(ids),
            image_token_positions=positions,
            image_grid_thw=image_grid_thw,
        )
        return np.asarray([ids], dtype="int64"), positions, position_ids

    def _build_multimodal_position_ids(self, *, sequence_length: int, image_token_positions: list[int], image_grid_thw: Any) -> Any:
        import numpy as np

        position_ids = np.zeros((3, 1, sequence_length), dtype="int64")
        grid_t, grid_h, grid_w = [int(value) for value in image_grid_thw[0].tolist()]
        merge = int(self.preprocessor_config.get("merge_size", 2))
        merged_h = max(1, grid_h // merge)
        merged_w = max(1, grid_w // merge)
        expected = grid_t * merged_h * merged_w
        if expected != len(image_token_positions):
            raise PaddleOCRVLOnnxRunnerError(
                "runner_contract_unknown",
                f"Image token count mismatch: expected {expected}, got {len(image_token_positions)}",
            )
        image_position_index = 0
        text_position = max(merged_h, merged_w) + 1
        for seq_index in range(sequence_length):
            if seq_index in image_token_positions:
                local = image_position_index
                t = local // (merged_h * merged_w)
                rem = local % (merged_h * merged_w)
                h = rem // merged_w
                w = rem % merged_w
                position_ids[:, 0, seq_index] = [t, h, w]
                image_position_index += 1
            else:
                position_ids[:, 0, seq_index] = [text_position, text_position, text_position]
                text_position += 1
        return position_ids

    def _generate(
        self,
        decoder: Any,
        embed_tokens: Any,
        tokenizer: Any,
        *,
        inputs_embeds: Any,
        prefill_position_ids: Any,
        prompt_length: int,
        max_new_tokens: int,
        started: float,
    ) -> list[int]:
        import numpy as np

        eos_token_id = self._special_token_id(tokenizer, "</s>", fallback=2)
        generated: list[int] = []
        past: dict[str, Any] = {
            f"past_key_values.{layer}.{kind}": np.zeros((1, 2, 0, 128), dtype="float32")
            for layer in range(int(self.config.get("num_hidden_layers", 18)))
            for kind in ("key", "value")
        }
        attention_mask = np.ones((1, prompt_length), dtype="int64")
        feed = {
            "inputs_embeds": inputs_embeds.astype("float32"),
            "attention_mask": attention_mask,
            "position_ids": prefill_position_ids,
            "use_cache_branch": np.asarray([False], dtype=bool),
            **past,
        }
        output_names = [item.name for item in decoder.get_outputs()]
        outputs = decoder.run(output_names, feed)
        logits = outputs[0]
        present = dict(zip(output_names[1:], outputs[1:]))
        next_token = int(logits[0, -1].argmax())

        for step in range(max_new_tokens):
            if time.perf_counter() - started > self.timeout_seconds:
                raise PaddleOCRVLOnnxRunnerError("timeout", f"Timed out after {self.timeout_seconds} seconds")
            generated.append(next_token)
            if next_token == eos_token_id:
                break
            token_ids = np.asarray([[next_token]], dtype="int64")
            token_embed = embed_tokens.run(None, {"input_ids": token_ids})[0]
            total_length = prompt_length + step + 1
            feed = {
                "inputs_embeds": token_embed.astype("float32"),
                "attention_mask": np.ones((1, total_length), dtype="int64"),
                "position_ids": np.asarray([[[total_length - 1]], [[total_length - 1]], [[total_length - 1]]], dtype="int64"),
                "use_cache_branch": np.asarray([True], dtype=bool),
            }
            for layer in range(int(self.config.get("num_hidden_layers", 18))):
                feed[f"past_key_values.{layer}.key"] = present[f"present.{layer}.key"]
                feed[f"past_key_values.{layer}.value"] = present[f"present.{layer}.value"]
            outputs = decoder.run(output_names, feed)
            logits = outputs[0]
            present = dict(zip(output_names[1:], outputs[1:]))
            next_token = int(logits[0, -1].argmax())
        return generated

    @staticmethod
    def _special_token_id(tokenizer: Any, token: str, *, fallback: int) -> int:
        token_id = tokenizer.token_to_id(token)
        return int(token_id if token_id is not None else fallback)

    @staticmethod
    def _onnxruntime_version() -> str | None:
        try:
            import onnxruntime as ort
            return str(getattr(ort, "__version__", ""))
        except Exception:
            return None
