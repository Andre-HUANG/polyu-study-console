"""The locked PyWinRT projection has distinct converted-bitmap methods."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/"学习控制台"))
import windows_document

class WindowsOCRProjectionChecks(unittest.TestCase):
    def exercise(self,fail=False):
        bitmap=SimpleNamespace(close=Mock())
        stream=SimpleNamespace(close=Mock())
        decoder=SimpleNamespace(get_software_bitmap_converted_async=AsyncMock(return_value=bitmap))
        file=SimpleNamespace(open_async=AsyncMock(return_value=stream))
        storage=SimpleNamespace(StorageFile=SimpleNamespace(get_file_from_path_async=AsyncMock(return_value=file)),FileAccessMode=SimpleNamespace(READ=1))
        imaging=SimpleNamespace(BitmapDecoder=SimpleNamespace(create_async=AsyncMock(return_value=decoder)),
            BitmapPixelFormat=SimpleNamespace(BGRA8=87),BitmapAlphaMode=SimpleNamespace(PREMULTIPLIED=1))
        engine=SimpleNamespace(recognize_async=AsyncMock())
        if fail:engine.recognize_async.side_effect=RuntimeError("synthetic OCR failure")
        else:engine.recognize_async.return_value=SimpleNamespace(lines=[SimpleNamespace(text="synthetic OCR text")])
        ocr=SimpleNamespace(OcrEngine=SimpleNamespace(available_recognizer_languages=[SimpleNamespace(language_tag="en-US")],
            try_create_from_language=Mock(return_value=engine)))
        with patch.dict(sys.modules,{"winrt.windows.storage":storage,"winrt.windows.graphics.imaging":imaging,"winrt.windows.media.ocr":ocr}):
            if fail:
                with self.assertRaises(RuntimeError):asyncio.run(windows_document._ocr("synthetic.png"))
            else:self.assertEqual(asyncio.run(windows_document._ocr("synthetic.png")),["synthetic OCR text"])
        decoder.get_software_bitmap_converted_async.assert_awaited_once_with(87,1)
        bitmap.close.assert_called_once();stream.close.assert_called_once()
    def test_projected_converted_bitmap_method_is_used(self):self.exercise()
    def test_failure_still_closes_bitmap_and_stream(self):self.exercise(fail=True)
