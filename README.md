# MiniMax H3 ComfyUI image for Vast.ai

Docker image for MiniMax H3 on Vast.ai. It uses the pinned Vast `base-image`
and installs only the GPU runtime, ComfyUI 0.32.0, Manager, and API wrapper.
No Stable Diffusion checkpoint or MiniMax H3 model is included in the image.

## Security

Never commit a Hugging Face token. The token previously pasted into chat must
be revoked and replaced. Set a new token only in the shell or Vast secret:

```bash
export HF_TOKEN='hf_...'
export CF_TOKEN='สร้าง-token-ยาวๆ-ของคุณเอง'
```

## Build

```bash
cd minimax-h3-vast-image
# For local build, the default `minimax-h3-comfy` is enough.
# For Docker Hub, replace this with your lowercase Docker Hub namespace.
export IMAGE_NAME=yourdockerhubuser/minimax-h3-comfy
export IMAGE_TAG=0.2.0
./build.sh build
```

The image is `linux/amd64`, intended for Vast.ai. A Mac can build it with
Docker Desktop emulation, but the Mac is not the target GPU runtime. The
Vast base digest is pinned in `Dockerfile`.

The size reduction comes from removing the baked-in Stable Diffusion
checkpoint from the previous `vastai/comfy` image. PyTorch/CUDA remains large
because it is required for MiniMax H3 GPU inference.

For a local Mac build, `build.sh` can use a locally available `0.1.0` image as
the build-only runtime source, which avoids re-downloading CUDA wheels. The
final image still starts from the pinned Vast `base-image` and never copies
`/opt/model_store`:

```bash
COMFY_SOURCE_IMAGE=tonkung/minimax-h3-comfy:0.1.0 ./build.sh build
```

On a clean builder, omit that variable; the default source is the official
Vast ComfyUI image. This source stage is not the final base and its baked-in
checkpoint is excluded from the output image.

## Probe authentication and download

This downloads only a small byte range from each selected file. It does not
produce usable model files and does not download the models completely.

```bash
export HF_TOKEN='hf_...'
CF_TOKEN="$CF_TOKEN" MODEL_PRESET=5090 ./build.sh probe
```

On a Mac without an NVIDIA runtime, omit GPU allocation for this probe:

```bash
USE_GPU=0 CF_TOKEN="$CF_TOKEN" MODEL_PRESET=5090 ./build.sh probe
```

For a UI-only local smoke test, no token or model download is needed:

```bash
CF_TOKEN='สร้าง-token-ยาวๆ-ของคุณเอง' USE_GPU=0 DOWNLOAD_MODE=skip ./build.sh run
docker logs -f minimax-h3-comfy
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
authenticated proxy ports 19111, 19080, 19384, 19188, and 19288. The proxy
forwards to the actual service ports 11111, 18080, 18384, 18188, and 18288.
The supplied
`72299` value is omitted because Docker ports cannot exceed 65535.
`COMFYUI_ARGS`, `PORTAL_CONFIG`, `MODEL_PRESET`, `MODEL_FILES`, and
`DOWNLOAD_MODE` can be overridden with environment variables.

For a custom download list, set `MODEL_MANIFEST_JSON` to a JSON array of
`url`, `description`, `destination`, and `download` entries. Set `download`
to `false` to exclude a file; omitted `download` defaults to `true`. The
filename is derived from the URL and an existing file at that destination is
replaced. Alternatively,
set `MODEL_MANIFEST_URL` to a public GitHub Raw (or other HTTP(S)) JSON URL, or
set `MODEL_MANIFEST` to a mounted JSON file path. If none are set, the bundled
manifest and preset behavior are unchanged. The priority is
`MODEL_MANIFEST_JSON`, `MODEL_MANIFEST_URL`, `MODEL_MANIFEST`, then the bundled
file. Example:

```json
[{"url":"https://example.com/model.safetensors","description":"custom model","destination":"diffusion_models","download":true}]
```

Example URL override:

```text
MODEL_MANIFEST_URL=https://raw.githubusercontent.com/USER/REPO/main/model-manifest.json
```

## Cloudflare and CF_TOKEN authentication

The container refuses to start unless `CF_TOKEN` is set. Every Cloudflare
tunnel and published service port is protected by the same token. Open the
ComfyUI tunnel with:

```text
https://your-tunnel.trycloudflare.com/?token=YOUR_CF_TOKEN
```

The proxy exchanges the query token for an HttpOnly cookie and redirects to a
clean URL. A missing or incorrect token returns `401 Unauthorized`. For API
calls, prefer a header:

```bash
curl https://your-tunnel.trycloudflare.com/api/... \
  -H 'Authorization: Bearer YOUR_CF_TOKEN'
```

Do not commit the token or put it in the image. Query-string tokens can appear
temporarily in browser history or access logs; use the Authorization header
for automation.

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
