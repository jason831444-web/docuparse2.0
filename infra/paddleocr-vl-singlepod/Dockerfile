FROM ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:latest-nvidia-gpu

USER root

# The official PaddleOCR-VL deployment keeps the pipeline API and VLM server in
# separate containers. This PoC image intentionally combines them for RunPod
# single-Pod experiments only. Do not use this image for DocuParse production
# until CUDA/Torch/Paddle/vLLM compatibility is verified in the built image.
ARG PADDLEOCR_VERSION=">=3.4.0,<3.5"
ARG PADDLEX_VERSION=">=3.4.0,<3.5"

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install "paddleocr${PADDLEOCR_VERSION}" "paddlex${PADDLEX_VERSION}"

COPY start-singlepod.sh /start-singlepod.sh
RUN chmod +x /start-singlepod.sh

USER paddleocr

ENV HF_HOME=/workspace/.cache/huggingface
ENV PADDLE_PDX_MODEL_SOURCE=HUGGINGFACE
ENV VLM_HOST=127.0.0.1
ENV VLM_PORT=8081
ENV PIPELINE_HOST=0.0.0.0
ENV PIPELINE_PORT=8080
ENV VLM_BACKEND=vllm

EXPOSE 8080

CMD ["/start-singlepod.sh"]
