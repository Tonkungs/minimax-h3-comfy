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
export IMAGE_TAG=1.5.0
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
```

Supported modes are `skip`, `probe`, `missing`, and `full`. Use `skip` for a
CPU-only UI smoke test without a Hugging Face token. Models are stored under
`/workspace/models`, so mount `/workspace` as persistent Vast storage.

## Vast ports and defaults

The image exposes host ports 1111, 8080, 8384, 8188, and 8288. Only 8188 is
served on all interfaces (TLS). 1111/8080/8288/8384 are token-gated `auth_proxy`
bridges on 127.0.0.1 forwarding to 11111/18080/18288/18384, used by the
Cloudflare tunnels. The 19xxx ports in `PORTAL_CONFIG`/`EXPOSE` have no listener.
The supplied
`72299` value is omitted because Docker ports cannot exceed 65535.

Port 8188 serves HTTPS with a self-signed certificate generated in the
container at first start (the key stays in `/etc/h3-tls`, never in
`/workspace`). Plain HTTP is accepted only from inside the container
(loopback, used by the Cloudflare tunnel); any other plain request gets
`400 Use HTTPS`. The tunnel bridges 1111/8080/8288/8384 now go through the same
token proxy.

`COMFYUI_ARGS`, `PORTAL_CONFIG`, `MODEL_PRESET`, and
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

For H3 startup, the image derives selected filenames from its manifest and
ignores any `MODEL_FILES` value (for example a stale one supplied by a Vast
template); use `MODEL_PRESET` or a custom manifest to choose models. Presets
`5090`, `6000` and `override` select only `diffusion_models`, `text_encoders`
and `vae` entries, so LatentSync entries are skipped; `MODEL_PRESET=full`
downloads them. Downloaded
files are linked into
`/opt/workspace-internal/ComfyUI/models/{diffusion_models,text_encoders,vae}`.
ComfyUI starts after preparation and is restarted automatically if links are
refreshed while it is already running. Official workflow copies default
`CLIPLoader device=cpu` for the Qwen encoder.

## Cloudflare and CF_TOKEN authentication

The container refuses to start unless `CF_TOKEN` is set. Every Cloudflare
tunnel (including the 1111/8080/8288/8384 bridges, which run `auth_proxy.py`)
and published service port is protected by the same token. Open the
ComfyUI tunnel with:

```text
https://your-tunnel.trycloudflare.com/?token=YOUR_CF_TOKEN
```

The proxy exchanges the query token for an HttpOnly cookie and redirects to a
clean URL. A missing or incorrect token returns `401 Unauthorized`. The proxy
strips its own token (Authorization bearer / `h3_auth` cookie) before forwarding
upstream. For API
calls, prefer a header:

```bash
curl https://your-tunnel.trycloudflare.com/api/... \
  -H 'Authorization: Bearer YOUR_CF_TOKEN'
```

Do not commit the token or put it in the image. Query-string tokens can appear
temporarily in browser history or access logs; use the Authorization header
for automation.

## H3 readiness and self-test API

The existing authenticated proxy also exposes these endpoints on the ComfyUI
port (default `8188`). They are available while models are downloading, so a
dispatcher can poll readiness over HTTPS (`-k` is for manual testing only; automation
must pin the certificate via the proof below):

```bash
curl -k https://INSTANCE/h3/status -H 'Authorization: Bearer YOUR_CF_TOKEN'
curl -k -X POST https://INSTANCE/h3/selftest \
  -H 'Authorization: Bearer YOUR_CF_TOKEN'
```

`GET /h3/status` returns `phase` (`booting`, `downloading`, `starting_comfy`,
`ready`, or `error`; `last_error` persists through `booting`/`downloading` and clears on `starting_comfy`/`ready`), `image_version`, `comfyui_health`, `last_error`, `last_error_at` (ISO UTC), `attempts` (prepare
starts), and a
`files` array with `name`, `destination`, `expected_bytes`, and
`downloaded_bytes`. Expected sizes come from selected manifest URLs; they are
`null` when Hugging Face metadata is unreachable. Downloaded bytes include the
current `.part` file. This release reports `image_version: "1.5.0"`, aligned
with the GitHub Actions image tag and `VERSION` file.

`POST /h3/selftest` starts one bundled-reference Ref2VA job (512x512, 3
seconds, 4 steps) in the background and returns `202 {"started": true, "run_id": "...", "started_at": <epoch>}`, or
`202 {"started": false, "running": true}` if one is already running. It only
runs after status is `ready`, and first checks ComfyUI `/object_info` for the
required nodes and inputs. Poll `GET /h3/status`: its `selftest` field holds
`{run_id, ok, duration_s, has_audio, output_bytes, error, started_at, finished_at, running}` for
the last run; match `run_id` to the 202 response (ffprobe checks duration and audio stream); generation can take
several minutes depending on the host.

### TLS proof (cert pinning)

```text
GET https://INSTANCE/h3/tls-proof        (no token)
200 {"scheme":"h3-tls-v1","cert_sha256":"<hex>","proof":"<hex>"}
proof = lowercase hex(HMAC-SHA256(key = UTF-8 bytes of CF_TOKEN,
        msg = "h3-tls-v1:" + lowercase hex SHA-256 of the leaf certificate DER))
```

- The client hashes the DER certificate it received in the TLS handshake
  itself (it does not trust `cert_sha256`), compares in constant time, sends
  no token before the proof matches, then pins that SHA-256 for every later call.
- A new container has a new certificate: re-run the proof on pin failure and
  never fall back to http. The certificate is reused across restarts of the
  same container.
- Because the proof is token-free, `CF_TOKEN` must be long and random (it
  allows offline guessing of weak tokens).

## Cheap Hugging Face download probe

For a cheap, plain Ubuntu probe instance with no H3 image or weights, set its
Vast `onstart` command to this one-liner after publishing the script:

```bash
bash -lc 'command -v curl >/dev/null || (apt-get update -qq && apt-get install -y -qq curl ca-certificates) >/dev/null 2>&1; curl -fsSL https://raw.githubusercontent.com/Tonkungs/minimax-h3-comfy/main/scripts/h3_download_probe.sh -o /tmp/h3p.sh && bash /tmp/h3p.sh || echo "PROBE_RESULT bytes=0 seconds=0 mbps=0.00 blocked=1"'
```

This works only after the script has been merged to `main`.

The script downloads only `minimax_h3_audio_vae_fp32.safetensors` with
`curl -fsL`, for at most 60 seconds per attempt. If the first attempt gets zero
bytes, it waits a randomized 60–90 seconds and retries once. Stdout contains
exactly one line, suitable for the Vast logs API:

```text
PROBE_RESULT bytes=<n> seconds=<s> mbps=<x>
```

Two zero-byte attempts end with
`PROBE_RESULT bytes=0 seconds=<s> mbps=0.00 blocked=1`.

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
