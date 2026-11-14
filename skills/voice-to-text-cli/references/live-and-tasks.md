# Live capture and task operations

Use the configured Python environment and project working directory from `SKILL.md`.
Commands below show its `python -X utf8 -m voice_to_text` form.

## Live capture

```powershell
python -X utf8 -m voice_to_text devices
python -X utf8 -m voice_to_text listen --source both --mic-device-index 3 --system-device-index 7 --language auto --json
```

Replace device indices with the current `devices` output; indices are not stable.
Omit them to use default devices. Both sources remain independent, with `source`
on each transcript. A microphone index must be a real input, and a system index
must be a loopback input. A new CLI process enumerates current devices; no GUI
restart or refresh click is needed.

`listen` waits for model readiness, then begins capturing. Ctrl+C stops capture
and drains final audio. Its JSON Lines stream has `status` / `transcript` records;
the final `stopped` status has `complete`, `error`, and `transcript_paths`. Inspect
the final state and exit status, rather than treating any transcript as success.
Exit 3 signals incomplete output; startup errors exit 1. Default model/flush
timeouts are 180/300 seconds. These can be overridden with
`--model-load-timeout-seconds` / `--flush-timeout-seconds`.

For bounded Agent recordings use `transcribe-speaker --seconds N --json` instead
of trying to infer elapsed capture time from process startup. For controlled
playback use `serve` and `session start/wait-ready/stop/wait`; the service must be
running, and playback starts only after readiness.

`--view all/mic/system` selects display without changing capture. `--verbose`
sends logs to stderr. `--output-dir` writes `transcript_all.md`,
`transcript_mic.md`, and `transcript_system.md`; use a unique directory per run.
`--interactive` is for a human terminal, accepts clear/status/view/stop, and
cannot be combined with `--json`. Agent automation should use JSON or sessions.

Video recognition and independent optimization accept cooperative Ctrl+C
cancellation. It waits for the active download or API request to return, checks
recognition between segments, and exits 130. Existing files are retained.
Do not claim an immediate hard stop. Direct `download-video` retains the original
downloader's completion behavior.

## Task operations

CLI, GUI, and HTTP share `%LOCALAPPDATA%/VoiceToText/tasks.sqlite3`.
`VTT_TASK_DB` or task `--db` can select another database. Task management needs
neither GUI nor HTTP service. Prefer an isolated database when testing changes.

```powershell
python -X utf8 -m voice_to_text task add --url "https://example.com/video" --instructions "Preserve terminology" --no-keep-audio
python -X utf8 -m voice_to_text task batch-add --urls-file "links.txt"
python -X utf8 -m voice_to_text task edit task_xxx --title "Interview" --chunk-seconds 10 --optimize --summarize
python -X utf8 -m voice_to_text task list --query "Interview" --status queued --page 1 --page-size 20
python -X utf8 -m voice_to_text task get task_xxx
python -X utf8 -m voice_to_text task result task_xxx --kind optimized --json
python -X utf8 -m voice_to_text task logs task_xxx --json
python -X utf8 -m voice_to_text task open task_xxx
python -X utf8 -m voice_to_text task stop task_xxx
python -X utf8 -m voice_to_text task retry task_xxx
```

Batch input accepts sharing text or newline URLs; `--urls-file -` reads stdin.
It saves valid tasks and reports `added` plus `errors`; partial failure exits 1.
Duplicate links require `--allow-duplicate` when intentionally adding another.

Creation/editing supports direct settings: `--output-dir`, `--instructions`,
`--chunk-seconds`, `--keep-audio/--no-keep-audio`, `--optimize/--no-optimize`, and
`--summarize/--no-summarize`, plus recognition configuration flags. Full payloads
remain supported via `--data` UTF-8 JSON. Direct flags override matching fields;
unprovided fields preserve JSON or existing settings. Editing requires queued
status; a terminal task must be retried before editing.

Unfiltered `task list` returns a JSON array. With query/status/page/page-size,
it returns `items`, `total`, `page`, `pages`, `page_size`, `counts`. Page sizes are
10, 20, or 50. `task result --kind` accepts raw/timestamped/optimized/summary/all.
In JSON, artifact entries contain path/text or null for missing outputs.
`task logs --json` returns status, error, progress, and events. `task open`
opens the existing output directory only when the user wants to see it.

### Executing the queue

Adding tasks does not run them or wake Codex. When the user requests execution,
the active Agent is the executor. Read
`docs/conversion-tasks.md` in the project checkout for the full protocol.

Claim with `task claim --owner codex`, retain the lease token, and heartbeat at
least every 30 seconds (lease lasts 120 seconds). Do not log tokens. Use
`task get` / heartbeat results to check `stop_requested`. On stopping, halt only
the playback and recording session owned by this task, then save/close and report
the appropriate cancelled/needs_review state. `task stop` requests this action;
it does not itself stop an audio session.

Task payload `config.model` maps to session `model_size`. Use the task's output
directory. Await session readiness before playback; stop after the source ends,
await drained results, and check completeness and detected speech. Produce the
enabled correction and summary outputs using the requested instructions.
Mark completed only when required files exist inside the task directory,
integrity is explicitly complete, and recording retention matches the task.
Treat uncertainty as needs_review. Do not silently claim automatic end-to-end
execution merely because a task was added or `summarize` was enabled.
