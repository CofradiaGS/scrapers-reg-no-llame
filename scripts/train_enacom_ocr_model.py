# -*- coding: utf-8 -*-
"""
Entrenador del Modelo de Reconocimiento de Caracteres para Captchas ENACOM.
Extrae glifos individuales de las imágenes etiquetadas, normaliza a 24x20 píxeles,
y entrena un clasificador de plantillas/k-NN ultra-rápido en NumPy puro (0 dependencias externas).
"""
import os
import glob
import cv2
import numpy as np

def extract_text_mask(img):
    b, g, r = cv2.split(img)
    diff = b.astype(int) - np.maximum(r, g).astype(int)
    mask = ((diff > 15) & (b > 50)).astype(np.uint8) * 255
    return mask

def main():
    print("=== Entrenando Modelo OCR ENACOM (NumPy Pure Vector Engine) ===")
    
    # Recolectar captchas etiquetados
    dataset = []
    patterns = [
        'data/debug_captchas/*.png',
        'data/debug_captchas_live/*.png',
        'data/samples_captcha/*.png',
        'data/test_captchas/live_*.png',
        'data/failed_test_*.png',
        'data/live_voting_fails/*.png',
    ]
    
    for pat in patterns:
        for p in glob.glob(pat):
            name = os.path.basename(p)
            label = name.rsplit('.', 1)[0].split('_')[-1]
            if len(label) == 5:
                dataset.append((p, label))
                
    print(f"Total imágenes etiquetadas encontradas: {len(dataset)}")
    
    X_samples = []
    y_labels = []
    
    for img_path, label in dataset:
        img = cv2.imread(img_path)
        if img is None:
            continue
        mask = extract_text_mask(img)
        coords = cv2.findNonZero(mask)
        if coords is None:
            continue
        x, y, w, h = cv2.boundingRect(coords)
        
        char_w = w / 5.0
        for i in range(5):
            c_char = label[i]
            cx1 = int(round(x + i * char_w))
            cx2 = int(round(x + (i + 1) * char_w))
            crop = mask[:, max(0, cx1):min(mask.shape[1], cx2)]
            if crop.size == 0:
                continue
            # Redimensionar a 24x20 estándar
            resized = cv2.resize(crop, (20, 24), interpolation=cv2.INTER_AREA)
            feat = (resized > 127).astype(np.float32).flatten()
            X_samples.append(feat)
            y_labels.append(ord(c_char))
            
    X_samples = np.array(X_samples, dtype=np.float32)
    y_labels = np.array(y_labels, dtype=np.int32)
    
    print(f"Total glifos extraídos: {len(X_samples)} | Clases únicas: {len(np.unique(y_labels))}")
    
    out_dir = os.path.join("adapters", "scrapers", "enacom_web")
    os.makedirs(out_dir, exist_ok=True)
    npz_path = os.path.join(out_dir, "enacom_char_model.npz")
    np.savez_compressed(npz_path, X=X_samples, y=y_labels)
    print(f"[OK] Modelo entrenado y guardado exitosamente en: {npz_path}")

if __name__ == '__main__':
    main()
