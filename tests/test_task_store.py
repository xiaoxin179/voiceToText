import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from voice_to_text.task_store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / "tasks.db")

    def add(self, **kw):
        return self.store.add({"url": "https://www.douyin.com/video/123", "output_dir": str(self.root / "out"), **kw})

    def test_persistent_defaults_edit_duplicate(self):
        task = self.add()
        self.assertEqual(task["payload"]["config"]["chunk_seconds"], 10)
        self.assertFalse(task["payload"]["keep_audio"])
        self.assertEqual(TaskStore(self.store.path).list()[0]["id"], task["id"])
        self.store.edit(task["id"], {"title": "新标题"})
        self.assertEqual(self.store.get(task["id"])["title"], "新标题")
        with self.assertRaises(ValueError): self.add()

    def test_partial_config_edit_and_invalid_values(self):
        task = self.add()
        edited = self.store.edit(task["id"], {"config": {"chunk_seconds": 5}})
        self.assertEqual(edited["payload"]["config"]["model"], "medium")
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError): self.store.edit(task["id"], {"config": {"chunk_seconds": value}})

    def test_cli_and_http_share_database(self):
        import contextlib
        import io
        import json
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from voice_to_text.api_server import VoiceToTextHandler
        from voice_to_text.cli import main
        with patch.dict(os.environ, {"VTT_TASK_DB": str(self.store.path)}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "add", "--url", "https://example.com/cli"]), 0)
            task = json.loads(output.getvalue())
            server = ThreadingHTTPServer(("127.0.0.1", 0), VoiceToTextHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_port}"
                def request(path, data=None):
                    req = urllib.request.Request(endpoint + path, data=json.dumps(data).encode() if data is not None else None,
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req) as response: return json.load(response)
                self.assertEqual(request("/tasks")["tasks"][0]["id"], task["id"])
                claim = request("/tasks/claim", {})["task"]
                self.assertIn("lease_token", claim)
                self.assertNotIn("lease_token", request("/tasks/" + task["id"]))
                request(f"/tasks/{task['id']}/stop", {})
                result = request(f"/tasks/{task['id']}/update", {"lease_token": claim["lease_token"], "status": "cancelled"})
                self.assertEqual(result["status"], "cancelled")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_serial_atomic_claim(self):
        self.add()
        self.add(url="https://example.com/second")
        def claim(_):
            try: return self.store.claim()
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(claim, range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        task = next(r for r in results if r)
        self.assertNotIn("lease_token", self.store.get(task["id"]))
        with self.assertRaises(ValueError): self.store.edit(task["id"], {"title": "bad"})

    def test_database_pagination_search_and_clamping(self):
        tasks = [self.add(url=f"https://example.com/{i}", title=f"视频 {i:02}") for i in range(23)]
        first = self.store.page()
        self.assertEqual((first["total"], first["pages"], len(first["items"])), (23, 3, 10))
        self.assertEqual(self.store.page(2)["items"][0]["id"], tasks[10]["id"])
        last = self.store.page(999)
        self.assertEqual((last["page"], len(last["items"])), (3, 3))
        self.assertEqual(self.store.page(query="视频 22")["total"], 1)
        self.assertEqual(self.store.page(query="%")["total"], 0)
        self.store.stop(tasks[0]["id"])
        filtered = self.store.page(status="cancelled")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["counts"]["queued"], 22)
        self.assertEqual(self.store.page(page_size=20)["pages"], 2)
        with self.assertRaises(ValueError): self.store.page(page_size=0)

    def test_lease_expiry_fences_old_worker(self):
        self.add()
        task = self.store.claim()
        with self.store._db() as db: db.execute("UPDATE tasks SET lease_until=0")
        self.assertEqual(self.store.get(task["id"])["status"], "interrupted")
        with self.assertRaises(ValueError): self.store.update(task["id"], task["lease_token"], {})
        self.store.retry(task["id"])
        other = self.store.claim()
        self.assertNotEqual(task["lease_token"], other["lease_token"])

    def test_stop_requires_acknowledgement(self):
        self.add()
        task = self.store.claim()
        self.store.stop(task["id"])
        heartbeat = self.store.update(task["id"], task["lease_token"], {})
        self.assertTrue(heartbeat["stop_requested"])
        with self.assertRaises(ValueError): self.store.update(task["id"], task["lease_token"], {"status": "completed"})
        self.assertEqual(self.store.update(task["id"], task["lease_token"], {"status": "cancelled"})["status"], "cancelled")

    def test_completion_requires_real_artifacts_and_integrity(self):
        self.add()
        task = self.store.claim()
        token = task["lease_token"]
        with self.assertRaises(ValueError): self.store.update(task["id"], token, {"status": "completed"})
        root = Path(task["payload"]["output_dir"])
        root.mkdir(parents=True)
        result = {"integrity": {"complete": True}}
        for key in ("raw_transcript_path", "timestamped_transcript_path", "optimized_transcript_path", "summary_path"):
            path = root / (key + ".md")
            path.write_text("测试", encoding="utf-8")
            result[key] = str(path)
        wav = root / "recording.wav"
        wav.touch()
        with self.assertRaises(ValueError): self.store.update(task["id"], token, {"status": "completed", "result": result})
        wav.unlink()
        self.store.update(task["id"], token, {"status": "completed", "result": result})
        retried = self.store.retry(task["id"])
        self.assertNotEqual(retried["payload"]["output_dir"], str(root))
        self.assertTrue(any("上次结果" in e["message"] for e in retried["events"]))


class TaskPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_defaults_and_queue_actions(self):
        from voice_to_text.task_page import TaskEditDialog, TaskQueuePage
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            dialog = TaskEditDialog()
            self.assertEqual(dialog.data()["config"]["chunk_seconds"], 10)
            self.assertFalse(dialog.data()["keep_audio"])
            task = store.add({"url": "https://example.com/video"})
            page = TaskQueuePage(store=store)
            page.table.selectRow(0)
            self.assertEqual(page.selected_id(), task["id"])
            self.assertTrue(page.buttons["edit"].isEnabled())
            page.act("stop")
            self.assertEqual(store.get(task["id"])["status"], "cancelled")
            page.act("retry")
            self.assertEqual(store.get(task["id"])["status"], "queued")
            page.timer.stop()
            page.close()
            dialog.close()

    def test_redesigned_list_search_and_collapsed_logs(self):
        from voice_to_text.task_page import TaskQueuePage, ElidedLabel
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task = store.add({"url": "https://www.douyin.com/video/123"})
            page = TaskQueuePage(store=store)
            page.resize(980, 700)
            page.show()
            self.app.processEvents()
            self.assertEqual(page.table.columnCount(), 2)
            self.assertEqual(page.table.item(0, 0).text(), "")
            self.assertEqual(page.table.cellWidget(0, 0).findChild(ElidedLabel).full_text, "抖音视频")
            self.assertEqual(page.detail_title.text(), "抖音视频")
            self.assertFalse(page.log_toggle.isChecked())
            self.assertTrue(page.details.isHidden())
            self.assertIn("10 秒切片", page.metadata.text())
            self.assertFalse(page.buttons["folder"].isEnabled())
            page.search.setText("不存在")
            self.assertEqual(page.table.rowCount(), 0)
            self.assertEqual(page.detail_title.text(), "选择一个任务")
            page.search.clear()
            self.assertEqual(page.selected_id(), task["id"])
            page.log_toggle.setChecked(True)
            self.assertFalse(page.details.isHidden())
            title = page.table.cellWidget(0, 0).findChild(ElidedLabel)
            self.assertTrue(title.text())
            page.timer.stop()
            page.close()

    def test_completed_markdown_and_status_actions(self):
        from voice_to_text.task_page import TaskQueuePage
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task = store.add({"url": "https://example.com/video", "output_dir": directory})
            claimed = store.claim()
            root = Path(claimed["payload"]["output_dir"])
            root.mkdir()
            result = {"integrity": {"complete": True}}
            for key in ("raw_transcript_path", "timestamped_transcript_path", "optimized_transcript_path", "summary_path"):
                path = root / (key + ".md")
                path.write_text("# 真实结果\n\n测试正文", encoding="utf-8")
                result[key] = str(path)
            store.update(task["id"], claimed["lease_token"], {"status": "completed", "result": result})
            page = TaskQueuePage(store=store)
            self.assertIn("真实结果", page.result_views["summary_path"].toPlainText())
            self.assertFalse(hasattr(page, "phase_labels"))
            self.assertTrue(page.buttons["retry"].isEnabled())
            self.assertTrue(page.buttons["edit"].isHidden())
            self.assertTrue(page.buttons["stop"].isHidden())
            self.assertTrue(page.buttons["folder"].isEnabled())
            page.search.setText("没有结果")
            page.search.clear()
            self.assertIn("真实结果", page.result_views["summary_path"].toPlainText())
            page.timer.stop()
            page.close()

    def test_pager_navigation_filters_and_live_page_shrink(self):
        from voice_to_text.task_page import TaskQueuePage
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            tasks = [store.add({"url": f"https://example.com/{i}", "title": f"任务 {i:02}"}) for i in range(23)]
            page = TaskQueuePage(store=store)
            self.assertEqual(page.table.rowCount(), 10)
            self.assertFalse(page.previous_page.isEnabled())
            page.change_page(1)
            self.assertEqual(page.selected_id(), tasks[10]["id"])
            page.change_page(1)
            self.assertEqual(page.table.rowCount(), 3)
            self.assertFalse(page.next_page.isEnabled())
            page.search.setText("任务 22")
            self.assertEqual(page.page_number, 1)
            self.assertEqual(page.table.rowCount(), 1)
            page.search.clear()
            page.page_size.setCurrentIndex(1)
            self.assertEqual(page.table.rowCount(), 20)
            page.change_page(1)
            page.filter.setCurrentIndex(page.filter.findData("queued"))
            self.assertEqual(page.page_number, 1)
            page.change_page(1)
            for task in tasks[3:]: store.stop(task["id"])
            page.refresh()
            self.assertEqual(page.page_number, 1)
            self.assertEqual(page.table.rowCount(), 3)
            self.assertIn("共 3 条", page.page_summary.text())
            page.timer.stop()
            page.close()
