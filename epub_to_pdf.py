"""Convert EPUB to PDF without loading Marker models."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    from marker.providers import BaseProvider
    from marker.providers.epub import EpubProvider

    class OutputTarget:
        temp_pdf_path = str(args.destination)
        get_font_css = staticmethod(BaseProvider.get_font_css)

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    EpubProvider.convert_epub_to_pdf(OutputTarget(), str(args.source))
    if not args.destination.is_file() or not args.destination.stat().st_size:
        raise RuntimeError("EPUB conversion did not create a PDF")


if __name__ == "__main__":
    main()
