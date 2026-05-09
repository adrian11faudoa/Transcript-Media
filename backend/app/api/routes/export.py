"""
Export route — exports are handled inline in /transcripts/{job_id}/export.
This module exists for future standalone export job queuing.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/export")


@router.get("/formats")
async def list_export_formats():
    """List all supported export formats with descriptions."""
    return {
        "formats": [
            {"id": "docx", "label": "Word Document", "extension": ".docx",
             "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "description": "Formatted transcript with styles, speaker labels, and table of contents"},
            {"id": "pdf", "label": "PDF", "extension": ".pdf",
             "mime": "application/pdf",
             "description": "Print-ready PDF with professional layout"},
            {"id": "txt", "label": "Plain Text", "extension": ".txt",
             "mime": "text/plain",
             "description": "Clean text with optional timestamps and speaker labels"},
            {"id": "srt", "label": "SubRip Subtitles", "extension": ".srt",
             "mime": "text/plain",
             "description": "Industry-standard subtitle format for video players"},
            {"id": "vtt", "label": "WebVTT", "extension": ".vtt",
             "mime": "text/vtt",
             "description": "HTML5 video subtitle format with speaker cues"},
            {"id": "json", "label": "JSON", "extension": ".json",
             "mime": "application/json",
             "description": "Full machine-readable transcript with word-level timestamps"},
        ]
    }
