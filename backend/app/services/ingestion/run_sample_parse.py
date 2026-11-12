"""CLI script to generate/parse sample financial PDFs in data/raw_pdfs/ and export to data/processed_md/."""

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.ingestion.pdf_parser import (
    LayoutAwarePDFParser,
    export_parsed_document,
)
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf

logger = get_logger(__name__)


def main() -> None:
    """Ensure sample PDF exists, parse all PDFs in raw_pdf_dir, and export Markdown + JSON."""
    settings = get_settings()
    setup_logging(debug=settings.app_debug)

    raw_dir = settings.raw_pdf_dir
    out_dir = settings.processed_md_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(raw_dir.glob("*.pdf"))
    if not pdf_files:
        sample_path = raw_dir / "FPT_FS_2024_sample.pdf"
        create_sample_financial_pdf(sample_path)
        logger.info("sample_pdf_generated", path=str(sample_path))
        pdf_files = [sample_path]

    parser = LayoutAwarePDFParser()
    for pdf_path in pdf_files:
        parsed_doc = parser.parse(pdf_path)
        md_path, json_path = export_parsed_document(parsed_doc, out_dir)
        logger.info(
            "sample_parse_success",
            pdf=pdf_path.name,
            pages=parsed_doc.total_pages,
            tables=parsed_doc.total_tables,
            markdown_out=str(md_path),
            json_out=str(json_path),
        )


if __name__ == "__main__":
    main()
