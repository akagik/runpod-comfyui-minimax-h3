# syntax=docker/dockerfile:1.7

# linux/amd64 manifest digest for nvidia/cuda:13.0.0-runtime-ubuntu24.04.
# The NVIDIA runtime base is materially smaller than the reference
# runpod/pytorch image while the pinned PyTorch wheels provide cuDNN/NCCL.
ARG BASE_IMAGE=nvidia/cuda:13.0.0-runtime-ubuntu24.04@sha256:4757e6477d94d56fd8230e15b7d59956750c983a47a42584f44dbe143090da82
FROM ${BASE_IMAGE}

ARG COMFYUI_COMMIT=dec5d9450a5290bcf63430409ea41018e67f41c3
ARG COMFYUI_VERSION=0.30.2
ARG IMAGE_VERSION=0.1.0

LABEL org.opencontainers.image.title="RunPod ComfyUI MiniMax H3 worker" \
      org.opencontainers.image.description="Model-free ComfyUI MiniMax H3 Pod and RunPod Serverless image" \
      org.opencontainers.image.source="https://github.com/akagik/runpod-comfyui-minimax-h3" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      io.runpod.comfyui.version="${COMFYUI_VERSION}" \
      io.runpod.comfyui.commit="${COMFYUI_COMMIT}" \
      io.runpod.pytorch.version="2.13.0" \
      io.runpod.cuda.version="13.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/comfyui-venv \
    PATH=/opt/comfyui-venv/bin:${PATH} \
    COMFYUI_DIR=/opt/ComfyUI \
    APP_DIR=/opt/runpod-comfyui \
    MODE_TO_RUN=serverless \
    COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    REQUIRE_NETWORK_VOLUME=true \
    REQUIRE_MINIMAX_MODELS=true \
    HF_HOME=/runpod-volume/hf-cache \
    HUGGINGFACE_HUB_CACHE=/runpod-volume/hf-cache/hub

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       ffmpeg \
       git \
       jq \
       libgl1 \
       libglib2.0-0 \
       libsm6 \
       libxext6 \
       libxrender1 \
       openssh-server \
       python3.12 \
       python3.12-venv \
       tini \
    && python3.12 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --no-cache-dir --upgrade \
       pip==26.1.2 setuptools==84.0.0 wheel \
    && "${VIRTUAL_ENV}/bin/python" -m pip install --no-cache-dir uv==0.8.13 \
    && mkdir -p /run/sshd \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN uv pip sync --python "${VIRTUAL_ENV}/bin/python" /tmp/requirements.lock.txt \
    && "${VIRTUAL_ENV}/bin/python" -m pip check \
    && rm -rf /tmp/requirements.lock.txt /root/.cache /root/.cache/uv

# Fetch exactly one ComfyUI commit and discard Git history from the image.
RUN git init "${COMFYUI_DIR}" \
    && git -C "${COMFYUI_DIR}" remote add origin https://github.com/Comfy-Org/ComfyUI.git \
    && git -C "${COMFYUI_DIR}" fetch --depth 1 origin "${COMFYUI_COMMIT}" \
    && git -C "${COMFYUI_DIR}" checkout --detach FETCH_HEAD \
    && test "$(git -C "${COMFYUI_DIR}" rev-parse HEAD)" = "${COMFYUI_COMMIT}" \
    && rm -rf "${COMFYUI_DIR}/.git"

COPY custom_nodes/minimax_h3_flref_audio.py "${COMFYUI_DIR}/custom_nodes/minimax_h3_flref_audio.py"
COPY config/extra_model_paths.yaml "${APP_DIR}/config/extra_model_paths.yaml"
COPY scripts/ "${APP_DIR}/scripts/"
COPY worker/ "${APP_DIR}/worker/"
COPY workflows/ "${APP_DIR}/workflows/"
COPY VERSION_MATRIX.md "${APP_DIR}/VERSION_MATRIX.md"

RUN chmod +x "${APP_DIR}/scripts/"*.sh \
    && ln -s "${APP_DIR}/scripts/start.sh" /start.sh \
    && mkdir -p "${COMFYUI_DIR}/models" "${COMFYUI_DIR}/input" "${COMFYUI_DIR}/output" \
    && find "${COMFYUI_DIR}/models" -type f -size +1M -print -quit | (! grep -q .) \
    && "${VIRTUAL_ENV}/bin/python" -m compileall -q "${APP_DIR}/worker" "${COMFYUI_DIR}/custom_nodes/minimax_h3_flref_audio.py" \
    && cd "${COMFYUI_DIR}" \
    && timeout 300 "${VIRTUAL_ENV}/bin/python" main.py \
       --quick-test-for-ci --cpu \
       --extra-model-paths-config "${APP_DIR}/config/extra_model_paths.yaml"

WORKDIR ${APP_DIR}
EXPOSE 8188 22 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/runpod-comfyui/scripts/start.sh"]
