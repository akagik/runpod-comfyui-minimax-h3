# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=ghcr.io/akagik/runpod-comfyui-minimax-h3:0.1.4@sha256:09e695790b1eb815a92c382f384313712c621172e031dd2cf136a0a7a9ea053e
FROM ${BASE_IMAGE}
ARG VDN_COMMIT=3eb63496c24ca70faaf8a14b6c75fcb480e34bf1
ARG COMFYUI_COMMIT=a20738f1d345e2695429544b5defea672dd74486
ARG IMAGE_VERSION=0.1.0
LABEL org.opencontainers.image.title="RunPod ComfyUI VDN-H3 BF16 8-step" \
      org.opencontainers.image.description="Model-free, pinned ComfyUI VDN-H3 port; not the official optimized VDN runtime" \
      org.opencontainers.image.source="https://github.com/akagik/runpod-comfyui-vdn-h3" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      io.runpod.vdn.commit="${VDN_COMMIT}" \
      io.runpod.comfyui.commit="${COMFYUI_COMMIT}" \
      io.runpod.comfyui.version="commit-pinned-2026-09-12"
ENV MODE_TO_RUN=pod REQUIRE_MINIMAX_MODELS=false \
    RUNPOD_VOLUME_ROOT=/workspace \
    COMFYUI_DIR=/opt/ComfyUI-vdn \
    HF_HOME=/workspace/hf-cache HUGGINGFACE_HUB_CACHE=/workspace/hf-cache/hub
# The parent INT8 model gate is disabled deliberately: SSH and ComfyUI must
# start before model downloads. Manager checks the actual BF16 loader choices.
# No model downloads, source images, credentials, or generation requests in image.
RUN git init "${COMFYUI_DIR}" \
    && git -C "${COMFYUI_DIR}" remote add origin https://github.com/Comfy-Org/ComfyUI.git \
    && git -C "${COMFYUI_DIR}" fetch --depth 1 origin "${COMFYUI_COMMIT}" \
    && git -C "${COMFYUI_DIR}" checkout --detach FETCH_HEAD \
    && test "$(git -C "${COMFYUI_DIR}" rev-parse HEAD)" = "${COMFYUI_COMMIT}" \
    && uv pip install --python /opt/comfyui-venv/bin/python --no-deps \
       comfyui-frontend-package==1.52.7 comfyui-workflow-templates==0.11.59 \
       comfyui-embedded-docs==0.5.11 comfy-kitchen==0.2.33 comfy-aimdo==0.5.3 av==18.0.0 \
       comfyui-workflow-templates-core==0.3.339 comfyui-workflow-templates-json==0.1.74 \
       comfyui-workflow-templates-media-assets-01==0.1.44 \
    && python -m pip check
RUN git init "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" \
    && git -C "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" remote add origin https://github.com/Saganaki22/ComfyUI-VDN-H3.git \
    && git -C "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" fetch --depth 1 origin "${VDN_COMMIT}" \
    && git -C "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" checkout --detach FETCH_HEAD \
    && test "$(git -C "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" rev-parse HEAD)" = "${VDN_COMMIT}"
COPY scripts/download_models.py scripts/smoke_nodes.py /opt/vdn-h3/scripts/
COPY models.lock.json /opt/vdn-h3/models.lock.json
COPY workflows/ /opt/vdn-h3/workflows/
COPY README.md /opt/vdn-h3/README.md
RUN python -m compileall -q /opt/vdn-h3/scripts "${COMFYUI_DIR}/custom_nodes/ComfyUI-VDN-H3" \
    && cd "${COMFYUI_DIR}" && timeout 300 python /opt/vdn-h3/scripts/smoke_nodes.py --cpu
# Inherit tini, SSH bootstrap, healthcheck, and --cache-none --disable-pinned-memory.
# Inherit no stop/terminate action and no automatic generation.
