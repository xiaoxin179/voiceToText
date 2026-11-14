---
name: voice-to-text-cli
description: Use the local Voice Studio CLI to transcribe video links, local audio, microphone or system playback; optimize existing transcripts; or manage the desktop conversion queue. Use for transcription, recording, transcript cleanup, and task/result operations in voiceToText. Queue execution requires an active Agent; adding a task does not start it.
---

# Voice To Text CLI

## Current CLI capabilities

Run the CLI from the user's VoiceToText checkout, using the Python environment
where the project dependencies are installed (for example, activate the
`voice-to-text` conda environment). Locate the checkout from current workspace
context or ask for its location if unavailable; do not assume a machine-specific
path. An installed copy of this skill is separate from the software checkout.
Use `-X utf8` to prevent Windows console encoding from corrupting Chinese output:

```powershell
python -X utf8 -m voice_to_text --help
```

All examples assume the project working directory and its configured Python.
Before relying on an unfamiliar option, check that subcommand's `--help`.
Maintained behavior and schemas are in `docs/cli-desktop-parity.md` in the checkout.

Choose the operation that matches the request:

- Fixed-duration capture: `transcribe-speaker --seconds N --source system --json`.
- Live dual-source capture: `listen --source both --json`; stdout is JSON Lines,
  not one JSON object. See [live capture](references/live-and-tasks.md#live-capture).
- Existing transcript optimization: `optimize-file INPUT --output OUTPUT --json`.
  Do not download or recognize the video again merely to optimize or retry.
- Local audio recognition: `transcribe-file AUDIO`; this command prints text,
  and does not support `--json`.
- Video URL recognition/download: `transcribe-video` / `download-video`.
- Desktop task operations: `task add/batch-add/edit/list/get/result/logs/open/stop/retry`.
  Read [task operations](references/live-and-tasks.md#task-operations) when needed.
- Agent-controlled asynchronous recording: `serve` then `session`.

For automation, use JSON wherever supported. Video, speaker, download, and
optimization commands return one JSON object; their progress goes to stderr.
Tool output can merge the two streams: do not parse an entire merged progress log
as a JSON object. Transcripts on disk are UTF-8; read them with `utf-8-sig` if
console output is garbled. Do not repeat a recording just to fix display encoding.

### Existing transcript optimization

```powershell
python -X utf8 -m voice_to_text optimize-file "raw.md" --output "optimized.md" --json
```

This uses the same optimizer and file writer as the desktop. Input and output must
differ. The default output is `transcript_deepseek.md` beside the input. A failed
request preserves the last successful output. The response contains
`optimized_text`, `optimized_transcript_path`, and `optimized_token_usage`
(`prompt_tokens`, `completion_tokens`, `total_tokens`). Video transcription with
`--optimize-with-deepseek` returns the same usage fields. Keys come from
`DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY`, or `--deepseek-api-key`.

Distinguish local Agent cleanup from `optimize-file`, which calls the configured
remote DeepSeek-compatible service. Follow the user's choice of method; do not
silently introduce a paid API call when they only ask for raw recognition.

### Output and recording decisions

Reuse the user's established source, output, retention, and cleanup preferences.
For a request such as "record one minute and give me the recognition result",
use system audio, capture exactly the requested duration after model readiness,
return raw recognized text in chat, and treat the recording as temporary.
Use a unique task output directory under the workspace's `.tmp` directory.
No video title is needed for a raw capture test. Ask only for unresolved choices
that materially change the requested deliverable; do not repeat settled questions.

After speaker capture, check both `integrity.complete` and
`audio_diagnostics.status`. Exit 0 indicates complete recognition with detected
speech; exit 3 indicates incomplete capture or missing audio/speech. Report the
actual condition instead of presenting silence or partial output as success.
Read the UTF-8 transcript before removing any task-owned temporary output.

When a user has confirmed a working recognition configuration, preserve it.
Choose CPU or GPU based on the installed runtime and hardware, not assumptions
about the skill author's machine.
An explicit CPU request uses int8 unless the user specifies another precision.
CLI commands with `--device cpu` select int8 automatically when no precision is
provided. `--language auto` is supported.

## Recognition configuration

Speaker defaults are `medium`, `cuda`, `float16`, language `zh`, simplified text,
10-second chunks, and `min_rms=0.001`. These defaults require a compatible NVIDIA
runtime. On CPU-only machines select `--device cpu --compute-type int8` and a
suitable installed model. Honor explicit model, language, and device preferences;
do not silently downgrade a user-confirmed GPU configuration.
Chunk size and RMS apply to live capture, not file/video transcription.
The direct `python -X utf8 main.py <command>` entry point remains supported.

## Decision

- Use `transcribe-video` for URLs that can be downloaded or already point at audio/video media, such as Bilibili, YouTube, a direct `douyinvod`/`media-audio` URL, or a local HTTP media URL.
- Use `transcribe-speaker` when the site needs login, blocks downloads, hides media URLs, or the user wants transcription of audio currently playing through the computer speakers.
- For Douyin share links, first try `transcribe-video` only if a direct `douyinvod`/`media-audio` stream URL is available. Normal Douyin page/share URLs often fail because Chrome/Edge cookies are locked or v20/App-Bound encrypted.
- Use `--json` for automation when the command supports it. Progress logs go to stderr; parse stdout separately. `listen` returns JSON Lines, while `transcribe-file` returns text.

## Video URL

Run from the project directory:

```powershell
python -X utf8 -m voice_to_text transcribe-video "https://www.bilibili.com/video/BV..." --model medium --device cuda --compute-type float16 --language zh --text-mode simplified --json
```

Useful options:

- `--model tiny|base|small|medium|large-v3`
- `--device cpu|cuda`
- `--compute-type int8|float16|int8_float16|float32`
- `--language zh|en|auto`
- `--cookie-browser chrome|edge|firefox` only when browser cookie extraction is expected to work
- `--output-dir C:\path\to\transcripts`

Use the JSON fields:

- `raw_text`
- `timestamped_text`
- `audio_path`
- `raw_transcript_path`
- `timestamped_transcript_path`
- `output_dir`

## Speaker Audio

Use this for Douyin, logged-in pages, web courses, meetings, or any external video that can be played but not downloaded.

Use the user's requested destination and retention preferences. If they request
an Obsidian note, locate their vault and desired folder rather than assuming a
personal vault layout. Write only requested deliverables there. Keep intermediate
files in a unique task directory; remove only task-owned temporary output after
verifying the final deliverable, respecting requested audio retention. If cleanup
is blocked by a tool or policy, stop and report remaining paths; do not bypass it.
For unresolved file workflows, ask only about choices that affect the deliverable
(destination, audio retention, or raw versus cleaned text). A simple timed capture
can return raw text in chat using the defaults above without extra questions.

Recommended workflow after confirmation:

1. Determine the video title from the user, browser page title, visible page text, or URL context.
2. Start playback when the user asked you to handle a specific video. For "record what is playing", capture the current playback without changing it.
3. Run speaker capture for the expected duration.
4. Parse stdout JSON and use `raw_text` or `timestamped_text`.
5. If the user requested raw output, move/copy only the requested transcript into the confirmed final location.
6. If the user requested optimization or a final Obsidian note, create the cleaned deliverable using the video title as context; keep raw files in the task's temporary output directory during preparation.
7. After verifying the requested deliverable, remove temporary transcript and audio files unless the user explicitly asked to keep them. If cleanup is blocked, report the paths and stop; do not route around the block.

```powershell
python -X utf8 -m voice_to_text transcribe-speaker --seconds 120 --source system --model medium --device cuda --compute-type float16 --language zh --text-mode simplified --chunk-seconds 10 --min-rms 0.001 --json
```

Use `--source mic` only when the user explicitly wants microphone input. For system playback, use `--source system`.

Important constraints:

- Speaker capture records all computer audio, not just one browser tab.
- Avoid other videos, notifications, TTS, or music during capture.
- The command blocks until the requested duration ends.
- Use `--silence-stop-seconds 8` to stop early after sustained silence once speech/audio has been heard.
- The CLI currently writes `speaker.wav`; treat it as temporary unless the user explicitly asks to retain it.

## Codex Optimization

When the user wants Codex to optimize the extracted result, do not merely summarize. Produce a cleaned transcript suitable for later reading or knowledge-base ingestion.

Use the video title as the primary glossary/context signal:

- Infer likely domain terms, product names, proper nouns, and acronyms from the title.
- Correct obvious ASR mistakes using title context and surrounding sentences.
- Preserve uncertain words instead of inventing facts. If a term is unclear, keep the closest ASR wording or mark it as uncertain.
- Add punctuation and paragraphs.
- Remove repeated filler caused by playback/ASR artifacts.
- Keep the original meaning and do not add content that was not in the audio.

For tasks where the user explicitly requests separate transcripts, output files may include:

- Raw ASR transcript: keep the CLI-generated `transcript_raw.md`.
- Timestamped ASR transcript: keep the CLI-generated `transcript_timestamped.md`.
- Optimized transcript: write `transcript_optimized.md` in the confirmed final directory unless the user specifies another path.

If the user requested a single final transcript path, write the optimized version there when optimization is enabled; otherwise write the raw transcript there. For an Obsidian workflow, save the requested final note and only the additional artifacts the user requested.

## Failure Handling

- If `transcribe-video` fails on Douyin with cookie errors, do not keep retrying Chrome/Edge cookies. Use speaker capture, or capture a direct `media-audio`/`douyinvod` URL from browser DevTools and pass that URL to `transcribe-video`.
- If the result has empty `raw_text`, check whether audio was actually playing, increase `--seconds`, lower `--min-rms`, or use a larger model.
- If CUDA fails, report the CUDA error and check the local runtime. Use `--device cpu --compute-type int8` only when the user requests or accepts a CPU fallback; do not silently replace their GPU configuration.
- If a direct Douyin media URL returns 403, it probably expired. Reopen/play the page and capture a fresh media URL, or use speaker capture.

## Local HTTP Alternative

CLI is the default. If a long-running session API is more convenient, start:

```powershell
python -X utf8 -m voice_to_text serve --host 127.0.0.1 --port 8765
```

Then use:

- `GET http://127.0.0.1:8765/health`
- `POST http://127.0.0.1:8765/transcribe/video`
- `POST http://127.0.0.1:8765/sessions/speaker`
- `GET http://127.0.0.1:8765/sessions/{session_id}?result=1`
- `POST http://127.0.0.1:8765/sessions/{session_id}/stop`
