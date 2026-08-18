IMAGE ?= ghcr.io/akagik/runpod-comfyui-minimax-h3:dev

.PHONY: lock validate build

lock:
	uv pip compile requirements.in \
		--python-version 3.12 \
		--python-platform x86_64-manylinux_2_28 \
		--generate-hashes \
		--output-file requirements.lock.txt

validate:
	python3 -m json.tool workflows/minimax_h3_r2va_api_1344x768.json >/dev/null
	python3 -m py_compile worker/handler.py
	python3 -m unittest discover -s tests
	@test "$$(shasum -a 256 custom_nodes/minimax_h3_flref_audio.py | awk '{print $$1}')" = "5df26f7202fda5c1a3e7f5fdcef4bf6f14a95abd56ec0180e87c52e9730f651b"
	@if find . -type f \( -name '*.safetensors' -o -name '*.ckpt' -o -name '*.gguf' -o -name '*.mp4' -o -name '*.png' \) -print -quit | grep -q .; then \
		echo 'Forbidden model/media file found' >&2; exit 1; \
	fi

build: validate
	docker build --platform linux/amd64 --tag "$(IMAGE)" .
