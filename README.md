# Marker Controller

A Windows-friendly controller for converting PDFs and supported documents to
Markdown with Marker. It adds resumable page checkpoints, low-VRAM execution,
multiple inputs, page selection, structured results, a desktop GUI, and a
small offline interactive tutorial.

Marker Controller does not upload documents or tutorial data. Conversion,
checkpoints, and the tutorial run locally.

## Features

- Marker 1.10 and Marker 2 execution paths
- Page-by-page or whole-document conversion
- Safe interruption and per-document resume
- Resume using saved settings or compatible new settings
- Multiple files and folders, recursive discovery, and include/exclude filters
- One-based page ranges such as `1-5,8,12-`
- TTY-aware progress plus quiet, verbose, and debug modes
- Diagnostic logs and JSON Lines results
- Optional rich structural metadata and local Qwen refinement
- Tk desktop GUI
- Lightweight offline `--tutorial`

## Requirements

- Windows 10 or 11
- Python 3.12 recommended
- An NVIDIA GPU supported by the selected Marker runtime
- [qpdf](https://qpdf.sourceforge.io/) for page-by-page processing
- Marker and any optional llama.cpp/GGUF models installed locally

The controller contains no model files or third-party executables.

## Install

```powershell
git clone https://github.com/36ty-blip/marker-controller.git
cd marker-controller
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install "marker-pdf==1.10.2"
```

Run the controller from the repository:

```powershell
.\marker.cmd --check
.\marker.cmd --tutorial
```

The default paths target the author's Windows layout. Override runtime paths
without editing the source:

```powershell
$env:MARKER1_EXE = "C:\path\to\marker_single.exe"
$env:MARKER2_EXE = "C:\path\to\marker_single.exe"
$env:QPDF_EXE = "C:\path\to\qpdf.exe"
$env:LLAMA_SERVER = "C:\path\to\llama-server.exe"
$env:SURYA_MODEL = "C:\path\to\surya.gguf"
$env:SURYA_MMPROJ = "C:\path\to\surya-mmproj.gguf"
$env:QWEN_MODEL = "C:\path\to\qwen.gguf"
```

Only configure the Marker 2 and Qwen variables when using those features.

## Quick start

Marker 2 is the default engine, including for images. Select Marker 1 explicitly
with `--engine marker1` when needed.

```powershell
# Convert one PDF
.\marker.cmd document.pdf

# Convert selected pages
.\marker.cmd document.pdf --pages 1-5,8,12-

# Convert multiple PDFs
.\marker.cmd first.pdf second.pdf -o converted

# Search a folder recursively
.\marker.cmd documents -o converted --recursive

# Open the desktop interface
.\marker.cmd --gui

# Resume the most recent unfinished conversion
.\marker.cmd --resume
```

Use `--dry-run` to inspect file selection without starting conversion:

```powershell
.\marker.cmd documents -o converted --recursive --dry-run
```

## Offline tutorial

```powershell
.\marker.cmd --tutorial
```

The tutorial uses local static content. It can build and preview a command,
explain the major options, and run local installation checks. It never starts
a conversion without confirmation and makes no network requests.

## Resume behavior

Unfinished state is stored under the output folder:

```text
marker_output/
└── _marker_work/
    └── document-1a2b3c4d/
        ├── run_manifest.json
        ├── split_pages/
        └── page_markdown/
```

Completed compatible pages are reused. Changing batch size, GPU tuning, page
selection, metadata, or Qwen refinement can retain compatible work. Changing
the engine or processing mode restarts that document. Changed source contents
are never combined with stale checkpoints.

## Diagnostics and structured output

```powershell
.\marker.cmd document.pdf --quiet
.\marker.cmd document.pdf --verbose --log marker.log
.\marker.cmd document.pdf --jsonl
```

Human diagnostics go to standard error. Normal result paths or JSONL records
go to standard output, making the command suitable for scripts.

## Tests

The unit tests do not require a GPU or model files:

```powershell
python -m unittest -v test_marker_process.py
```

## License

MIT
