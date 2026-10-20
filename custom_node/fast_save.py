"""H3FastVideoSave: pipe frames to system ffmpeg (NVENC when available, else libx264)."""

import logging
import os
import shutil
import subprocess
import tempfile
import time
import wave

log = logging.getLogger(__name__)
_NVENC_OK = None  # cached result of the one-time probe


def ffmpeg_args(encoder, width, height, fps, crf, wav_path, out_path, frames):
    a = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-"]
    if wav_path:
        a += ["-i", wav_path]
    a += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
    if encoder == "nvenc":
        a += ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19",
              "-b:v", "0", "-pix_fmt", "yuv420p"]
    else:
        a += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if wav_path:
        a += ["-c:a", "aac", "-b:a", "192k", "-t", f"{frames / fps:.6f}"]
    a += ["-movflags", "+faststart", out_path]
    return a


def _nvenc_available():
    global _NVENC_OK
    if _NVENC_OK is None:
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, timeout=20)
            _NVENC_OK = r.returncode == 0
        except Exception:
            _NVENC_OK = False
    return _NVENC_OK


def _encode(encoder, images, w, h, fps, crf, wav_path, out_path):
    import torch
    n = images.shape[0]
    p = subprocess.Popen(ffmpeg_args(encoder, w, h, fps, crf, wav_path, out_path, n),
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    # Convert on the GPU when there is one: 243 frames at 1080x1920 took 12.9 s on CPU vs 4.2 s on CUDA (RTX 5090).
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        for i in range(0, n, 16):
            try:
                frames = images[i:i + 16].to(dev, non_blocking=True)
            except Exception:
                dev = "cpu"; frames = images[i:i + 16]
            chunk = frames.clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy().tobytes()
            p.stdin.write(chunk)
        p.stdin.close()
    except BrokenPipeError:
        pass  # ffmpeg died early; its stderr/returncode explains it below
    err = p.stderr.read().decode("utf-8", "replace")
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg ({encoder}) failed: {err[-800:]}")


class H3FastVideoSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
                "filename_prefix": ("STRING", {"default": "MiniMax_H3"}),
                "encoder": (["auto", "nvenc", "x264"], {"default": "auto"}),
                "crf": ("INT", {"default": 18, "min": 0, "max": 51}),
            },
            "optional": {"audio": ("AUDIO",)},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "MiniMax H3"

    def save(self, images, fps, filename_prefix, encoder="auto", crf=18, audio=None):
        import folder_paths
        import numpy as np

        if not shutil.which("ffmpeg"):
            raise RuntimeError("H3FastVideoSave: ffmpeg not found in PATH")
        n, h, w = int(images.shape[0]), int(images.shape[1]), int(images.shape[2])
        full, filename, counter, sub, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory(), w, h)
        name = f"{filename}_{counter:05}_.mp4"
        out_path = os.path.join(full, name)

        wav_path = None
        try:
            if audio is not None:
                pcm = audio["waveform"][0].float().clamp(-1, 1).cpu().numpy()  # [C,N]; float() because bf16 has no numpy dtype
                pcm = (pcm.T * 32767).round().astype(np.int16)
                fd, wav_path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(pcm.shape[1])
                    wf.setsampwidth(2)
                    wf.setframerate(int(audio["sample_rate"]))
                    wf.writeframes(pcm.tobytes())

            used = "nvenc" if encoder == "nvenc" or (encoder == "auto" and _nvenc_available()) else "x264"
            t0 = time.time()
            try:
                _encode(used, images, w, h, fps, crf, wav_path, out_path)
            except Exception as e:
                if used != "nvenc":
                    raise
                log.warning("H3FastVideoSave: nvenc failed (%s); retrying with x264", e)
                used = "x264"
                _encode(used, images, w, h, fps, crf, wav_path, out_path)
            log.info("H3FastVideoSave: encoder=%s frames=%d size=%dx%d seconds=%.1f",
                     used, n, w, h, time.time() - t0)
        finally:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)

        return {"ui": {"images": [{"filename": name, "subfolder": sub, "type": "output"}],
                       "animated": (True,)}}
