import sys
sys.path.insert(0, '.')
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter
from adapters.scrapers.enacom_web.captcha_solver import _get_ocr_executor, _ocr_lock
import cv2, numpy as np, asyncio, re
import winsdk.windows.media.ocr as win_ocr
import winsdk.windows.graphics.imaging as win_imaging
import winsdk.windows.storage.streams as win_streams

engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()

async def recognize_async(enc_bytes):
    stream = win_streams.InMemoryRandomAccessStream()
    w = win_streams.DataWriter(stream)
    w.write_bytes(enc_bytes)
    await w.store_async()
    stream.seek(0)
    dec = await win_imaging.BitmapDecoder.create_async(stream)
    bmp = await dec.get_software_bitmap_async()
    res = await engine.recognize_async(bmp)
    stream.close()
    return re.sub(r'[^a-zA-Z0-9]', '', res.text or ''), (res.text_angle or 0.0)

def ocr_sync(mat):
    ok, enc = cv2.imencode('.png', mat)
    if not ok: return '', 0.0
    with _ocr_lock:
        f = _get_ocr_executor().submit(lambda: asyncio.run(recognize_async(enc.tobytes())))
        return f.result(timeout=4.0)

adapter = EnacomWebAdapter(headless=True)
page = adapter._ensure_browser()

for i in range(15):
    b = adapter._recargar_captcha_sincronico(page)
    nparr = np.frombuffer(b, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    # 1. CleanSmooth
    smooth = adapter.solver.clean_smooth(img)
    t1, ang1 = ocr_sync(smooth)
    
    # 2. Deskewed smooth (if angle is detected)
    t2 = t1
    if abs(ang1) > 2.0:
        h, w = smooth.shape[:2]
        M = cv2.getRotationMatrix2D((w/2, h/2), -ang1, 1.0)
        deskewed = cv2.warpAffine(smooth, M, (w, h), borderValue=[255, 255, 255])
        t2, ang2 = ocr_sync(deskewed)
    
    # 3. Clean Binary (pure black text on white)
    b_ch, g_ch, r_ch = cv2.split(img)
    diff = b_ch.astype(int) - np.maximum(r_ch, g_ch).astype(int)
    mask = (diff > 15) & (b_ch > 50)
    bin_mat = np.full_like(b_ch, 255)
    bin_mat[mask] = 0
    bin_pad = cv2.copyMakeBorder(bin_mat, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    bin_scaled = cv2.resize(bin_pad, (bin_pad.shape[1] * 2, bin_pad.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    t3, _ = ocr_sync(bin_scaled)
    
    match = (t1 == t2 == t3)
    print(f"#{i+1:2}: Smooth={t1:5} (ang={ang1:4.1f}) | Deskew={t2:5} | Bin={t3:5} | AllMatch={match}")

adapter.cerrar()
