import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_to_text.cuda_runtime import candidate_cuda_dll_dirs
from voice_to_text.task_store import default_task_output_dir


class DesktopPackagingTests(unittest.TestCase):
    def test_frozen_cuda_directories_include_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            cuda = Path(folder) / "nvidia" / "cublas" / "bin"
            cuda.mkdir(parents=True)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", folder, create=True):
                self.assertIn(cuda, candidate_cuda_dll_dirs())

    def test_frozen_task_output_is_in_documents_not_bundle(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(default_task_output_dir(), Path.home() / "Documents" / "VoiceToText" / "transcripts" / "tasks")
