import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from page_controller import find_sources, marker_args_for, process, recover_saved_markdown


class PageControllerTest(unittest.TestCase):
    def test_folder_discovery_and_gui_presets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "B.PDF").touch()
            (root / "a.pdf").touch()
            (root / "ignore.txt").touch()
            self.assertEqual(
                [path.name for path in find_sources(root)], ["a.pdf", "B.PDF"]
            )
        self.assertEqual(
            marker_args_for("Marker 2", False, "--foo bar"),
            ["--mode", "balanced", "--foo", "bar"],
        )
        self.assertIn(
            "--recognition_batch_size",
            marker_args_for("Marker 1.10", True, ""),
        )

    def test_processes_each_page_once_and_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as output:
                writer.write(output)

            calls = root / "calls.txt"
            fake_marker = root / "fake_marker.py"
            fake_marker.write_text(
                """import argparse
from pathlib import Path
from pypdf import PdfReader

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('--output_dir', type=Path, required=True)
parser.add_argument('--output_format')
args = parser.parse_args()
number = args.source.stem.split('-')[-1]
args.output_dir.mkdir(parents=True, exist_ok=True)
(args.output_dir / 'page.md').write_text(f'Page {number}\\n', encoding='utf-8')
with Path(r'CALLS_FILE').open('a', encoding='utf-8') as log:
    log.write(number + '\\n')
assert len(PdfReader(str(args.source)).pages) == 1
""".replace("CALLS_FILE", str(calls).replace("\\", "\\\\")),
                encoding="utf-8",
            )

            marker = str(root / "fake_marker.cmd") if sys.platform == "win32" else str(fake_marker)
            marker_args = []
            if sys.platform == "win32":
                Path(marker).write_text(
                    f'@"{sys.executable}" "{fake_marker}" %*\n', encoding="utf-8"
                )
            else:
                fake_marker.write_text(
                    f"#!{sys.executable}\n" + fake_marker.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                fake_marker.chmod(0o755)

            output_dir = root / "output"
            result = process(
                source, output_dir, marker, marker_args, keep_intermediate_files=True
            )
            final = result.final
            self.assertEqual(len(PdfReader(str(source)).pages), 2)
            self.assertIn("Page 0001", final.read_text(encoding="utf-8"))
            self.assertIn("Page 0002", final.read_text(encoding="utf-8"))
            process(
                source, output_dir, marker, marker_args, keep_intermediate_files=True
            )
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["0001", "0002"])

    def test_recovers_output_saved_before_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            page_dir = Path(temporary) / "page-0001"
            markdown = page_dir / "runs" / "1" / "page.md"
            markdown.parent.mkdir(parents=True)
            markdown.write_text("Recovered\n", encoding="utf-8")

            self.assertEqual(recover_saved_markdown(page_dir), markdown)
            self.assertTrue((page_dir / "complete.txt").is_file())

    def test_partial_output_and_managed_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as output:
                writer.write(output)

            fake_marker = root / "fake_marker.py"
            fake_marker.write_text(
                """import argparse
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('--output_dir', type=Path, required=True)
parser.add_argument('--output_format')
args = parser.parse_args()
number = args.source.stem.split('-')[-1]
if number == '0002':
    sys.exit(2)
args.output_dir.mkdir(parents=True, exist_ok=True)
(args.output_dir / 'figure.png').write_bytes(b'image')
(args.output_dir / 'page.md').write_text('![figure](figure.png)\\n', encoding='utf-8')
""",
                encoding="utf-8",
            )
            marker = str(root / "fake_marker.cmd") if sys.platform == "win32" else str(fake_marker)
            if sys.platform == "win32":
                Path(marker).write_text(
                    f'@"{sys.executable}" "{fake_marker}" %*\n', encoding="utf-8"
                )
            else:
                fake_marker.write_text(
                    f"#!{sys.executable}\n" + fake_marker.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                fake_marker.chmod(0o755)

            output_dir = root / "output"
            result = process(source, output_dir, marker, [])

            self.assertEqual(result.failed_pages, (2,))
            text = result.final.read_text(encoding="utf-8")
            self.assertIn("![figure](images/sample_001.png)", text)
            self.assertIn("Page 2 failed and remains pending", text)
            self.assertTrue((output_dir / "images" / "sample_001.png").is_file())
            self.assertTrue((output_dir / "_marker_pages").is_dir())

    def test_success_cleans_intermediate_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as output:
                writer.write(output)

            fake_marker = root / "fake_marker.py"
            fake_marker.write_text(
                """import argparse
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('--output_dir', type=Path, required=True)
parser.add_argument('--output_format')
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
(args.output_dir / 'page.md').write_text('Done\\n', encoding='utf-8')
""",
                encoding="utf-8",
            )
            marker = str(root / "fake_marker.cmd") if sys.platform == "win32" else str(fake_marker)
            if sys.platform == "win32":
                Path(marker).write_text(
                    f'@"{sys.executable}" "{fake_marker}" %*\n', encoding="utf-8"
                )
            else:
                fake_marker.write_text(
                    f"#!{sys.executable}\n" + fake_marker.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                fake_marker.chmod(0o755)

            output_dir = root / "output"
            result = process(source, output_dir, marker, [])

            self.assertFalse(result.failed_pages)
            self.assertTrue(result.final.is_file())
            self.assertFalse((output_dir / "_marker_pages").exists())


if __name__ == "__main__":
    unittest.main()

