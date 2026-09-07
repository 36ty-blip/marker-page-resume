"""Run Marker in isolated, resumable, one-page worker processes."""

from __future__ import annotations

import argparse
import hashlib
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pypdf import PdfReader, PdfWriter


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class Paused(Exception):
    """Raised after a requested pause has stopped the active page worker."""


@dataclass(frozen=True)
class ProcessResult:
    final: Path
    failed_pages: tuple[int, ...]


def source_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def split_pdf(source: Path, destination: Path) -> list[Path]:
    reader = PdfReader(str(source))
    destination.mkdir(parents=True, exist_ok=True)
    pages = []
    for number, page in enumerate(reader.pages, 1):
        target = destination / f"page-{number:04}.pdf"
        if not target.is_file() or not target.stat().st_size:
            pending = target.with_suffix(".pdf.tmp")
            with pending.open("wb") as output:
                writer = PdfWriter()
                writer.add_page(page)
                writer.write(output)
            pending.replace(target)
        pages.append(target)
    return pages


def completed_markdown(page_dir: Path) -> Path | None:
    checkpoint = page_dir / "complete.txt"
    if not checkpoint.is_file():
        return None
    markdown = (page_dir / checkpoint.read_text(encoding="utf-8").strip()).resolve()
    try:
        markdown.relative_to(page_dir.resolve())
    except ValueError:
        return None
    return markdown if markdown.is_file() and markdown.stat().st_size else None


