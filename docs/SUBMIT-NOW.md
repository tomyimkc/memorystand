# Submit now — owner-only last mile

Deadline: **2026-08-18 17:00 Eastern**. Form: https://cockroachdb-ai.devpost.com/

Do these in order. Do not improvise field values; they are already written in
[`SUBMISSION.md`](SUBMISSION.md).

## 1. Upload the video (this is the remaining hard blocker)

Upload this cut — regenerated 2026-08-17 with the same Grok presenter pipeline
as the earlier contest films (image_edit likeness → image_to_video lip-sync →
whisper verify → compose). 12/12 shots passed, 0 ms lip-sync residual:

| Cut | Path | Runtime | SHA-256 |
|---|---|---|---|
| **Use this** | `~/Downloads/MemoryStand-Grok-public-presenter-2026-08-17.mp4` | 110.708 s | `9af40b085a477f1dc3385a2e19dba19a0b7486100834068cb69b7cbff99d383e` |
| Sidecar SRT | `~/Downloads/MemoryStand-Grok-public-presenter-2026-08-17.srt` | 35 cues | `ee1141c3254c092b229a54c08bae3c8862dcc5748ef3a4d9297ebf5529212c6f` |

1. YouTube or Vimeo, **Public**, not Unlisted.
2. Watch the public link logged out, start to finish.
3. Paste the URL into Devpost's video field.

No channel credential exists in this environment, so this step cannot be automated.

## 2. Devpost fields (paste from SUBMISSION.md)

| # | Field | Value |
|---|---|---|
| 1 | Demo app URL | `https://main.d19xad9aeccy3e.amplifyapp.com` |
| 2 | Testing instructions | Field 2 in SUBMISSION.md (demo credential is auto-filled from `/health`) |
| 3 | Public repo | `https://github.com/tomyimkc/memorystand` |
| 4 | License | `https://github.com/tomyimkc/memorystand/blob/main/LICENSE` |
| 5–7, 9, 11, 15 | Cockroach / AWS / integration / prior work / feedback / AI tools | paste the drafted blocks in SUBMISSION.md |
| 8 | Project start date | `08-03-26` |
| 10 | Architecture | `docs/architecture.png` (already attached as draft attachment 8745) |
| 12 | Submitter type | Individual |
| 13 | Country | Hong Kong |
| 14 | Organization | *(blank)* |
| 16 | Level of learning | Moderate |
| 17 | AI career value | Yes |
| 18–20 | Eligibility checkboxes | Yes / Yes / Yes |

## 3. Click Submit

Confirm the dashboard says **submitted**, not draft. Then leave the live demo up through 2026-09-15.

## 4. After the contest

Rotate `/memorystand/anthropic_api_key` and the published demo credential. The router wallet is empty (HTTP 402); funding it is optional because the fallback already demonstrates the thesis.
