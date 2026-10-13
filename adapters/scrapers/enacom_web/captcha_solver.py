# -*- coding: utf-8 -*-
"""
Módulo de Resolución de Captcha para ENACOM Numeración.
Utiliza procesamiento de imágenes con OpenCV para remover ruido y líneas de interferencia,
y el motor OCR nativo de Windows (Windows.Media.Ocr vía winsdk) para reconocimiento 100% local,
sin dependencias de cloud APIs ni ejecutables externos.
"""
import os
import re
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
import cv2
import winsdk.windows.media.ocr as win_ocr
import winsdk.windows.graphics.imaging as win_imaging
import winsdk.windows.storage as win_storage


class EnacomCaptchaSolver:
    """Extractor y resolutor OCR para los captchas alfanuméricos de ENACOM."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="EnacomOCR")

    def clean_image_bytes(self, img_bytes: bytes) -> np.ndarray:
        """
        Filtra y binariza la imagen del captcha eliminando las líneas de interferencia.
        El texto es azul oscuro (B alto, R y G bajos), mientras que las líneas son grises (R ~ G ~ B).
        """
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar la imagen del captcha.")

        b, g, r = cv2.split(img)
        # Máscara cromática: el texto es azul nítido
        text_mask = (b > 80) & (b.astype(int) - g.astype(int) > 25) & (b.astype(int) - r.astype(int) > 25)

        # Fondo blanco (255), texto negro (0)
        cleaned = np.full_like(b, 255)
        cleaned[text_mask] = 0

        # Eliminar motas de ruido y cruces de líneas residuales (< 10 píxeles)
        bin_inv = (cleaned == 0).astype(np.uint8)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bin_inv, connectivity=8)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] < 10:
                cleaned[labels == i] = 255

        # Margen perimetral para optimizar segmentación OCR
        cleaned = cv2.copyMakeBorder(cleaned, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)

        # Escalar 3x con interpolación cúbica para alta nitidez en bordes
        h, w = cleaned.shape
        resized = cv2.resize(cleaned, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
        return resized

    async def _recognize_async(self, file_path: str) -> str:
        """Invoca el motor OCR nativo de Windows 10/11."""
        abs_path = os.path.abspath(file_path)
        storage_file = await win_storage.StorageFile.get_file_from_path_async(abs_path)
        stream = await storage_file.open_async(win_storage.FileAccessMode.READ)
        decoder = await win_imaging.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()
        if not engine:
            return ""
        result = await engine.recognize_async(bitmap)
        raw_text = result.text or ""
        # Limpieza de caracteres espurios, espacios o guiones
        clean_text = re.sub(r"[^a-zA-Z0-9]", "", raw_text)
        return clean_text

    def _sync_recognize(self, file_path: str) -> str:
        return asyncio.run(self._recognize_async(file_path))

    def solve_bytes(self, img_bytes: bytes) -> str:
        """Procesa y resuelve el código del captcha a partir de los bytes crudos."""
        cleaned_mat = self.clean_image_bytes(img_bytes)
        # Guardar en archivo temporal para la API de Windows Storage
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            cv2.imwrite(temp_path, cleaned_mat)
            future = self._executor.submit(self._sync_recognize, temp_path)
            solved_text = future.result(timeout=5.0)
            return solved_text.strip()
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
