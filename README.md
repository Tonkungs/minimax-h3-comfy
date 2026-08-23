# MiniMax H3 ComfyUI image for Vast.ai

Docker image built on the pinned Vast ComfyUI image with runtime-selectable
MiniMax H3 model downloads from `Comfy-Org/MiniMax-H3`.

## Security

Never commit a Hugging Face token. The token previously pasted into chat must
be revoked and replaced. Set a new token only in the shell or Vast secret:

```bash
export HF_TOKEN='hf_...'
```

## Build

```bash
cd minimax-h3-vast-image
# For local build, the default `minimax-h3-comfy` is enough.
# For Docker Hub, replace this with your lowercase Docker Hub namespace.
export IMAGE_NAME=yourdockerhubuser/minimax-h3-comfy
export IMAGE_TAG=0.1.0
./build.sh build
```

The image is `linux/amd64`, intended for Vast.ai. A Mac can build it with
Docker Desktop emulation, but the Mac is not the target GPU runtime.

## Probe authentication and download

This downloads only a small byte range from each selected file. It does not
produce usable model files and does not download the models completely.

```bash
export HF_TOKEN='hf_...'
MODEL_PRESET=5090 ./build.sh probe
```

On a Mac without an NVIDIA runtime, omit GPU allocation for this probe:

```bash
USE_GPU=0 MODEL_PRESET=5090 ./build.sh probe
```

## Model selection

```bash
MODEL_PRESET=5090 ./build.sh run
MODEL_PRESET=6000 ./build.sh run
MODEL_PRESET=full ./build.sh run
MODEL_FILES='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors,minimax_h3_video_vae_fp16.safetensors' ./build.sh run
```

Supported modes are `skip`, `probe`, `missing`, and `full`. Use `skip` for a
CPU-only UI smoke test without a Hugging Face token. Models are stored under
`/workspace/models`, so mount `/workspace` as persistent Vast storage.

## Vast ports and defaults

The image exposes host ports 1111, 8080, 8384, 8188, and 8288. They map to the
actual service ports 11111, 18080, 18384, 18188, and 18288. The supplied
`72299` value is omitted because Docker ports cannot exceed 65535.
`COMFYUI_ARGS`, `PORTAL_CONFIG`, `MODEL_PRESET`, `MODEL_FILES`, and
`DOWNLOAD_MODE` can be overridden with environment variables.

## Official workflows

The runtime copies official MiniMax H3 workflow references into
`/workspace/workflows`. The source workflows are:

- T2V: https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_t2v.json
- I2V: https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_i2v.json
- R2V: https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json

The browser extension automatically loads `video_minimax_h3_i2v.json` when the
ComfyUI page opens. To suppress this for one browser load, append
`?autoload_i2v=0` to the ComfyUI URL.

## Startup order and Cloudflare tunnel

At container startup, model selection/download and workflow preparation happen
first. The Vast supervisor then starts ComfyUI and the other services. The
Cloudflare tunnel manager is gated by an HTTP readiness check and starts only
after ComfyUI responds on `COMFYUI_PORT` (default `18188`). If you override the
ComfyUI port in `COMFYUI_ARGS`, set the matching value too:

```bash
-e COMFYUI_PORT=18188
```

## License

Review the MiniMax H3 community license and the terms of every model/adaptor
before commercial use: https://huggingface.co/Comfy-Org/MiniMax-H3
