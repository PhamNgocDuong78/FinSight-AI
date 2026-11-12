"""Abstract interface for pluggable document parsers."""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from app.schemas.ingestion import ParsedDocument


class BaseDocumentParser(ABC):
    """Pluggable interface for document parsers (PDF, DOCX, HTML, etc.)."""

    @abstractmethod
    def parse(self, file_path: Path | str) -> ParsedDocument:
        """Synchronously parse a document into a unified ParsedDocument."""

    async def aparse(self, file_path: Path | str) -> ParsedDocument:
        """Asynchronously parse a document without blocking the event loop."""
        return await asyncio.to_thread(self.parse, file_path)
