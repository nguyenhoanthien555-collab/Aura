"""
High-fidelity Microsoft Edge neural text-to-speech endpoint.
Provides cloud speech synthesis with zh-CN-XiaoxiaoNeural and other Edge voices.
"""

from __future__ import annotations

import io
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from server.auth import verify_token
from core.logger import logger

router = APIRouter(prefix="/api/voice", tags=["voice"], dependencies=[Depends(verify_token)])


@router.get("/tts")
async def synthesize_speech(
    text: str = Query(..., description="Text to synthesize into neural audio"),
    voice: str = Query("zh-CN-XiaoxiaoNeural", description="Microsoft Edge neural voice"),
    rate: str = Query("+0%", description="Speech rate adjustment (e.g. +0%, +10%, -5%)"),
    pitch: str = Query("+0Hz", description="Speech pitch adjustment (e.g. +0Hz, +2Hz)"),
):
    """
    Synthesize high-fidelity neural speech audio using Microsoft Edge TTS.
    Returns audio/mpeg stream.
    """
    cleaned_text = text.strip()
    if not cleaned_text:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    if len(cleaned_text) > 4000:
        raise HTTPException(status_code=400, detail="Text too long (max 4000 characters)")

    try:
        import edge_tts
    except ImportError as err:
        logger.warning("edge-tts library is not installed: %s", err)
        raise HTTPException(status_code=503, detail="edge-tts is not installed on the server")

    try:
        communicate = edge_tts.Communicate(
            text=cleaned_text,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        audio_stream = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_stream.write(chunk.get("data", b""))

        audio_bytes = audio_stream.getvalue()
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Edge TTS generated empty audio payload")

        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": 'inline; filename="speech.mp3"',
                "Content-Type": "audio/mpeg",
                "Cache-Control": "public, max-age=86400",
            },
        )
    except Exception as exc:
        logger.warning("Edge TTS synthesis failed for voice '%s': %s", voice, exc)
        raise HTTPException(status_code=500, detail=f"TTS synthesis error: {exc}")
