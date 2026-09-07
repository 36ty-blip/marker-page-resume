# Resumable page processing for Marker 1 and Marker 2

Run each PDF page in a fresh Marker worker process, checkpoint completed pages, and resume after an interruption without repeating finished work.

This controller is **independent of Marker’s model architecture**. It works with Marker 1.x and the newer VLM-backed Marker 2. It does not require a GPU; Marker can use whichever backend is configured separately.

## Why use it?

- Press `Ctrl+C` at any time. Completed pages remain checkpointed.
- Resume with the same command. Only an interrupted or incomplete page is rerun.
- Memory and failures are isolated to one page worker instead of accumulating in a long-lived document process.

## Install

Install Marker separately, then install the controller’s only dependency:

```console
python -m pip install pypdf
```

## Windows-friendly graphical launcher

Run the script without arguments:

```console
python page_controller.py
```

The pop-up window lets you select:

- one input PDF or a folder of PDFs;
- the output folder;
- the `marker_single` executable;
- Marker 1.10 or Marker 2;
- low-memory Marker 1 batch sizes;
- any additional Marker options.

Choose **Start / Resume** to begin. Choose **Pause** at any time to stop the active page worker safely. Starting again skips every completed page and reruns only the interrupted page.

### Create a Windows desktop shortcut

Use the same Python installation where you installed `pypdf`. Its windowless executable is normally `pythonw.exe`; inside a virtual environment it is under `.venv\Scripts\pythonw.exe`.

1. Right-click the Windows desktop and choose **New → Shortcut**.
2. Enter this as the shortcut location, replacing both example paths:

   ```text
   "C:\path\to\pythonw.exe" "C:\path\to\marker-page-resume\page_controller.py"
   ```

3. Choose **Next**, name it **Marker Page Resume**, and choose **Finish**.
4. Double-click the shortcut whenever you want to open the graphical launcher directly, without a command window.

Keep the quotation marks around both paths, especially when a folder name contains spaces. If you are unsure which Python installation is active, run `where pythonw` in Command Prompt.

## Command-line use

Marker 2 example:

```console
python page_controller.py input.pdf output --marker /path/to/marker_single -- --mode balanced
```

Marker 1 example:

```console
python page_controller.py input.pdf output --marker /path/to/marker_single -- --layout_batch_size 1 --detection_batch_size 1 --ocr_error_batch_size 1 --recognition_batch_size 1 --equation_batch_size 1 --table_rec_batch_size 1
```

Options after the standalone `--` are passed directly to `marker_single`.

Checkpoints and page assets remain under `output/_marker_pages`. The combined result is written to `output/<input-name>.md`.

## Verify

```console
python -m unittest -v test_page_controller.py
```

## License

MIT. This is an independent reference implementation and is not affiliated with Datalab or the Marker project.