def recover_saved_markdown(page_dir: Path) -> Path | None:
    """Recover output saved after Marker finished but before checkpoint creation."""
    candidates = sorted(
        (path for path in (page_dir / "runs").rglob("*.md") if path.stat().st_size),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        return None
    markdown = candidates[0]
    pending = page_dir / "complete.tmp"
    pending.write_text(os.path.relpath(markdown, page_dir), encoding="utf-8")
    pending.replace(page_dir / "complete.txt")
    return markdown


def stop_process(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_marker(
    command: list[str], stop_event: threading.Event | None = None
) -> None:
    process = subprocess.Popen(command)
    try:
        while True:
            try:
                returncode = process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if stop_event is not None and stop_event.is_set():
                    stop_process(process)
                    raise Paused
    except KeyboardInterrupt:
        stop_process(process)
        raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def convert_page(
    page: Path,
    page_dir: Path,
    marker: str,
    marker_args: list[str],
    stop_event: threading.Event | None = None,
) -> Path:
    existing = completed_markdown(page_dir)
    if existing:
        return existing
    recovered = recover_saved_markdown(page_dir)
    if recovered:
        return recovered

    run_dir = page_dir / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        marker,
        str(page),
        "--output_dir",
        str(run_dir),
        "--output_format",
        "markdown",
        *marker_args,
    ]
    run_marker(command, stop_event)
    generated = [path for path in run_dir.rglob("*.md") if path.stat().st_size]
    if not generated:
        raise RuntimeError(f"Marker produced no Markdown for {page.name}")
    markdown = max(generated, key=lambda path: path.stat().st_mtime_ns)

    pending = page_dir / "complete.tmp"
    pending.write_text(os.path.relpath(markdown, page_dir), encoding="utf-8")
    pending.replace(page_dir / "complete.txt")
    return markdown


def assemble_markdown(markdowns: list[Path | None], final: Path) -> None:
    """Create the final Markdown and copy its images out of temporary storage."""
    image_dir = final.parent / "images"
    copied: dict[Path, Path] = {}
    chunks = []

    for number, markdown in enumerate(markdowns, 1):
        if markdown is None:
            chunks.append(f"<!-- Page {number} failed and remains pending. -->")
            continue
        text = markdown.read_text(encoding="utf-8").strip()

        def rewrite(match: re.Match[str]) -> str:
            alt, target = match.groups()
            if "://" in target or target.startswith("#"):
                return match.group(0)
            image = (markdown.parent / target).resolve()
            if not image.is_file():
                return match.group(0)
            if image not in copied:
                image_dir.mkdir(parents=True, exist_ok=True)
                destination = image_dir / (
                    f"{final.stem}_{len(copied) + 1:03}{image.suffix.lower()}"
                )
                shutil.copy2(image, destination)
                copied[image] = destination
            relative = copied[image].relative_to(final.parent).as_posix()
            return f"![{alt}]({relative})"

        chunks.append(IMAGE_RE.sub(rewrite, text))

    pending = final.with_suffix(".md.tmp")
    pending.write_text("\n\n".join(chunks).strip() + "\n", encoding="utf-8")
    pending.replace(final)


def process(
    source: Path,
    output_dir: Path,
    marker: str,
    marker_args: list[str],
    stop_event: threading.Event | None = None,
    status: Callable[[str], None] = print,
    keep_intermediate_files: bool = False,
) -> ProcessResult:
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "_marker_pages" / f"{source.stem}-{source_id(source)}"
    pages = split_pdf(source, work_dir / "input")

    results: list[Path | None] = []
    failed_pages = []
    for number, page in enumerate(pages, 1):
        if stop_event is not None and stop_event.is_set():
            raise Paused
        page_dir = work_dir / f"page-{number:04}"
        state = "skipping completed" if completed_markdown(page_dir) else "processing"
        status(f"[{number}/{len(pages)}] {state}")
        try:
            results.append(
                convert_page(page, page_dir, marker, marker_args, stop_event)
            )
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            failed_pages.append(number)
            results.append(None)
            status(f"[{number}/{len(pages)}] failed: {exc}")

    final = output_dir / f"{source.stem}.md"
    assemble_markdown(results, final)
    if failed_pages:
        status("Pending pages: " + ", ".join(map(str, failed_pages)))
    elif not keep_intermediate_files:
        shutil.rmtree(work_dir)
        try:
            work_dir.parent.rmdir()
        except OSError:
            pass
    return ProcessResult(final, tuple(failed_pages))


def find_sources(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        sources = sorted(
            (item for item in path.iterdir() if item.suffix.lower() == ".pdf"),
            key=lambda item: item.name.casefold(),
        )
        if sources:
            return sources
        raise ValueError(f"No PDF files found in: {path}")
    raise ValueError(f"Select a PDF file or a folder containing PDFs: {path}")


def marker_args_for(version: str, low_memory: bool, extra: str) -> list[str]:
    marker_args = ["--mode", "balanced"] if version == "Marker 2" else []
    if version == "Marker 1.10" and low_memory:
        marker_args.extend(
            [
                "--layout_batch_size", "1",
                "--detection_batch_size", "1",
                "--ocr_error_batch_size", "1",
                "--recognition_batch_size", "1",
                "--equation_batch_size", "1",
                "--table_rec_batch_size", "1",
            ]
        )
    return marker_args + shlex.split(extra)


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Marker resumable page processing")
    root.resizable(False, False)

    input_var = tk.StringVar()
    output_var = tk.StringVar()
    marker_var = tk.StringVar(value="marker_single")
    version_var = tk.StringVar(value="Marker 2")
    low_memory_var = tk.BooleanVar(value=True)
    keep_intermediate_var = tk.BooleanVar(value=False)
    extra_var = tk.StringVar()
    status_var = tk.StringVar(value="Ready")
    events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
    stop_event = threading.Event()
    worker: threading.Thread | None = None

    def choose_input_file() -> None:
        selected = filedialog.askopenfilename(
            parent=root, title="Select a PDF", filetypes=[("PDF files", "*.pdf")]
        )
        if selected:
            input_var.set(selected)
            if not output_var.get():
                output_var.set(str(Path(selected).parent))

    def choose_input_folder() -> None:
        selected = filedialog.askdirectory(parent=root, title="Select PDF folder")
        if selected:
            input_var.set(selected)
            if not output_var.get():
                output_var.set(selected)

    def choose_output() -> None:
        selected = filedialog.askdirectory(parent=root, title="Select output folder")
        if selected:
            output_var.set(selected)

    def choose_marker() -> None:
        selected = filedialog.askopenfilename(
            parent=root, title="Select marker_single executable"
        )
        if selected:
            marker_var.set(selected)

    def update_version(*_args) -> None:
        low_memory_check.configure(
            state="normal" if version_var.get() == "Marker 1.10" else "disabled"
        )

    def set_running(running: bool) -> None:
        start_button.configure(state="disabled" if running else "normal")
        pause_button.configure(state="normal" if running else "disabled")

    def work(
        sources: list[Path], output: Path, marker: str, marker_args: list[str],
        keep_intermediate_files: bool,
    ) -> None:
        try:
            incomplete = []
            for source in sources:
                events.put(("status", f"{source.name}: starting"))
                result = process(
                    source,
                    output,
                    marker,
                    marker_args,
                    stop_event,
                    lambda message, name=source.name: events.put(
                        ("status", f"{name}: {message}")
                    ),
                    keep_intermediate_files,
                )
                if result.failed_pages:
                    incomplete.append(source.name)
                events.put(("status", f"Created: {result.final.name}"))
            message = f"Completed {len(sources)} PDF(s)."
            if incomplete:
                message += " Incomplete: " + ", ".join(incomplete)
            events.put(("done", message))
        except Paused:
            events.put(("paused", "Paused. Press Start to resume."))
        except Exception as exc:
            events.put(("error", str(exc)))

    def start() -> None:
        nonlocal worker
        try:
            sources = find_sources(Path(input_var.get().strip()))
            output = Path(output_var.get().strip())
            marker = marker_var.get().strip()
            if not output_var.get().strip():
                raise ValueError("Select an output folder.")
            if not marker or (not Path(marker).is_file() and shutil.which(marker) is None):
                raise ValueError("Select marker_single or make it available on PATH.")
            marker_args = marker_args_for(
                version_var.get(), low_memory_var.get(), extra_var.get()
            )
            output.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot start", str(exc), parent=root)
            return

        stop_event.clear()
        set_running(True)
        status_var.set("Starting…")
        worker = threading.Thread(
            target=work,
            args=(sources, output, marker, marker_args, keep_intermediate_var.get()),
        )
        worker.start()

    def pause() -> None:
        stop_event.set()
        status_var.set("Pausing after the active worker stops…")
        pause_button.configure(state="disabled")

    def poll_events() -> None:
        try:
            while True:
                kind, message = events.get_nowait()
                status_var.set(message)
                if kind in {"done", "paused", "error"}:
                    set_running(False)
                if kind == "done":
                    messagebox.showinfo("Marker", message, parent=root)
                elif kind == "error":
                    messagebox.showerror("Marker stopped", message, parent=root)
        except queue.Empty:
            pass
        root.after(100, poll_events)

    frame = ttk.Frame(root, padding=14)
    frame.grid()
    ttk.Label(frame, text="Input PDF or folder").grid(row=0, column=0, sticky="w")
    ttk.Entry(frame, textvariable=input_var, width=58).grid(
        row=0, column=1, columnspan=2, padx=8, pady=5
    )
    ttk.Button(frame, text="PDF…", command=choose_input_file).grid(row=0, column=3)
    ttk.Button(frame, text="Folder…", command=choose_input_folder).grid(
        row=0, column=4, padx=(5, 0)
    )

    ttk.Label(frame, text="Output folder").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=output_var, width=58).grid(
        row=1, column=1, columnspan=2, padx=8, pady=5
    )
    ttk.Button(frame, text="Browse…", command=choose_output).grid(
        row=1, column=3, columnspan=2, sticky="ew"
    )

    ttk.Label(frame, text="marker_single").grid(row=2, column=0, sticky="w")
    ttk.Entry(frame, textvariable=marker_var, width=58).grid(
        row=2, column=1, columnspan=2, padx=8, pady=5
    )
    ttk.Button(frame, text="Browse…", command=choose_marker).grid(
        row=2, column=3, columnspan=2, sticky="ew"
    )

    ttk.Label(frame, text="Marker version").grid(row=3, column=0, sticky="w")
    version = ttk.Combobox(
        frame,
        textvariable=version_var,
        values=("Marker 2", "Marker 1.10"),
        state="readonly",
        width=16,
    )
    version.grid(row=3, column=1, sticky="w", padx=8, pady=5)
    low_memory_check = ttk.Checkbutton(
        frame,
        text="Use low-memory batch sizes (Marker 1)",
        variable=low_memory_var,
    )
    low_memory_check.grid(row=3, column=2, columnspan=3, sticky="w")

    ttk.Label(frame, text="Extra Marker options").grid(row=4, column=0, sticky="w")
    ttk.Entry(frame, textvariable=extra_var, width=58).grid(
        row=4, column=1, columnspan=4, sticky="ew", padx=8, pady=5
    )

    ttk.Checkbutton(
        frame,
        text="Keep intermediate files after success",
        variable=keep_intermediate_var,
    ).grid(row=5, column=1, columnspan=4, sticky="w", padx=8, pady=5)

    ttk.Separator(frame).grid(row=6, column=0, columnspan=5, sticky="ew", pady=8)
    ttk.Label(frame, textvariable=status_var, width=72).grid(
        row=7, column=0, columnspan=3, sticky="w"
    )
    pause_button = ttk.Button(frame, text="Pause", command=pause, state="disabled")
    pause_button.grid(row=7, column=3, padx=(8, 5))
    start_button = ttk.Button(frame, text="Start / Resume", command=start)
    start_button.grid(row=7, column=4)

    version_var.trace_add("write", update_version)
    update_version()
    root.protocol("WM_DELETE_WINDOW", lambda: (stop_event.set(), root.destroy()))
    poll_events()
    root.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) == 1 or sys.argv[1:] == ["--gui"]:
        return run_gui()

    parser = argparse.ArgumentParser(
        description="Run Marker one page at a time with atomic resume checkpoints."
    )
    parser.add_argument("source", type=Path, help="PDF file or folder containing PDFs")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--marker", required=True, help="path to marker_single")
    parser.add_argument(
        "--keep-intermediate-files",
        action="store_true",
        help="retain split pages, raw Marker output, and checkpoints after success",
    )
    parser.add_argument(
        "marker_args",
        nargs=argparse.REMAINDER,
        help="Marker options after a standalone -- separator",
    )
    args = parser.parse_args()
    marker_args = args.marker_args[1:] if args.marker_args[:1] == ["--"] else args.marker_args

    try:
        sources = find_sources(args.source)
        incomplete = []
        for source in sources:
            result = process(
                source,
                args.output_dir,
                args.marker,
                marker_args,
                keep_intermediate_files=args.keep_intermediate_files,
            )
            print(f"Created: {result.final}")
            if result.failed_pages:
                incomplete.append((source, result.failed_pages))
        if incomplete:
            for source, pages in incomplete:
                print(f"Incomplete: {source.name}; pending pages: {', '.join(map(str, pages))}")
            return 1
    except ValueError as exc:
        parser.error(str(exc))
    except (KeyboardInterrupt, Paused):
        print("\nPaused. Run the same command to resume; completed pages are preserved.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

