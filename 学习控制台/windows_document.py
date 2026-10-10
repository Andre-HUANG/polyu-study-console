"""Local Windows PDF text/table and Windows Runtime OCR adapter.

Windows OCR supplies no confidence value. Its rows retain unknown confidence
and assessment dates remain candidates for confirmation, never fabricated scores.
"""
from __future__ import annotations
import asyncio
import tempfile
from pathlib import Path


async def _ocr(path):
    from winrt.windows.storage import StorageFile, FileAccessMode
    from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.media.ocr import OcrEngine
    file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    bitmap = None
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED)
        languages = [language for language in OcrEngine.available_recognizer_languages
                     if language.language_tag.lower().startswith(("en", "zh"))][:3]
        engines = [OcrEngine.try_create_from_language(language) for language in languages]
        if not engines:
            engines = [OcrEngine.try_create_from_user_profile_languages()]
        engines = [engine for engine in engines if engine is not None]
        if not engines:
            raise RuntimeError("Windows 本机 OCR 语种不可用；请核对系统语言功能，原文件已保留")
        rows = []
        for engine in engines:
            result = await engine.recognize_async(bitmap)
            rows.extend(line.text for line in result.lines)
        return list(dict.fromkeys(rows))
    finally:
        if bitmap is not None:
            bitmap.close()
        stream.close()


def ocr_image(path):
    from PIL import Image
    # Both the resized image and PDF render stay inside this account's documents.
    with tempfile.TemporaryDirectory(prefix=".ocr-", dir=Path(path).parent) as temporary:
        prepared = Path(temporary) / "page.png"
        with Image.open(path) as image:
            image.convert("RGB").save(prepared)
        with Image.open(prepared) as image:
            image.thumbnail((2000, 2000))
            image.save(prepared)
        return asyncio.run(_ocr(prepared))


def read(path, text_only=False):
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        rows = ocr_image(path)
        return [{"page": 1, "text": "\n".join(rows), "rows": rows,
                 "rowConfidences": [None] * len(rows), "confidenceUnknown": True,
                 "method": "Windows 本机 OCR（置信度未提供）", "lowConfidence": True,
                 "readable": bool(rows), "processed": True}]
    import pdfplumber
    pages = []
    try:
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                pages.append({"page": number, "text": text, "rows": text.splitlines(),
                              "method": "PDF 文字与版面", "readable": bool(text.strip()),
                              "processed": True, "textOnly": text_only})
                page.close()
    except Exception as error:
        raise RuntimeError("PDF 正文无法打开或受密码保护（" + type(error).__name__ + "）") from None
    if text_only:
        return pages
    import pypdfium2
    with pypdfium2.PdfDocument(path) as pdf, tempfile.TemporaryDirectory(prefix=".ocr-", dir=path.parent) as temporary:
        for number, result in enumerate(pages):
            bitmap = page = None
            try:
                page = pdf[number]
                scale = min(3, 2000 / max(page.get_size()))
                bitmap = page.render(scale=scale)
                rendered = Path(temporary) / "page.png"
                bitmap.to_pil().save(rendered)
                rows = ocr_image(rendered)
                result.update(ocrRows=rows, ocrRowConfidences=[None] * len(rows),
                              ocrConfidenceUnknown=True, ocrLowConfidence=True,
                              readable=result["readable"] or bool(rows), textOnly=False)
                if rows:
                    result["method"] += " · Windows 本机 OCR（置信度未提供）"
            except Exception as error:
                result.update(textOnly=False, issues=["本页图片读取未完成（" + type(error).__name__ + "）"])
            finally:
                if bitmap is not None: bitmap.close()
                if page is not None: page.close()
    return pages
