"""CLI access to the same persistent queue and artifacts as the desktop."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .task_store import ARTIFACTS, STATUS_LABELS, TaskStore, extract_urls, read_task_artifact

KINDS = dict(zip(("raw", "timestamped", "optimized", "summary"), ARTIFACTS))


def configure_task_parser(parser):
    parser.add_argument("action", choices=["add", "batch-add", "list", "get", "edit", "claim",
                                          "update", "heartbeat", "stop", "retry", "result", "logs", "open"])
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--urls-file", help="UTF-8 links/share text file; - reads stdin for batch-add")
    parser.add_argument("--title")
    parser.add_argument("--data", type=Path, help="UTF-8 JSON object for task creation/edit/update")
    parser.add_argument("--token", help="Lease token (or VTT_TASK_TOKEN)")
    parser.add_argument("--owner", default="codex")
    parser.add_argument("--allow-duplicate", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--instructions")
    for flag in ("keep-audio", "optimize", "summarize"):
        parser.add_argument("--" + flag, action=argparse.BooleanOptionalAction, default=None)
    for flag in ("model", "device", "compute-type", "language", "text-mode"):
        parser.add_argument("--" + flag)
    parser.add_argument("--chunk-seconds", type=float)
    parser.add_argument("--min-rms", type=float)
    parser.add_argument("--status", choices=list(STATUS_LABELS))
    parser.add_argument("--query", "--search", default="")
    parser.add_argument("--page", type=int)
    parser.add_argument("--page-size", type=int, choices=[10, 20, 50])
    parser.add_argument("--kind", choices=[*KINDS, "all"], default="all")
    parser.add_argument("--json", action="store_true", help="Structured artifact/log output (task metadata is always JSON)")


def _payload(args):
    data = json.loads(args.data.read_text(encoding="utf-8-sig")) if args.data else {}
    if not isinstance(data, dict):
        raise ValueError("--data must contain a JSON object")
    for key in ("url", "title", "instructions", "keep_audio", "optimize", "summarize"):
        value = getattr(args, key)
        if value is not None:
            data[key] = value
    if args.output_dir is not None:
        data["output_dir"] = str(args.output_dir)
    config = {key: getattr(args, key) for key in
              ("model", "device", "compute_type", "language", "text_mode", "chunk_seconds", "min_rms")
              if getattr(args, key) is not None}
    if config:
        if not isinstance(data.get("config", {}), dict):
            raise ValueError("config must be an object")
        data["config"] = {**data.get("config", {}), **config}
    return data


def cmd_task(args) -> int:
    store = TaskStore(args.db)
    action = args.action
    if action not in {"add", "batch-add", "list", "claim"} and not args.task_id:
        raise ValueError("task_id is required")
    data = _payload(args)
    code = 0
    if action == "add":
        result = store.add(data, args.allow_duplicate)
    elif action == "batch-add":
        text = data.get("url", "")
        if args.urls_file:
            text += "\n" + (sys.stdin.read() if args.urls_file == "-" else Path(args.urls_file).read_text(encoding="utf-8-sig"))
        urls = extract_urls(text)
        if not urls:
            raise ValueError("No http/https links found; supply --url or --urls-file")
        added, errors = [], []
        for url in urls:
            try:
                added.append(store.add({**data, "url": url}, args.allow_duplicate))
            except ValueError as exc:
                errors.append({"url": url, "error": str(exc)})
        result = {"added": added, "errors": errors}
        code = 1 if errors else 0
    elif action == "list":
        if args.page is not None or args.page_size is not None or args.query or args.status:
            result = store.page(args.page if args.page is not None else 1,
                                args.page_size if args.page_size is not None else 10, args.status, args.query)
        else:
            result = store.list()  # Preserve the existing machine-readable contract.
    elif action == "claim":
        result = store.claim(args.owner, args.task_id)
    elif action == "get":
        result = store.get(args.task_id)
    elif action == "edit":
        result = store.edit(args.task_id, data)
    elif action in {"update", "heartbeat"}:
        result = store.update(args.task_id, args.token or os.environ.get("VTT_TASK_TOKEN"), data)
    elif action == "stop":
        result = store.stop(args.task_id)
    elif action == "retry":
        result = store.retry(args.task_id)
    else:
        task = store.get(args.task_id)
        if action == "open":
            path = Path(task["payload"]["output_dir"])
            if not path.is_dir():
                raise ValueError("任务尚未开始，输出目录还未创建。")
            os.startfile(str(path))
            result = {"output_dir": str(path)}
        elif action == "logs":
            result = {"task_id": task["id"], "status": task["status"], "error": task["error"],
                      "progress": task["progress"], "events": task["events"]}
            if not args.json:
                print(f"{task['id']} | {task['status']} | {task['error'] or task['progress'].get('message', '')}")
                for event in task["events"]:
                    print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event['at']))}  {event['message']}")
                return 0
        else:
            keys = ARTIFACTS if args.kind == "all" else [KINDS[args.kind]]
            result = {key: read_task_artifact(task, key) for key in keys}
            if not any(result.values()):
                raise ValueError("结果尚未生成，或所选输出未启用")
            if not args.json:
                for key, artifact in result.items():
                    if artifact:
                        if len(keys) > 1:
                            print(f"--- {key} ---")
                        print(artifact["text"])
                return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
