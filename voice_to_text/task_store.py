"""Persistent conversion queue shared by the desktop UI, CLI and HTTP service."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ACTIVE = {"running", "waiting", "stopping"}
TERMINAL = {"completed", "needs_review", "failed", "cancelled", "interrupted"}
STATUS_LABELS = {"queued": "待处理", "running": "执行中", "waiting": "等待处理",
                 "stopping": "正在停止", "completed": "已完成", "needs_review": "需检查",
                 "failed": "失败", "cancelled": "已取消", "interrupted": "已中断"}
PHASE_LABELS = {"queued": "等待 Codex 领取", "preparing": "准备任务", "inspecting": "检查视频",
                "capturing": "播放 / 识别", "transcribing": "等待识别完成",
                "proofreading": "校对文字稿", "summarizing": "生成总结", "finished": "处理结束"}
DEFAULT_CONFIG = {"model": "medium", "device": "cuda", "compute_type": "float16", "language": "zh",
                  "chunk_seconds": 10, "min_rms": .001, "source": "system"}
ARTIFACTS = ("raw_transcript_path", "timestamped_transcript_path", "optimized_transcript_path", "summary_path")


def read_task_artifact(task, key):
    if key not in ARTIFACTS:
        raise ValueError("Unknown artifact kind")
    value = task["result"].get(key)
    if not value:
        return None
    path = Path(value).resolve()
    root = Path(task["payload"]["output_dir"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("结果文件不在任务输出目录")
    if not path.is_file():
        return None
    return {"path": str(path), "text": path.read_text(encoding="utf-8-sig")}


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    for key in ("chunk_seconds", "min_rms"):
        value = float(config[key])
        if not math.isfinite(value) or (value <= 0 if key == "chunk_seconds" else value < 0):
            raise ValueError(f"invalid {key}")


def database_path() -> Path:
    configured = os.environ.get("VTT_TASK_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "VoiceToText" / "tasks.sqlite3"


def extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip('.,;!，。；！、）)]}>"\'') for u in re.findall(r"https?://[^\s<>]+", text)))


class TaskStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT NOT NULL, created REAL NOT NULL,
                    updated REAL NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
                    lease_token TEXT, lease_until REAL, stop_requested INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0, owner TEXT NOT NULL DEFAULT '',
                    progress TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    at REAL NOT NULL, attempt INTEGER NOT NULL, message TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS task_order ON tasks(status,position);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db
        finally:
            db.close()

    def _expire(self, db):
        now = time.time()
        rows = db.execute("SELECT id,attempt FROM tasks WHERE status IN ('running','waiting','stopping') AND lease_until < ?", (now,)).fetchall()
        for row in rows:
            db.execute("UPDATE tasks SET status='interrupted',lease_token=NULL,lease_until=NULL,error=?,updated=? WHERE id=?",
                       ("执行器心跳超时；请检查浏览器和采集会话后重试", now, row["id"]))
            self._event(db, row["id"], row["attempt"], "执行中断：心跳超时")

    def _event(self, db, task_id, attempt, message):
        db.execute("INSERT INTO events(task_id,at,attempt,message) VALUES(?,?,?,?)", (task_id, time.time(), attempt, message))

    def _row(self, row, include_token=False):
        if row is None:
            raise KeyError("task not found")
        data = dict(row)
        for key in ("payload", "progress", "result"):
            data[key] = json.loads(data[key])
        data["stop_requested"] = bool(data["stop_requested"])
        if not include_token:
            data.pop("lease_token", None)
        return data

    def list(self, status=None):
        with self._db() as db:
            self._expire(db)
            rows = db.execute("SELECT * FROM tasks ORDER BY position,created").fetchall()
            return [self._row(row) for row in rows if not status or row["status"] == status]

    def get(self, task_id):
        with self._db() as db:
            self._expire(db)
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            task["events"] = [dict(row) for row in db.execute("SELECT at,attempt,message FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
            return task

    def page(self, page=1, page_size=10, status=None, query=""):
        """Return one ordered database page plus counts from the same snapshot."""
        page, page_size = int(page), int(page_size)
        if page < 1 or page_size not in (10, 20, 50):
            raise ValueError("page must be positive; page_size must be 10, 20 or 50")
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if query.strip():
            clauses.append("(instr(lower(title), lower(?)) > 0 OR instr(lower(url), lower(?)) > 0)")
            params.extend([query.strip(), query.strip()])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) AS n FROM tasks GROUP BY status")}
            total = db.execute("SELECT COUNT(*) FROM tasks" + where, params).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = db.execute("SELECT * FROM tasks" + where + " ORDER BY position,created,id LIMIT ? OFFSET ?",
                              [*params, page_size, (page - 1) * page_size]).fetchall()
            return {"items": [self._row(row) for row in rows], "total": total,
                    "page": page, "pages": pages, "page_size": page_size, "counts": counts}

    def add(self, data: dict, allow_duplicate=False):
        urls = extract_urls(str(data.get("url", "")))
        if len(urls) != 1 or not urlsplit(urls[0]).hostname:
            raise ValueError("请填写一条有效的 http/https 视频链接")
        url = urls[0]
        task_id = "task_" + uuid.uuid4().hex[:12]
        output_root = Path(data.get("output_dir") or Path(__file__).resolve().parents[1] / "transcripts" / "tasks").expanduser().resolve()
        if not isinstance(data.get("config", {}), dict): raise ValueError("config must be an object")
        config = {**DEFAULT_CONFIG, **data.get("config", {})}
        validate_config(config)
        payload = {"output_dir": str(output_root / task_id), "keep_audio": bool(data.get("keep_audio", False)),
                   "optimize": bool(data.get("optimize", True)), "summarize": bool(data.get("summarize", True)),
                   "instructions": str(data.get("instructions", "")), "config": config}
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not allow_duplicate and db.execute("SELECT 1 FROM tasks WHERE url=? AND status!='cancelled'", (url,)).fetchone():
                raise ValueError("已有相同链接的任务；如需重新处理，请选择重试或允许重复")
            pos = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM tasks").fetchone()[0]
            db.execute("INSERT INTO tasks(id,url,title,status,phase,created,updated,position,payload) VALUES(?,?,?,'queued','queued',?,?,?,?)",
                       (task_id, url, str(data.get("title", "")).strip(), now, now, pos, json.dumps(payload, ensure_ascii=False)))
            self._event(db, task_id, 0, "已添加，等待 Codex 领取")
        return self.get(task_id)

    def edit(self, task_id, data):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if task["status"] != "queued":
                raise ValueError("仅待处理任务可编辑；已结束任务请先重试")
            url = str(data.get("url", task["url"]))
            urls = extract_urls(url)
            if len(urls) != 1 or not urlsplit(urls[0]).hostname:
                raise ValueError("请填写有效的视频链接")
            if urls[0] != task["url"] and db.execute("SELECT 1 FROM tasks WHERE url=? AND id!=? AND status!='cancelled'", (urls[0], task_id)).fetchone():
                raise ValueError("已有相同链接的任务")
            payload = task["payload"]
            for key in ("keep_audio", "optimize", "summarize", "instructions", "output_dir"):
                if key in data:
                    payload[key] = data[key]
            if "config" in data:
                if not isinstance(data["config"], dict): raise ValueError("config must be an object")
                payload["config"] = {**payload["config"], **data["config"]}
            validate_config(payload["config"])
            payload["output_dir"] = str(Path(payload["output_dir"]).expanduser().resolve())
            db.execute("UPDATE tasks SET url=?,title=?,payload=?,updated=? WHERE id=?", (urls[0], str(data.get("title", task["title"])), json.dumps(payload, ensure_ascii=False), time.time(), task_id))
            self._event(db, task_id, task["attempt"], "已修改任务设置")
        return self.get(task_id)

    def claim(self, owner="codex", task_id=None):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            if db.execute("SELECT 1 FROM tasks WHERE status IN ('running','waiting','stopping')").fetchone():
                raise ValueError("已有任务在执行；系统音频任务必须串行处理")
            row = db.execute("SELECT * FROM tasks WHERE status='queued'" + (" AND id=?" if task_id else "") + " ORDER BY position LIMIT 1", (task_id,) if task_id else ()).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            now = time.time()
            db.execute("UPDATE tasks SET status='running',phase='preparing',lease_token=?,lease_until=?,updated=?,owner=?,attempt=attempt+1,stop_requested=0,error='' WHERE id=?",
                       (token, now + 120, now, owner, row["id"]))
            self._event(db, row["id"], row["attempt"] + 1, f"{owner} 已领取")
            return self._row(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone(), True)

    def update(self, task_id, token, data):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(), True)
            if task["lease_token"] != token or not token or task["status"] not in ACTIVE:
                raise ValueError("任务租约已失效，请重新领取；不能回写旧执行结果")
            status = data.get("status", task["status"])
            if status not in ACTIVE | TERMINAL:
                raise ValueError("invalid task status")
            if task["stop_requested"] and status in {"running", "waiting", "completed"}:
                raise ValueError("任务已请求停止；请停止采集并回写 cancelled/needs_review")
            phase = data.get("phase", task["phase"])
            if phase not in PHASE_LABELS:
                raise ValueError("invalid phase")
            result = {**task["result"], **data.get("result", {})}
            if status == "completed":
                required = ["raw_transcript_path", "timestamped_transcript_path"]
                if task["payload"]["optimize"]: required.append("optimized_transcript_path")
                if task["payload"]["summarize"]: required.append("summary_path")
                root = Path(task["payload"]["output_dir"]).resolve()
                for key in required:
                    path = Path(result.get(key, "")).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        raise ValueError(f"结果文件缺失或不在任务输出目录: {key}")
                if result.get("integrity", {}).get("complete") is not True:
                    raise ValueError("完成任务需要明确的 integrity.complete=true；否则请标记 needs_review")
                if not task["payload"]["keep_audio"] and any(root.rglob("*.wav")):
                    raise ValueError("任务设置为不保留录音，请导出结果并删除录音后再标记完成")
            now = time.time()
            done = status in TERMINAL
            db.execute("UPDATE tasks SET status=?,phase=?,title=?,progress=?,result=?,error=?,updated=?,lease_until=?,lease_token=? WHERE id=?",
                       (status, "finished" if done else phase, str(data.get("title", task["title"])),
                        json.dumps({**task["progress"], **data.get("progress", {})}, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                        str(data.get("error", task["error"])), now, None if done else now + 120, None if done else token, task_id))
            message = data.get("message")
            if message or status != task["status"] or phase != task["phase"]:
                self._event(db, task_id, task["attempt"], str(message or STATUS_LABELS[status] + " / " + PHASE_LABELS[phase]))
        return self.get(task_id)

    def stop(self, task_id):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if task["status"] == "queued":
                db.execute("UPDATE tasks SET status='cancelled',updated=? WHERE id=?", (time.time(), task_id))
            elif task["status"] in ACTIVE:
                db.execute("UPDATE tasks SET status='stopping',stop_requested=1,updated=? WHERE id=?", (time.time(), task_id))
            else:
                raise ValueError("任务已结束")
            self._event(db, task_id, task["attempt"], "已请求停止；执行器将在下次心跳时确认" if task["status"] in ACTIVE else "已取消")
        return self.get(task_id)

    def retry(self, task_id):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db)
            task = self._row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
            if task["status"] not in TERMINAL:
                raise ValueError("只有已结束或已中断的任务可重试")
            # Prior result manifests remain in events and output folders.
            if task["result"]:
                self._event(db, task_id, task["attempt"], "上次结果: " + json.dumps(task["result"], ensure_ascii=False))
            payload = task["payload"]
            payload["output_dir"] = str(Path(payload["output_dir"]).parent / f"{task_id}-attempt-{task['attempt'] + 1}")
            db.execute("UPDATE tasks SET status='queued',phase='queued',progress='{}',result='{}',error='',stop_requested=0,payload=?,updated=? WHERE id=?",
                       (json.dumps(payload, ensure_ascii=False), time.time(), task_id))
            self._event(db, task_id, task["attempt"], "已重新加入队列；保留历史文件")
        return self.get(task_id)
