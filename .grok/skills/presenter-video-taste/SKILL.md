---
name: presenter-video-taste
description: >
  Frame and compose talking-head / Grok presenter videos as a medium shot, never
  a face-fill close-up. Use when generating or editing a presenter cut, Remotion
  talking-head, image_to_video lip-sync, contest demo video, profile Grok video,
  or when a face looks too close / cropped / PPT-like. Slash: /presenter-video-taste.
  Negative override: do not use for game sprites, icons, or product-only screen captures.
---

# Presenter video taste

A portrait close-up `objectFit: cover` onto 16:9 crops the mouth off and looks like a webcam. That is a failed cut.

## Source frames first

1. Generate or composite the **base frame as 16:9** (1920×1080 or 1280×720). Never animate a tall portrait and stretch it later.
2. **Medium shot, not ECU.** Head + both shoulders + upper chest. Head height ≈ 30–40% of the frame, not 70%+.
3. Leave headroom above the hair. Sit the subject on the lower two-thirds. Do not cut the chin, ears, or crown.
4. Same likeness, same wardrobe, same lighting across beats. Derive LEFT/RIGHT from one reference with `image_edit`, never a fresh `image_gen` of a real person.
5. Extract a still from the composed 16:9 frame and **read it** before generating video. If you cannot see both shoulders and the mouth, the frame is wrong — fix the still, do not animate it.

## Animate

- `image_to_video` from that 16:9 medium still. Prompt: exact spoken line, still camera, subtle motion only.
- Duration 10s. Do not ask for a push-in.
- Rate-limit: two takes at a time. Copy each `videos/N.mp4` into the clip slot before the next generate.
- After any regen, re-run whisper. An old PASS receipt is a lie if the file on disk changed.

## Compose (Remotion)

- 16:9 medium take: `objectFit: "cover"` + `objectPosition: "center center"`.
- **Never** `objectPosition: "center 42%"` to "fix" a portrait — that is how the 2026-08-17 MemoryStand cut became a face-fill.
- Portrait take: `objectFit: "contain"` on a dark 16:9 canvas. Never `cover` a 3:4 portrait onto 16:9.
- Talking head is the picture. No slide boards unless the user asked for product footage.
- Captions in the empty lower band. Lower-third on the **empty** side (LEFT presenter → right).

## Verify before you say done

Extract a frame at ~2s and ~mid-shot. Fail if: forehead-only, mouth under a caption, no shoulders, or the subject fills more than half the frame height.
