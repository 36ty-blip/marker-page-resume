"""Run Marker in isolated, resumable, one-page worker processes."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


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


def run_marker(command: list[str]) -> None:
    process = subprocess.Popen(command)
    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def convert_page(
    page: Path, page_dir: Path, marker: str, marker_args: list[str]
) -> Path:
    existing = completed_markdown(page_dir)
    if existing:
        return existing

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
    run_marker(command)
    generated = [path for path in run_dir.rglob("*.md") if path.stat().st_size]
    if not generated:
        raise RuntimeError(f"Marker produced no Markdown for {page.name}")
    markdown = max(generated, key=lambda path: path.stat().st_mtime_ns)

    pending = page_dir / "complete.tmp"
    pending.write_text(os.path.relpath(markdown, page_dir), encoding="utf-8")
    pending.replace(page_dir / "complete.txt")
    return markdown


def make_image_paths_portable(markdown: Path, output_dir: Path) -> str:
    text = markdown.read_text(encoding="utf-8").strip()

    def rewrite(match: re.Match[str]) -> str:
        alt, target = match.groups()
        if "://" in target or target.startswith("#"):
            return match.group(0)
        image = (markdown.parent / target).resolve()
        if not image.exists():
            return match.group(0)
        relative = Path(os.path.relpath(image, output_dir)).as_posix()
        return f"![{alt}]({relative})"

    return IMAGE_RE.sub(rewrite, text)


def process(
    source: Path, output_dir: Path, marker: str, marker_args: list[str]
) -> Path:
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "_marker_pages" / f"{source.stem}-{source_id(source)}"
    pages = split_pdf(source, work_dir / "input")

    results = []
    for number, page in enumerate(pages, 1):
        page_dir = work_dir / f"page-{number:04}"
        state = "skipping completed" if completed_markdown(page_dir) else "processing"
        print(f"[{number}/{len(pages)}] {state}", flush=True)
        results.append(convert_page(page, page_dir, marker, marker_args))

    final = output_dir / f"{source.stem}.md"
    pending = final.with_suffix(".md.tmp")
    chunks = [make_image_paths_portable(path, output_dir) for path in results]
    pending.write_text("\n\n".join(chunks).strip() + "\n", encoding="utf-8")
    pending.replace(final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Marker one page at a time with atomic resume checkpoints."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--marker", required=True, help="path to marker_single")
    parser.add_argument(
        "marker_args",
        nargs=argparse.REMAINDER,
        help="Marker options after a standalone -- separator",
    )
    args = parser.parse_args()
    marker_args = args.marker_args[1:] if args.marker_args[:1] == ["--"] else args.marker_args
    if not args.source.is_file():
        parser.error(f"PDF not found: {args.source}")

    try:
        final = process(args.source, args.output_dir, args.marker, marker_args)
    except KeyboardInterrupt:
        print("\nPaused. Run the same command to resume; completed pages are preserved.")
        return 130
    print(f"Created: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

