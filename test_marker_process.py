import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, run
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter

import marker_process as marker


class MarkerProcessTests(unittest.TestCase):
    def setUp(self):
        self._resume_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._resume_directory.cleanup)
        self._resume_patch = patch.object(
            marker,
            "LAST_RUN_FILE",
            Path(self._resume_directory.name) / "last-run.json",
        )
        self._resume_patch.start()
        self.addCleanup(self._resume_patch.stop)
        self._active_patch = patch.object(
            marker,
            "ACTIVE_RUNS_FILE",
            Path(self._resume_directory.name) / "active-runs.json",
        )
        self._active_patch.start()
        self.addCleanup(self._active_patch.stop)

    def test_resume_restores_conversion_settings_without_restarting(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.touch()
            output = root / "out"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(marker, "check_runtime", return_value="Test GPU"),
                patch.object(
                    marker, "process_page_by_page", side_effect=[[1], []]
                ) as process,
            ):
                first = marker.main([
                    str(source), "--output", str(output), "--engine", "marker1",
                    "--refine", "--keep-work",
                    "--marker1-recognition-batch-size", "4",
                    "--pages-per-process", "3",
                ])
                saved = marker.load_active_resume_arguments()
                second = marker.main(["--resume"])

            self.assertEqual((first, second), (1, 0))
            self.assertIn("--refine", saved)
            self.assertIn("--keep-work", saved)
            self.assertNotIn("--restart", saved)
            self.assertEqual(
                saved[saved.index("--marker1-recognition-batch-size") + 1], "4"
            )
            self.assertEqual(saved[saved.index("--pages-per-process") + 1], "3")
            self.assertEqual(process.call_args.args[5], 4)
            self.assertEqual(process.call_args.kwargs["pages_per_process"], 3)
            self.assertTrue(process.call_args.kwargs["llm_refinement"])
            self.assertTrue(process.call_args.kwargs["create_metadata"])

    def test_resume_requires_a_saved_conversion(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            marker.main(["--resume"])
        self.assertEqual(raised.exception.code, 2)

    def test_tutorial_runs_the_local_front_end(self):
        with patch.object(marker, "run_tutorial", return_value=7) as tutorial:
            result = marker.main(["--tutorial"])
        self.assertEqual(result, 7)
        tutorial.assert_called_once_with()

    def test_redirected_tutorial_prints_an_offline_quick_start(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = marker.run_tutorial()
        self.assertEqual(result, 0)
        self.assertIn("Quick start", stdout.getvalue())
        self.assertIn("marker --resume", stdout.getvalue())

    def test_doctor_reports_missing_runtime_files_without_loading_models(self):
        missing = Path(self._resume_directory.name) / "missing.exe"
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            patch.object(
                marker, "required_runtime_files",
                return_value=[("Test component", missing)],
            ),
            patch.object(marker, "check_runtime") as live_check,
        ):
            result = marker.main(["--doctor", "--engine", "marker2"])
        self.assertEqual(result, 2)
        self.assertIn("MISSING  Test component", stdout.getvalue())
        self.assertIn("marker --setup", stdout.getvalue())
        live_check.assert_not_called()

    def test_doctor_accepts_a_complete_path_configuration(self):
        present = Path(self._resume_directory.name) / "component.exe"
        present.touch()
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            patch.object(
                marker, "required_runtime_files",
                return_value=[("Test component", present)],
            ),
        ):
            result = marker.main(["--doctor", "--engine", "marker1"])
        self.assertEqual(result, 0)
        self.assertIn("OK  Test component", stdout.getvalue())
        self.assertIn("marker --check --engine marker1", stdout.getvalue())

    def test_user_configuration_is_saved_atomically(self):
        config_file = Path(self._resume_directory.name) / "config.json"
        previous = dict(marker.USER_CONFIG)

        def restore():
            marker.USER_CONFIG.clear()
            marker.USER_CONFIG.update(previous)

        self.addCleanup(restore)
        with patch.object(marker, "APP_CONFIG_FILE", config_file):
            marker.save_user_config({"QPDF_EXE": "C:/tools/qpdf.exe"})
        self.assertEqual(
            json.loads(config_file.read_text(encoding="utf-8")),
            {"QPDF_EXE": "C:/tools/qpdf.exe"},
        )
        self.assertEqual(marker.USER_CONFIG["QPDF_EXE"], "C:/tools/qpdf.exe")

    def test_page_selection_supports_ranges_open_ends_and_deduplication(self):
        self.assertEqual(
            marker.parse_page_selection("1-3,3,5,8-", 10),
            [1, 2, 3, 5, 8, 9, 10],
        )
        self.assertEqual(marker.parse_page_selection("-2", 5), [1, 2])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            marker.parse_page_selection("6", 5)

    def test_page_groups_preserve_selected_order(self):
        self.assertEqual(
            marker.page_groups([1, 3, 4, 8, 9], 2),
            [[1, 3], [4, 8], [9]],
        )

    def test_create_page_group_combines_pages_with_qpdf(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            split_pages = [root / "page-1.pdf", root / "page-2.pdf"]

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"combined")
                return CompletedProcess(command, 0)

            with (
                patch.object(marker.subprocess, "run", side_effect=fake_run) as run,
                patch.object(marker, "qpdf_page_count", return_value=2),
            ):
                grouped = marker.create_page_group(
                    split_pages, [1, 2], root / "groups"
                )

            self.assertTrue(grouped.is_file())
            command = run.call_args.args[0]
            self.assertEqual(command[:3], [str(marker.QPDF), "--empty", "--pages"])
            self.assertEqual(command[3:7], [
                str(split_pages[0]), "1", str(split_pages[1]), "1",
            ])

    def test_grouped_processing_reports_every_page_in_a_failed_group(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            output = root / "out"
            output.mkdir()
            split_pages = [root / f"page-{number}.pdf" for number in range(1, 6)]
            converted = root / "converted.md"
            converted.write_text("converted\n", encoding="utf-8")
            group_inputs = [root / "group-1.pdf", root / "group-2.pdf", root / "group-3.pdf"]
            progress_events = []

            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(marker, "prepare_input_pdf", return_value=source),
                patch.object(marker, "qpdf_page_count", return_value=5),
                patch.object(marker, "split_pdf", return_value=split_pages),
                patch.object(marker, "create_page_group", side_effect=group_inputs),
                patch.object(
                    marker, "convert_input", side_effect=[converted, None, converted]
                ) as convert,
            ):
                failed = marker.process_page_by_page(
                    source, output, "1.10", 30, True, 1, True,
                    create_metadata=False, pages_per_process=2,
                    progress_callback=progress_events.append,
                )

            self.assertEqual(failed, [3, 4])
            self.assertEqual(convert.call_count, 3)
            self.assertEqual(
                [event["event"] for event in progress_events],
                ["started", "finished"] * 3,
            )
            self.assertEqual(
                [
                    event["status"]
                    for event in progress_events
                    if event["event"] == "finished"
                ],
                ["success", "failed", "success"],
            )
            self.assertEqual(
                [event["label"] for event in progress_events[::2]],
                ["Pages 1–2", "Pages 3–4", "Page 5"],
            )
            self.assertTrue(all(
                event["duration_seconds"] >= 0
                for event in progress_events
                if event["event"] == "finished"
            ))
            self.assertIn(
                "Pages 3–4 failed", (output / "source.md").read_text("utf-8")
            )

    def test_resumed_pages_are_excluded_from_eta_work_units(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            output = root / "out"
            output.mkdir()
            split_pages = [root / f"page-{number}.pdf" for number in range(1, 6)]
            converted = root / "converted.md"
            converted.write_text("converted\n", encoding="utf-8")
            progress_events = []
            stderr = io.StringIO()

            def checkpoint_for(page_dir, _require_metadata=True):
                return converted if page_dir.name in {
                    "page0001", "page0002", "page0003", "page0004"
                } else None

            marker.REPORTER.configure(progress="plain")
            with (
                contextlib.redirect_stderr(stderr),
                patch.object(marker, "prepare_input_pdf", return_value=source),
                patch.object(marker, "qpdf_page_count", return_value=5),
                patch.object(marker, "split_pdf", return_value=split_pages),
                patch.object(marker, "completed_markdown", side_effect=checkpoint_for),
                patch.object(marker, "convert_input", return_value=converted),
            ):
                failed = marker.process_page_by_page(
                    source, output, "1.10", 30, True, 1, True,
                    create_metadata=False,
                    progress_callback=progress_events.append,
                )

            started = [
                event for event in progress_events if event["event"] == "started"
            ]
            self.assertEqual(failed, [])
            self.assertEqual([event["resumed"] for event in started], [
                True, True, True, True, False,
            ])
            self.assertTrue(all(event["timed_total"] == 1 for event in started))
            self.assertIn("Completed: 5/5 pages (1-5)", stderr.getvalue())
            self.assertNotIn("[1/5", stderr.getvalue())

    def test_grouped_metadata_keeps_original_page_numbers(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            markdown = root / "group.md"
            markdown.write_text("group\n", encoding="utf-8")
            marker.metadata_for(markdown).write_text(json.dumps({
                "structured_document": {
                    "pages": [
                        {"page_id": 0, "blocks": [{"id": "/page/0/Text/0"}]},
                        {"page_id": 1, "blocks": [{"id": "/page/1/Text/0"}]},
                    ]
                }
            }), encoding="utf-8")

            metadata = marker.build_document_metadata(
                root / "source.pdf", "2.0", "page", [markdown], {},
                grouped_page_numbers=[[2, 5]],
            )

            self.assertEqual([page["page_id"] for page in metadata["pages"]], [1, 4])
            self.assertEqual(
                [page["blocks"][0]["id"] for page in metadata["pages"]],
                ["/page/1/Text/0", "/page/4/Text/0"],
            )

    def test_qpdf_page_count_can_check_structure_and_accept_warnings(self):
        result = CompletedProcess(
            args=[],
            returncode=3,
            stdout="checking source.pdf\n2\n",
            stderr="qpdf: operation succeeded with warnings\n",
        )
        with patch.object(marker.subprocess, "run", return_value=result) as run:
            self.assertEqual(
                marker.qpdf_page_count(Path("source.pdf"), check_structure=True),
                2,
            )
        self.assertEqual(
            run.call_args.args[0],
            [str(marker.QPDF), "--check", "--show-npages", "source.pdf"],
        )

    def test_qpdf_page_count_rejects_qpdf_errors(self):
        result = CompletedProcess(
            args=[], returncode=2, stdout="", stderr="damaged PDF\n"
        )
        with (
            patch.object(marker.subprocess, "run", return_value=result),
            self.assertRaisesRegex(RuntimeError, "damaged PDF"),
        ):
            marker.qpdf_page_count(Path("source.pdf"))

    def test_valid_pdf_uses_qpdf_structural_check(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.pdf"
            source.write_bytes(b"not empty")
            with patch.object(marker, "qpdf_page_count", return_value=1) as count:
                self.assertTrue(marker.valid_pdf(source))
            count.assert_called_once_with(source, check_structure=True)

    def test_selected_pages_keep_original_numbers_in_output(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=10, height=10)
            with source.open("wb") as output_pdf:
                writer.write(output_pdf)
            output = root / "out"
            output.mkdir()
            split_pages = [root / f"page-{number}.pdf" for number in range(1, 4)]
            markdown = root / "converted.md"
            markdown.write_text("selected page\n", encoding="utf-8")

            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(marker, "prepare_input_pdf", return_value=source),
                patch.object(marker, "qpdf_page_count", return_value=3),
                patch.object(marker, "split_pdf", return_value=split_pages),
                patch.object(marker, "convert_input", side_effect=[markdown, None]) as convert,
            ):
                failed = marker.process_page_by_page(
                    source, output, "1.10", 30, True, 1, True,
                    create_metadata=False, page_selection="1,3",
                )

            self.assertEqual(failed, [3])
            self.assertEqual(
                [call.args[0] for call in convert.call_args_list],
                [split_pages[0], split_pages[2]],
            )
            self.assertIn("Page 3 failed", (output / "source.md").read_text("utf-8"))

    def test_multiple_operands_and_jsonl_results(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first.pdf"
            second = root / "second.pdf"
            first.touch()
            second.touch()
            output = root / "out"
            stdout = io.StringIO()

            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(marker, "check_runtime", return_value="Test GPU"),
                patch.object(marker, "process_page_by_page", return_value=[]) as process,
            ):
                result = marker.main([
                    str(first), "--output", str(output), str(second), "--jsonl",
                    "--pages", "1-2",
                ])

            records = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(result, 0)
            self.assertEqual([record["type"] for record in records], [
                "check", "result", "result", "summary"
            ])
            self.assertEqual([record["status"] for record in records[1:3]], [
                "success", "success"
            ])
            self.assertFalse(records[0]["cached"])
            self.assertEqual(records[-1]["succeeded"], 2)
            self.assertTrue(all(
                call.kwargs["page_selection"] == "1-2"
                for call in process.call_args_list
            ))

    def test_cached_runtime_components_are_reused_and_reported(self):
        gpu = {
            "name": "Test GPU", "memory_total_mib": 4096,
            "memory_free_mib": 3072,
        }
        with (
            patch.object(marker, "check_qpdf_runtime", return_value=("qpdf ready", None)) as qpdf,
            patch.object(marker, "check_nvidia_gpu", return_value=("Test GPU", gpu)) as nvidia,
            patch.object(marker, "check_runtime_file", return_value=("file found", None)) as file_check,
            patch.object(marker, "check_llama_cuda", return_value=("CUDA ready", None)) as llama,
            patch.object(
                marker, "probe_surya_gpu_fit",
                return_value=(
                    "30 GPU layers loaded safely",
                    {"requested": 30, "resolved_gpu_layers": 30, "model_layers": 34},
                ),
            ) as fit,
        ):
            first = marker.check_runtime("2.0", "page", 30, False)
            with patch.dict(
                marker.os.environ,
                {marker.RUNTIME_CHECK_CACHE_ENV: json.dumps(first["cache"])},
            ):
                second = marker.check_runtime("2.0", "whole", 30, False)

        self.assertEqual(first["status"], "ready")
        self.assertFalse(first["cached"])
        self.assertEqual(second["status"], "ready")
        self.assertTrue(second["cached"])
        self.assertEqual(second["resolved_gpu_layers"], 30)
        self.assertTrue(all(item["cached"] for item in second["components"]))
        self.assertEqual(qpdf.call_count, 1)
        self.assertEqual(nvidia.call_count, 1)
        self.assertEqual(file_check.call_count, 4)
        self.assertEqual(llama.call_count, 1)
        self.assertEqual(fit.call_count, 1)

    def test_gpu_layer_change_rechecks_only_the_model_fit(self):
        gpu = {
            "name": "Test GPU", "memory_total_mib": 4096,
            "memory_free_mib": 3072,
        }
        with (
            patch.object(marker, "check_qpdf_runtime", return_value=("qpdf ready", None)) as qpdf,
            patch.object(marker, "check_nvidia_gpu", return_value=("Test GPU", gpu)) as nvidia,
            patch.object(marker, "check_runtime_file", return_value=("file found", None)) as file_check,
            patch.object(marker, "check_llama_cuda", return_value=("CUDA ready", None)) as llama,
            patch.object(marker, "probe_surya_gpu_fit") as fit,
        ):
            fit.side_effect = [
                ("30 safe", {"resolved_gpu_layers": 30}),
                ("20 safe", {"resolved_gpu_layers": 20}),
            ]
            first = marker.check_runtime("2.0", "page", 30, False)
            with patch.dict(
                marker.os.environ,
                {marker.RUNTIME_CHECK_CACHE_ENV: json.dumps(first["cache"])},
            ):
                second = marker.check_runtime("2.0", "page", 20, False)

        self.assertEqual(second["status"], "ready")
        self.assertEqual(second["resolved_gpu_layers"], 20)
        self.assertEqual(qpdf.call_count, 1)
        self.assertEqual(nvidia.call_count, 1)
        self.assertEqual(file_check.call_count, 4)
        self.assertEqual(llama.call_count, 1)
        self.assertEqual(fit.call_count, 2)
        fresh = [item["key"] for item in second["components"] if not item["cached"]]
        self.assertEqual(fresh, ["surya_gpu_fit"])

    def test_engine_switch_reuses_shared_checks_only(self):
        gpu = {
            "name": "Test GPU", "memory_total_mib": 4096,
            "memory_free_mib": 3072,
        }
        with (
            patch.object(marker, "check_qpdf_runtime", return_value=("qpdf ready", None)) as qpdf,
            patch.object(marker, "check_nvidia_gpu", return_value=("Test GPU", gpu)) as nvidia,
            patch.object(marker, "check_runtime_file", return_value=("file found", None)),
            patch.object(marker, "check_llama_cuda", return_value=("CUDA ready", None)),
            patch.object(marker, "check_marker1_cuda", return_value=("CUDA ready", None)),
            patch.object(
                marker, "probe_surya_gpu_fit",
                return_value=("Auto chose 25", {"resolved_gpu_layers": 25}),
            ),
        ):
            marker2 = marker.check_runtime("2.0", "page", "auto", False)
            with patch.dict(
                marker.os.environ,
                {marker.RUNTIME_CHECK_CACHE_ENV: json.dumps(marker2["cache"])},
            ):
                marker1 = marker.check_runtime("1.10", "page", "auto", False)

        self.assertEqual(qpdf.call_count, 1)
        self.assertEqual(nvidia.call_count, 1)
        reused = [item["key"] for item in marker1["components"] if item["cached"]]
        fresh = [item["key"] for item in marker1["components"] if not item["cached"]]
        self.assertEqual(reused, ["qpdf", "nvidia_gpu"])
        self.assertEqual(fresh, ["marker_1.10", "marker1_python", "marker1_cuda"])

    def test_gpu_layers_accept_auto_all_or_positive_numbers(self):
        self.assertEqual(marker.normalize_gpu_layers("Auto"), "auto")
        self.assertEqual(marker.normalize_gpu_layers("ALL"), "all")
        self.assertEqual(marker.normalize_gpu_layers("20"), 20)
        for invalid in ("", "0", "-1", "many"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                marker.normalize_gpu_layers(invalid)

    def test_unsafe_manual_gpu_layers_fail_at_the_model_load_test(self):
        gpu = {
            "name": "Test GPU", "memory_total_mib": 4096,
            "memory_free_mib": 1024,
        }
        with (
            patch.object(marker, "check_qpdf_runtime", return_value=("qpdf ready", None)),
            patch.object(marker, "check_nvidia_gpu", return_value=("Test GPU", gpu)),
            patch.object(marker, "check_runtime_file", return_value=("file found", None)),
            patch.object(marker, "check_llama_cuda", return_value=("CUDA ready", None)),
            patch.object(
                marker, "probe_surya_gpu_fit",
                side_effect=RuntimeError(
                    "GPU layers 25 could not safely load the Surya model. "
                    "Choose Auto or a lower number."
                ),
            ),
        ):
            result = marker.check_runtime("2.0", "page", 25, False)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["components"][-1]["key"], "surya_gpu_fit")
        self.assertEqual(result["components"][-1]["status"], "error")
        self.assertIn("Choose Auto or a lower number", result["error"])

    def test_pages_per_process_requires_page_mode(self):
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            marker.main([
                "source.pdf", "--mode", "whole", "--pages-per-process", "2"
            ])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires --mode page", stderr.getvalue())

    def test_multiple_operands_require_explicit_output(self):
        with tempfile.TemporaryDirectory() as raw:
            first = Path(raw) / "first.pdf"
            second = Path(raw) / "second.pdf"
            first.touch()
            second.touch()
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                marker.main([str(first), str(second), "--dry-run"])
            self.assertEqual(raised.exception.code, 2)

    def test_quiet_suppresses_routine_diagnostics(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.pdf"
            source.touch()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
                patch.object(marker, "check_runtime", return_value="Test GPU"),
                patch.object(marker, "process_page_by_page", return_value=[]),
            ):
                result = marker.main([str(source), "--quiet"])
            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")

    def test_auto_progress_adapts_to_redirected_and_tty_stderr(self):
        reporter = marker.Reporter()
        reporter.configure(progress="auto")
        redirected = io.StringIO()
        with contextlib.redirect_stderr(redirected):
            reporter.progress(1, 2, "Page 1", marker.time.monotonic())
        self.assertEqual(redirected.getvalue(), "")

        reporter.configure(progress="auto", verbose=True)
        with contextlib.redirect_stderr(redirected):
            reporter.progress(1, 2, "Page 1", marker.time.monotonic())
        self.assertNotIn("\x1b", redirected.getvalue())
        self.assertTrue(redirected.getvalue().endswith("\n"))

        class TtyBuffer(io.StringIO):
            def isatty(self):
                return True

        tty = TtyBuffer()
        with contextlib.redirect_stderr(tty):
            reporter.progress(1, 2, "Page 1", marker.time.monotonic())
            reporter.finish_progress()
        self.assertIn("\r[1/2", tty.getvalue())

    def test_gui_progress_duration_and_result_formatting(self):
        self.assertEqual(marker.format_clock_duration(82.8), "00:01:22")
        self.assertEqual(marker.format_work_duration(38.6), "38.6s")
        self.assertEqual(marker.format_work_duration(126.7), "2m 06.7s")
        self.assertEqual(
            marker.format_page_progress_result("Page 1", True, 38.6),
            "Page 1 completed — 38.6s ✅",
        )
        self.assertEqual(
            marker.format_page_progress_result("Pages 5–8", False, 126.7),
            "Pages 5–8 failed — 2m 06.7s ❌",
        )
        self.assertFalse(
            marker.should_show_page_progress_result(True, "standard")
        )
        self.assertTrue(
            marker.should_show_page_progress_result(False, "standard")
        )
        self.assertTrue(
            marker.should_show_page_progress_result(True, "verbose")
        )
        self.assertEqual(marker.format_page_ranges([4, 2, 1, 4]), "1-2, 4")
        self.assertEqual(
            marker.format_page_summary("Completed", [1, 2, 4], 4),
            "Completed: 3/4 pages (1-2, 4)",
        )
        self.assertEqual(
            marker.format_page_summary("Failed", [3], 4),
            "Failed: 1/4 page (3)",
        )

    def test_eta_uses_only_pages_that_were_actually_processed(self):
        self.assertEqual(marker.estimate_remaining_seconds(4.0, 1, 2), 4.0)
        self.assertEqual(marker.estimate_remaining_seconds(4.0, 1, 1), 0.0)
        self.assertIsNone(marker.estimate_remaining_seconds(0.0, 0, 1))
        self.assertEqual(marker.estimate_remaining_seconds(0.0, 0, 0), 0.0)

    def test_runtime_check_summary_is_compact_and_keeps_exact_failure(self):
        summaries = marker.format_runtime_check_summaries({
            "status": "error",
            "error": "Surya needs 512 MiB more VRAM",
            "components": [
                {"label": "qpdf", "status": "ready", "detail": "qpdf ready"},
                {
                    "label": "Graphics card",
                    "status": "ready",
                    "detail": "Test GPU",
                },
                {
                    "label": "Surya GPU fit",
                    "status": "error",
                    "detail": "Surya needs 512 MiB more VRAM",
                },
            ],
        })
        self.assertEqual(summaries, [
            (
                "ready",
                "Ready: 2/3",
            ),
            (
                "failed",
                "Fail: 1/3 check "
                "(Surya GPU fit — Surya needs 512 MiB more VRAM)",
            ),
        ])

    def test_check_command_uses_compact_runtime_summary(self):
        check = {
            "status": "error",
            "error": "CUDA device was not found",
            "components": [
                {"label": "qpdf", "status": "ready", "detail": "qpdf ready"},
                {
                    "label": "llama.cpp CUDA",
                    "status": "error",
                    "detail": "CUDA device was not found",
                },
            ],
            "cache": {"components": {}},
            "cached": False,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(marker, "check_runtime", return_value=check),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = marker.main(["--check"])

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue().splitlines(), [
            "Ready: 1/2",
            "Fail: 1/2 check (llama.cpp CUDA — CUDA device was not found)",
        ])
        self.assertEqual(stderr.getvalue(), "")

    def test_gui_conversion_console_starts_minimized(self):
        options = marker.gui_child_process_options("conversion")
        self.assertTrue(
            options["creationflags"] & marker.subprocess.CREATE_NEW_PROCESS_GROUP
        )
        self.assertEqual(
            options["startupinfo"].wShowWindow,
            marker.WINDOWS_SHOW_MINIMIZED,
        )
        self.assertTrue(
            options["startupinfo"].dwFlags
            & marker.subprocess.STARTF_USESHOWWINDOW
        )
        self.assertNotIn("startupinfo", marker.gui_child_process_options("check"))

    def test_gui_console_notice_explains_how_to_stop_marker(self):
        self.assertIn(
            "Close this terminal window to stop Marker",
            marker.GUI_CONSOLE_NOTICE,
        )
        self.assertIn("checkpoints will be preserved", marker.GUI_CONSOLE_NOTICE)

    def test_diagnostic_log_records_details_suppressed_by_quiet(self):
        with tempfile.TemporaryDirectory() as raw:
            log = Path(raw) / "marker.log"
            reporter = marker.Reporter()
            reporter.configure(quiet=True, log_file=log)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                reporter.message("routine detail")
                reporter.message("hidden command", "debug")
            self.assertEqual(stderr.getvalue(), "")
            logged = log.read_text(encoding="utf-8")
            self.assertIn("[INFO] routine detail", logged)
            self.assertIn("[DEBUG] hidden command", logged)

    def test_run_quietly_stops_child_tree_on_interrupt(self):
        process = MagicMock()
        process.wait.side_effect = KeyboardInterrupt
        stderr = io.StringIO()

        with (
            contextlib.redirect_stderr(stderr),
            patch.object(marker.subprocess, "Popen", return_value=process),
            patch.object(marker, "stop_process_tree") as stop_process_tree,
            self.assertRaises(KeyboardInterrupt),
        ):
            marker.run_quietly(["marker"], {}, 10)

        stop_process_tree.assert_called_once_with(process, graceful=True)
        self.assertIn("press Ctrl+C again", stderr.getvalue())

    def test_checkpoint_manifest_requires_restart_after_settings_change(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            output = root / "out"
            output.mkdir()
            manifest = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 1, False, True
            )
            marker.prepare_document_state(source, output, manifest, restart=False)
            marker.prepare_document_state(source, output, manifest, restart=False)

            changed = json.loads(json.dumps(manifest))
            changed["settings"]["processing_mode"] = "whole"
            with self.assertRaisesRegex(RuntimeError, "--resume-settings current"):
                marker.prepare_document_state(source, output, changed, restart=False)

            final = output / "source.md"
            refined = output / "source.refined.md"
            final.touch()
            refined.touch()
            source_image = output / "images" / "source_001.png"
            source_image.parent.mkdir()
            source_image.touch()
            other_image = output / "images" / "other_001.png"
            other_image.touch()
            unrelated = output / "_marker_work" / "other" / "keep.txt"
            unrelated.parent.mkdir()
            unrelated.touch()
            with contextlib.redirect_stderr(io.StringIO()):
                marker.prepare_document_state(source, output, changed, restart=True)

            self.assertFalse(final.exists())
            self.assertFalse(refined.exists())
            self.assertFalse(source_image.exists())
            self.assertTrue(other_image.exists())
            self.assertTrue(unrelated.exists())
            saved = json.loads(
                (marker.document_work_dir(source, output) / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved, changed)

    def test_work_folders_are_unique_per_source_and_legacy_is_reused(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "one" / "same.pdf"
            second = root / "two" / "same.pdf"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            output = root / "out"
            self.assertNotEqual(
                marker.document_work_dir(first, output),
                marker.document_work_dir(second, output),
            )

            legacy = output / "_marker_work" / "same"
            legacy.mkdir(parents=True)
            manifest = marker.build_run_manifest(
                first, "1.10", "page", 30, True, 1, False, True
            )
            (legacy / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            self.assertEqual(marker.document_work_dir(first, output), legacy)
            self.assertNotEqual(marker.document_work_dir(second, output), legacy)

    def test_current_tuning_reuses_checkpoints_but_engine_change_restarts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            output = root / "out"
            original = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 1, False, True
            )
            work = marker.prepare_document_state(source, output, original, False)
            checkpoint = work / "page_markdown" / "page0001" / "done.md"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text("done", encoding="utf-8")

            tuned = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 8, False, True
            )
            reused = marker.prepare_document_state(
                source, output, tuned, False, resume_settings="current"
            )
            self.assertEqual(reused, work)
            self.assertTrue(checkpoint.exists())

            incompatible = marker.build_run_manifest(
                source, "1.10", "whole", 30, True, 8, False, True
            )
            restarted = marker.prepare_document_state(
                source, output, incompatible, False, resume_settings="current"
            )
            self.assertFalse(checkpoint.exists())
            self.assertEqual(restarted, marker.document_work_dir(source, output))

    def test_source_content_change_cannot_resume_with_current_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"old")
            output = root / "out"
            manifest = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 1, False, True
            )
            marker.prepare_document_state(source, output, manifest, False)
            source.write_bytes(b"new")
            changed = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 4, False, True
            )
            with self.assertRaisesRegex(RuntimeError, "source file contents changed"):
                marker.prepare_document_state(
                    source, output, changed, False, resume_settings="current"
                )

    def test_active_resume_can_select_one_document_from_a_folder_run(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_folder = root / "source"
            source_folder.mkdir()
            first = source_folder / "first.pdf"
            second = source_folder / "second.pdf"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            output = root / "out"
            for source in (first, second):
                manifest = marker.build_run_manifest(
                    source, "1.10", "page", 30, True, 3, False, True
                )
                manifest["resume_arguments"] = [
                    str(source), "--output", str(output), "--engine", "marker1",
                    "--mode", "page", "--marker1-recognition-batch-size", "3",
                ]
                marker.prepare_document_state(source, output, manifest, False)

            selected = marker.load_active_resume_arguments([second])
            self.assertEqual(selected[0], str(second))
            self.assertNotIn(str(first), selected)
            combined = marker.load_active_resume_arguments([source_folder])
            self.assertEqual(set(combined[:2]), {str(first), str(second)})

    def test_legacy_last_run_registers_existing_work_without_moving_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.write_bytes(b"pdf")
            output = root / "out"
            legacy = output / "_marker_work" / "source"
            legacy.mkdir(parents=True)
            manifest = marker.build_run_manifest(
                source, "1.10", "page", 30, True, 1, False, True
            )
            (legacy / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            marker.save_last_run([
                str(source), "--output", str(output), "--engine", "marker1",
                "--mode", "page",
            ])

            records = marker.list_active_runs()
            self.assertEqual(len(records), 1)
            self.assertEqual(Path(records[0]["work_dir"]).resolve(), legacy.resolve())
            self.assertTrue(legacy.is_dir())

    def test_cli_without_arguments_prints_help(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            result = marker.main([])

        self.assertEqual(result, 0)
        self.assertIn("usage: marker", stdout.getvalue())
        self.assertIn("--dry-run", stdout.getvalue())

    def test_windows_launcher_explains_a_missing_local_environment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            launcher = root / "marker.cmd"
            source_launcher = Path(marker.__file__).parent / "marker.cmd"
            launcher.write_text(
                source_launcher.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "marker_process.py").write_text("", encoding="utf-8")
            environment = marker.os.environ.copy()
            environment.pop("MARKER_CONTROLLER_PYTHON", None)
            result = run(
                ["cmd.exe", "/d", "/c", str(launcher), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "could not find its Python environment", result.stdout
        )
        self.assertIn("py -3.12 -m venv .venv", result.stdout)
        self.assertIn("marker.cmd --setup", result.stdout)

    def test_gui_runs_the_native_front_end(self):
        with patch.object(marker, "choose_settings", return_value=7) as gui:
            result = marker.main(["--gui"])
        self.assertEqual(result, 7)
        gui.assert_called_once_with()

    def test_gui_falls_back_to_a_desktop_python_with_tk(self):
        with tempfile.TemporaryDirectory() as raw:
            desktop_python = Path(raw) / "python.exe"
            desktop_python.touch()
            with (
                patch.dict(
                    marker.os.environ,
                    {"MARKER_GUI_PYTHON": str(desktop_python)},
                    clear=False,
                ),
                patch.object(
                    marker.subprocess,
                    "run",
                    return_value=CompletedProcess([], 0),
                ) as probe,
                patch.object(marker.subprocess, "call", return_value=7) as launch,
            ):
                result = marker.launch_gui_with_desktop_python(RuntimeError("bad Tk"))

            self.assertEqual(result, 7)
            probe.assert_called_once()
            self.assertEqual(launch.call_args.args[0][-1], "--gui")
            self.assertEqual(
                launch.call_args.kwargs["env"]["MARKER_GUI_RELAUNCHED"], "1"
            )

    def test_cli_dry_run_uses_safe_default_output_without_hardware_check(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.pdf"
            source.touch()
            stdout = io.StringIO()

            with (
                contextlib.redirect_stdout(stdout),
                patch.object(marker, "check_runtime") as check_runtime,
            ):
                result = marker.main([str(source), "--dry-run"])

            self.assertEqual(result, 0)
            self.assertIn(
                str((source.parent / "marker_output").resolve()), stdout.getvalue()
            )
            check_runtime.assert_not_called()

    def test_cli_dry_run_with_restart_does_not_delete_outputs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.touch()
            output = root / "out"
            output.mkdir()
            existing = output / "source.md"
            existing.write_text("keep", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                result = marker.main(
                    [str(source), "--output", str(output), "--restart", "--dry-run"]
                )

            self.assertEqual(result, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_recursive_discovery_honors_include_and_exclude(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            nested = root / "nested"
            nested.mkdir()
            keep = nested / "keep.pdf"
            skip = nested / "draft.pdf"
            other = nested / "notes.txt"
            for path in (keep, skip, other):
                path.touch()

            supported, unsupported = marker.discover_inputs(
                root,
                "1.10",
                "page",
                False,
                recursive=True,
                include_patterns=["*.pdf"],
                exclude_patterns=["draft*"],
            )

            self.assertEqual(supported, [keep])
            self.assertEqual(set(unsupported), {skip, other})

    def test_cli_rejects_engine_specific_option_for_other_engine(self):
        stderr = io.StringIO()

        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            marker.main(["source.pdf", "--engine", "marker1", "--gpu-layers", "20"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("only applies to Marker 2", stderr.getvalue())

    def test_cli_interrupt_returns_130_and_preserves_resume_message(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.pdf"
            source.touch()
            stderr = io.StringIO()

            with (
                contextlib.redirect_stderr(stderr),
                patch.object(marker, "check_runtime", return_value="Test GPU"),
                patch.object(marker, "process_page_by_page", side_effect=KeyboardInterrupt),
            ):
                result = marker.main([str(source)])

            self.assertEqual(result, 130)
            self.assertIn("completed checkpoints were preserved", stderr.getvalue())

    def test_cli_success_writes_only_result_path_to_stdout(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.pdf"
            source.touch()
            output = Path(raw) / "out"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                patch.object(marker, "check_runtime", return_value="Test GPU"),
                patch.object(
                    marker, "process_page_by_page", return_value=[]
                ) as process,
            ):
                result = marker.main([str(source), "--output", str(output)])

            self.assertEqual(result, 0)
            self.assertEqual(
                stdout.getvalue().strip(), str((output / "source.md").resolve())
            )
            self.assertIn("Summary: 1 succeeded", stderr.getvalue())
            self.assertEqual(process.call_args.args[2], "2.0")

    def test_conversion_without_metadata_uses_standard_converter(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.touch()
            checkpoint = root / "checkpoint"
            commands = []

            def fake_run(command, _env, _timeout):
                commands.append(command)
                output = Path(command[command.index("--output_dir") + 1])
                markdown = output / "source.md"
                markdown.write_text("Text\n", encoding="utf-8")
                marker.metadata_for(markdown).write_text("{}", encoding="utf-8")

            with patch.object(marker, "run_quietly", side_effect=fake_run):
                result = marker.convert_input(
                    source, checkpoint, "1.10", 30, True, 1, False
                )

            self.assertNotIn("--converter_cls", commands[0])
            self.assertFalse(marker.metadata_for(result).exists())
            self.assertEqual(marker.completed_markdown(checkpoint, False), result)

    def test_marker1_photo_retries_image_only_output_as_text(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            source.touch()
            checkpoint = root / "checkpoint"
            commands = []

            def fake_run(command, _env, _timeout):
                commands.append(command)
                output = Path(command[command.index("--output_dir") + 1])
                output.mkdir(parents=True, exist_ok=True)
                result = "![](page.jpeg)" if len(commands) == 1 else "Text"
                (output / "source.md").write_text(result, encoding="utf-8")

            with patch.object(marker, "run_quietly", side_effect=fake_run):
                result = marker.convert_input(
                    source, checkpoint, "1.10", 30, True, 1, False, True
                )

            self.assertNotIn("--force_layout_block", commands[0])
            self.assertIn("--lowres_image_dpi", commands[0])
            self.assertEqual(commands[1][-2:], ["--force_layout_block", "Text"])
            self.assertEqual(result.read_text(encoding="utf-8"), "Text")

    def test_marker1_gpu_check_rejects_cuda_unavailable(self):
        results = [
            CompletedProcess([], 0, "NVIDIA GeForce RTX 2050\n", ""),
            CompletedProcess([], 1, "", ""),
        ]
        with (
            patch.object(marker.subprocess, "run", side_effect=results),
            self.assertRaisesRegex(RuntimeError, "cannot initialize CUDA"),
        ):
            marker.verify_gpu("1.10", 30, False)

    def test_marker2_gpu_check_requires_llama_cuda_device(self):
        results = [
            CompletedProcess([], 0, "NVIDIA GeForce RTX 2050\n", ""),
            CompletedProcess([], 0, "Available devices:\n  CPU\n", ""),
        ]
        with (
            patch.object(marker.subprocess, "run", side_effect=results),
            self.assertRaisesRegex(RuntimeError, "llama.cpp"),
        ):
            marker.verify_gpu("2.0", 30, False)

    def test_qwen_refinement_is_checkpointed_without_debug_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            markdown = root / "page.md"
            original = "OCR texxt\n\n![Figure](figure.png)\n"
            markdown.write_text(original, encoding="utf-8")
            checkpoint = root / "work" / "page0001.md"
            metadata = {
                "pages": [{
                    "page_id": 0,
                    "blocks": [{
                        "type": "Text",
                        "reading_order": [0],
                        "bbox": [0, 0, 10, 10],
                        "text": "OCR text",
                    }],
                }]
            }

            class FakeServer:
                calls = 0

                def token_count(self, text):
                    return len(text.split())

                def complete(self, system_prompt, user_prompt, max_tokens):
                    self.calls += 1
                    self.system_prompt = system_prompt
                    self.user_prompt = user_prompt
                    self.max_tokens = max_tokens
                    return "OCR text\n\n![Figure](figure.png)\n"

            server = FakeServer()
            first = marker.refine_markdown_with_qwen(
                markdown, metadata, checkpoint, server
            )
            second = marker.refine_markdown_with_qwen(
                markdown, metadata, checkpoint, server
            )

            self.assertEqual(first, second)
            self.assertEqual(server.calls, 1)
            self.assertIn("untrusted data", server.system_prompt)
            self.assertIn("OCR texxt", server.user_prompt)
            self.assertGreater(server.max_tokens, 256)
            self.assertEqual(markdown.read_text(encoding="utf-8"), original)
            self.assertIn("OCR text", checkpoint.read_text(encoding="utf-8"))
            self.assertTrue(checkpoint.with_suffix(".md.sha256").is_file())
            self.assertEqual(
                {path.name for path in checkpoint.parent.iterdir()},
                {"page0001.md", "page0001.md.sha256"},
            )

    def test_compact_qwen_metadata_is_a_semantic_layout_map(self):
        metadata = {
            "pages": [{
                "page_id": 6,
                "bbox": [0, 0, 200, 400],
                "text_extraction_method": "surya",
                "blocks": [{
                    "type": "SectionHeader",
                    "bbox": [20, 40, 180, 80],
                    "text": "A " * 60,
                    "text_extraction_method": "pdftext",
                    "confidence": {"best": {"label": "Text", "score": 0.5}},
                    "children": [{
                        "type": "Line",
                        "text": "child text must not replace the semantic parent",
                    }],
                }],
            }]
        }

        compact = marker.compact_refinement_metadata(metadata)

        page = compact["pages"][0]
        self.assertEqual(compact["schema_version"], 2)
        self.assertEqual(page["page"], 7)
        self.assertEqual(page["default_source"], "ocr")
        self.assertEqual(page["bbox_scale"], 1000)
        self.assertEqual(len(page["regions"]), 1)
        region = page["regions"][0]
        self.assertEqual(region["type"], "SectionHeader")
        self.assertEqual(region["bbox_2d"], [100, 100, 900, 200])
        self.assertEqual(region["source"], "pdf_text")
        self.assertIn(" … ", region["anchor"])
        self.assertNotIn("confidence", region)

    def test_qwen_rejects_token_limit_completion(self):
        server = marker.QwenServer()
        server.url = "http://127.0.0.1:1"
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{
                "message": {"content": "partial"},
                "finish_reason": "length",
            }]
        }).encode("utf-8")

        with (
            patch.object(server, "start"),
            patch.object(marker.urllib.request, "urlopen", return_value=response),
            self.assertRaisesRegex(RuntimeError, "truncated"),
        ):
            server.complete("system", "user", 10)

    def test_failed_page_keeps_previous_refined_document(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=10, height=10)
            writer.add_blank_page(width=10, height=10)
            with source.open("wb") as output_pdf:
                writer.write(output_pdf)
            output = root / "out"
            output.mkdir()
            previous = output / "source.refined.md"
            previous.write_text("previous successful refinement\n", encoding="utf-8")
            converted = root / "converted.md"
            converted.write_text("page one\n", encoding="utf-8")
            marker.metadata_for(converted).write_text(
                json.dumps({"structured_document": {"pages": [{
                    "page_id": 0,
                    "bbox": [0, 0, 10, 10],
                    "blocks": [],
                }]}}),
                encoding="utf-8",
            )

            with (
                patch.object(marker, "prepare_input_pdf", return_value=source),
                patch.object(marker, "qpdf_page_count", return_value=2),
                patch.object(marker, "split_pdf", return_value=[source, source]),
                patch.object(marker, "convert_input", side_effect=[converted, None]),
                patch.object(marker, "QwenServer") as qwen,
            ):
                failed = marker.process_page_by_page(
                    source, output, "1.10", 30, True, 1, True, True, True
                )

            self.assertEqual(failed, [2])
            self.assertEqual(
                previous.read_text(encoding="utf-8"),
                "previous successful refinement\n",
            )
            qwen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
