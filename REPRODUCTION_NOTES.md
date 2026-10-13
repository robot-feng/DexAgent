# DexAgent reproduction notes

Updated 2026-10-12.

## Execution and model choices

- The current interactive Codex agent is driving the reproduction; no separate Codex CLI login or Anthropic/OpenAI model API call was used. Stage 1–3 matched the bundled `place_cup_in_bowl` example, so Stage 1–2 reused its verified outputs and Stage 3 adapted its verified trajectory.
- The gated SAM 3.1 segmentation backend was replaced by `yonigozlan/sam3-litetext-s0`. Its local checkpoint is `/data/tzq/datasets/starVLA/playground/Pretrained_models/sam3-litetext-s0`; Transformers 5.13.1 runs it with the existing PyTorch 2.5.1+cu124. Inference selects one GPU with the most free memory because automatic multi-GPU layer splitting failed on this model.
- Stage 4 did not have `netflix/void-model`'s `void_pass1.safetensors` (~11 GB) or `alibaba-pai/CogVideoX-Fun-V1.5-5b-InP` (~20 GB). Instead of downloading these, its static-camera plate step uses LaMa (`big-lama.pt`, ~197 MiB) as an image-inpainting substitute for low-coverage regions. The checkpoint lives at `/data/tzq/datasets/starVLA/playground/Pretrained_models/lama-big/big-lama.pt`, SHA-256 `7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c`.
- Stage 4's 85 sampled frames use SAM3-LiteText masks. About 77.5% of pixels are visible in every sampled frame; 3.4% remain covered throughout, mostly the stationary bowl area. The LaMa-filled clean plate was visually checked.

## Current run state

- Stage 4 randomization accepted 2 of 4 attempts, with physics replay enabled; the original trajectory also passed replay.
- The LaMa clean plate completed, then export produced all 9 bundles: 2 randomized samples plus the original, each with ego, left-wrist and right-wrist views at 236 frames and 1280×1024.
- Blender Cycles rendering completed serially with the configured 32 samples to avoid contending for memory on four A100 GPUs already used by other jobs. All 9 views (2 randomized samples plus the original, each with ego and two wrist cameras) have exactly 236 rendered frames. Composite produced 13 H.264 videos at 15 fps; `ffprobe` verified every video has 236 frames and a 15.73-second duration. The side-by-side preview and human-vs-generated comparison were visually checked.
- The cloned environment can import `bpy 5.0.1`, and export plus Cycles rendering both started successfully. `pip check` nevertheless reports `bpy 5.0.1 is not supported on this platform`; this warning does not match the runtime behavior observed so far.
