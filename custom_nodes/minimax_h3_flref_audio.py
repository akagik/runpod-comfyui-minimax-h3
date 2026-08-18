"""MiniMax H3 first/last keyframe + reference-audio conditioning (fl2va + voice ref).

Combines the stock MiniMaxH3ImageToVideo keyframe anchors with
MiniMaxH3ReferenceToVideo's standalone reference audio in one conditioning.
Prompt tags follow the ref2va presentation order:
  <Picture 1> = first frame, <Picture 2> = last frame (when both are given),
  <Audio 1> = the voice reference.

Also patches comfy.model_base.MiniMaxH3.extra_conds: the stock code overwrites
payload["cond_video_latents"] when both minimax_keyframes and minimax_refs are
present, dropping the keyframe latents. The patch concatenates keyframe latents
first, matching PackedLayout's segment order (keyframe cond rows precede ref
blocks). Single-mode graphs (keyframes only / refs only) are unaffected.
"""

import comfy.model_base
import node_helpers
from comfy_extras.nodes_minimax_h3 import (
    MiniMaxH3ReferenceToVideo,
    _empty_av_latent,
    _resize,
)


def _patch_extra_conds():
    cls = comfy.model_base.MiniMaxH3
    if getattr(cls, "_flref_audio_patched", False):
        return
    orig = cls.extra_conds

    def extra_conds(self, **kwargs):
        out = orig(self, **kwargs)
        keyframes = kwargs.get("minimax_keyframes")
        refs = kwargs.get("minimax_refs")
        if keyframes and refs:
            payload = out["minimax_payload"].cond
            payload["cond_video_latents"] = (
                [kf["latent"] for kf in keyframes]
                + [r["latent"] for r in refs if "latent" in r]
            )
        return out

    cls.extra_conds = extra_conds
    cls._flref_audio_patched = True


_patch_extra_conds()


class MiniMaxH3FirstLastAudioToVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "audio_vae": ("VAE",),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "width": ("INT", {"default": 1344, "min": 32, "max": 16384, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": 16384, "step": 32}),
                "length": ("INT", {"default": 124, "min": 5, "max": 3600, "step": 17,
                                   "tooltip": "Frame count at 24 fps, snapped up to the model's 17k+5 grid (124 = ~5s)"}),
            },
            "optional": {
                "first_frame": ("IMAGE",),
                "last_frame": ("IMAGE",),
                "ref_audio": ("AUDIO", {"tooltip": "Standalone voice reference; refer to it as <Audio 1> in the prompt"}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "LATENT")
    FUNCTION = "execute"
    CATEGORY = "model/conditioning/minimax"
    DESCRIPTION = ("fl2va keyframes plus a standalone voice reference. Prompt tags: "
                   "<Picture 1>/<Picture 2> = keyframes in input order, <Audio 1> = ref audio.")

    def execute(self, clip, vae, audio_vae, prompt, width, height, length,
                first_frame=None, last_frame=None, ref_audio=None):
        latent, frame_count = _empty_av_latent(width, height, length)

        ref_items = []
        keyframes = []
        if first_frame is not None:
            # geometry anchor: plain stretch to canvas (same policy as MiniMaxH3ImageToVideo)
            img = _resize(first_frame[:1], width, height, "disabled")
            ref_items.append({"type": "image", "data": img})
            keyframes.append({"resolved_frame_index": 0, "image": img})
        if last_frame is not None:
            # follower: aspect-preserving cover-crop
            img = _resize(last_frame[:1], width, height, "center")
            ref_items.append({"type": "image", "data": img})
            keyframes.append({"resolved_frame_index": frame_count - 1, "image": img})

        refs = []
        if ref_audio is not None:
            audio_latent, ref_audio_t = MiniMaxH3ReferenceToVideo._encode_ref_audio(
                audio_vae, ref_audio)
            ref_items.append({"type": "audio"})
            refs.append({"kind": "audio", "ref_audio_t": ref_audio_t,
                         "audio_latent": audio_latent})

        if ref_items:
            tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
        else:
            tokens = clip.tokenize(prompt)
        cond = clip.encode_from_tokens_scheduled(tokens)

        values = {}
        if keyframes:
            for kf in keyframes:
                kf["latent"] = vae.encode(kf.pop("image"))
            values["minimax_keyframes"] = keyframes
            values["minimax_frame_count"] = frame_count
        if refs:
            values["minimax_refs"] = refs
        if values:
            cond = node_helpers.conditioning_set_values(cond, values)
        return (cond, latent)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3FirstLastAudioToVideo": MiniMaxH3FirstLastAudioToVideo,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3FirstLastAudioToVideo": "MiniMax H3 First/Last + Ref Audio to Video",
}
