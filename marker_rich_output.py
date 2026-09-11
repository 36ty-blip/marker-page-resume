"""Marker renderer that keeps Markdown output and adds structural metadata.

This module is loaded inside either Marker virtual environment through
PYTHONPATH.  It intentionally uses only APIs shared by Marker 1.10 and 2.0.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from marker.converters.pdf import PdfConverter
from marker.renderers.markdown import MarkdownRenderer
from marker.settings import settings


SCHEMA_VERSION = 1


def _enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def _plain_text(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def _confidence(block: Any) -> dict | None:
    top_k = getattr(block, "top_k", None)
    if not top_k:
        return None
    values = [
        {"label": _enum_name(label), "score": float(score)}
        for label, score in top_k.items()
    ]
    values.sort(key=lambda item: item["score"], reverse=True)
    return {"best": values[0], "alternatives": values[1:]}


def _block_record(document: Any, rendered_block: Any, order: list[int]) -> dict:
    block_id = rendered_block.id
    block = document.get_block(block_id)
    polygon = rendered_block.polygon
    block_type = _enum_name(block_id.block_type)
    record = {
        "id": str(block_id),
        "type": block_type,
        "reading_order": order,
        "bbox": list(polygon.bbox),
        "polygon": [list(point) for point in polygon.polygon],
    }

    text = _plain_text(rendered_block.html)
    if not text and block is not None and block_type not in {"Line", "Span", "Char"}:
        text = block.raw_text(document).strip()
    if text:
        record["text"] = text
    if rendered_block.html and "content-ref" not in rendered_block.html:
        record["html"] = rendered_block.html

    if block is not None:
        source = getattr(block, "source", None)
        if source:
            record["source"] = source
        extraction = getattr(block, "text_extraction_method", None)
        if extraction:
            record["text_extraction_method"] = extraction
        confidence = _confidence(block)
        if confidence:
            record["confidence"] = confidence

    image_name = (
        f"{block_id.to_path()}.{settings.OUTPUT_IMAGE_FORMAT.lower()}"
        if block_id.block_type is not None
        else None
    )
    if image_name:
        record["possible_image_reference"] = image_name

    children = rendered_block.children or []
    if children:
        record["children"] = [
            _block_record(document, child, [*order, index])
            for index, child in enumerate(children)
        ]
    return record


def rich_document_metadata(document: Any, document_output: Any) -> dict:
    pages = []
    rendered_pages = document_output.children or []
    for index, rendered_page in enumerate(rendered_pages):
        page = document.get_page(rendered_page.id.page_id)
        pages.append(
            {
                "page_id": rendered_page.id.page_id,
                "reading_order": index,
                "bbox": list(rendered_page.polygon.bbox),
                "polygon": [
                    list(point) for point in rendered_page.polygon.polygon
                ],
                "text_extraction_method": getattr(
                    page, "text_extraction_method", None
                ),
                "blocks": [
                    _block_record(document, block, [block_index])
                    for block_index, block in enumerate(
                        rendered_page.children or []
                    )
                ],
            }
        )
    return {
        "schema": "marker-rich-document",
        "schema_version": SCHEMA_VERSION,
        "pages": pages,
    }


class RichMarkdownRenderer(MarkdownRenderer):
    """Return Marker Markdown while attaching a common structural record."""

    def __call__(self, document):
        rendered = super().__call__(document)
        document_output = document.render(self.block_config)
        rendered.metadata["structured_document"] = rich_document_metadata(
            document, document_output
        )

        extracted_images = set(rendered.images)

        def remove_missing_image_hints(blocks: list[dict]) -> None:
            for block in blocks:
                image_name = block.pop("possible_image_reference", None)
                if image_name in extracted_images:
                    block["image_reference"] = image_name
                remove_missing_image_hints(block.get("children", []))

        for page in rendered.metadata["structured_document"]["pages"]:
            remove_missing_image_hints(page["blocks"])
        return rendered


class RichPdfConverter(PdfConverter):
    """Force the rich Markdown renderer while retaining normal CLI behavior."""

    def __init__(
        self,
        artifact_dict,
        processor_list=None,
        renderer=None,
        llm_service=None,
        config=None,
    ):
        super().__init__(
            artifact_dict=artifact_dict,
            processor_list=processor_list,
            renderer="marker_rich_output.RichMarkdownRenderer",
            llm_service=llm_service,
            config=config,
        )
