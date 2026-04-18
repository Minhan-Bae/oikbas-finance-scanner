"""Gemini Vision 기반 PDF·차트 이미지 요약.

finance-scanner의 보조 모듈. 매크로 리포트 PDF, 차트 이미지(Finviz/TradingView 스크린샷 등)를
Gemini Vision에 투입해 볼트 `044_Macro` 노트 초안 또는 구조화된 요약을 얻는다.

Opus 분석 파이프라인 대체가 아니라 **원천 재료 수집 단계의 보조** — 이미지/PDF에서
숫자·라벨·트렌드를 텍스트로 끌어내 Opus가 쓰기 쉬운 입력으로 만든다.

사용:
    from oikbas_finance.vision import summarize_pdf, analyze_chart

    md = summarize_pdf(Path("macro_report.pdf"))
    obj = analyze_chart(Path("spx_daily.png"), context="SPX daily, 3mo")

환경변수: VERTEX_AI_API_KEY 또는 GEMINI_API_KEY.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_MODEL = "gemini-2.5-flash"  # Vision 처리엔 Flash가 단가/정확도 균형
_PRO_MODEL = "gemini-2.5-pro"         # 긴 PDF·복잡 차트 fallback


class VisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChartAnalysis:
    title: str
    timeframe: str | None
    key_levels: list[float]
    trend: str  # "up" | "down" | "sideways" | "unclear"
    summary: str
    raw: dict[str, Any]


def _get_api_key() -> str:
    key = os.environ.get("VERTEX_AI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise VisionError("VERTEX_AI_API_KEY or GEMINI_API_KEY env required")
    return key


def _get_client():
    try:
        from google import genai  # type: ignore
    except ImportError as exc:
        raise VisionError(
            "google-genai not installed. Add 'google-genai>=0.3' to dependencies."
        ) from exc
    return genai.Client(api_key=_get_api_key())


def _load_file_part(path: Path):
    """경로를 genai SDK의 Part 형태로 변환."""
    from google.genai import types  # type: ignore

    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0]
    if not mime:
        mime = "application/pdf" if path.suffix.lower() == ".pdf" else "image/png"
    return types.Part.from_bytes(data=data, mime_type=mime)


def summarize_pdf(
    pdf_path: Path,
    *,
    focus: str | None = None,
    model: str = _DEFAULT_MODEL,
) -> str:
    """PDF → 마크다운 요약.

    focus: 강조할 관점 (예: "small-cap swing trading 관점", "매크로 환율 영향").
           None이면 일반 거시 리포트 요약.
    """
    client = _get_client()
    from google.genai import types  # type: ignore

    focus_line = (
        f"\n특별히 다음 관점을 강조해서 요약해라: {focus}" if focus else ""
    )
    instruction = (
        "다음 PDF 리포트를 분석해 한국어 마크다운으로 요약한다.\n"
        "형식:\n"
        "## 핵심 요약 (3줄)\n"
        "## 주요 수치 (표)\n"
        "## 리스크 / 기회\n"
        "## 액션 가능한 시그널\n"
        f"{focus_line}\n\n"
        "원문에 없는 수치를 지어내지 말 것. 불확실하면 '원문 미기재'로 표기."
    )
    part = _load_file_part(pdf_path)
    resp = client.models.generate_content(
        model=model,
        contents=[instruction, part],
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return resp.text or ""


_CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "timeframe": {"type": "string", "nullable": True},
        "key_levels": {
            "type": "array",
            "items": {"type": "number"},
            "description": "차트에 표시된 주요 지지/저항 가격 (숫자만)",
        },
        "trend": {
            "type": "string",
            "enum": ["up", "down", "sideways", "unclear"],
        },
        "summary": {
            "type": "string",
            "description": "1~2문장 핵심 요약 (한국어)",
        },
    },
    "required": ["title", "key_levels", "trend", "summary"],
}


def analyze_chart(
    image_path: Path,
    *,
    context: str | None = None,
    model: str = _DEFAULT_MODEL,
) -> ChartAnalysis:
    """차트 이미지 → 구조화 분석."""
    client = _get_client()
    from google.genai import types  # type: ignore

    ctx = f"\n추가 컨텍스트: {context}" if context else ""
    instruction = (
        "이 차트 이미지를 분석해 JSON으로만 답한다. "
        "보이는 가격 라벨/지지저항/추세만 쓰고, 보이지 않는 것은 추정하지 마라." + ctx
    )
    part = _load_file_part(image_path)
    resp = client.models.generate_content(
        model=model,
        contents=[instruction, part],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=_CHART_SCHEMA,
        ),
    )
    raw = json.loads(resp.text or "{}")
    return ChartAnalysis(
        title=raw.get("title", ""),
        timeframe=raw.get("timeframe"),
        key_levels=[float(x) for x in raw.get("key_levels", [])],
        trend=raw.get("trend", "unclear"),
        summary=raw.get("summary", ""),
        raw=raw,
    )
