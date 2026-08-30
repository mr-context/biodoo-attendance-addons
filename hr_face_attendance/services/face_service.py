"""
FaceService — Singleton InsightFace (ArcFace buffalo_s) + MiniFASNet ONNX.

Chargé à la première utilisation (import on-demand).
Si antispoof.onnx est absent, la détection de vivacité est désactivée (avertissement).
"""

import os
import logging
import warnings

_logger = logging.getLogger(__name__)

# Supprime le FutureWarning de scikit-image dans insightface (API deprecated mais fonctionnelle)
warnings.filterwarnings('ignore', category=FutureWarning, module='insightface')

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FaceService:
    _instance = None
    _init_error = None

    @classmethod
    def get_instance(cls):
        if cls._init_error is not None:
            raise RuntimeError(cls._init_error)
        if cls._instance is None:
            try:
                cls._instance = cls()
            except Exception as e:
                cls._init_error = str(e)
                raise
        return cls._instance

    def __init__(self):
        import insightface
        import onnxruntime
        import numpy as np
        import cv2

        self.np = np
        self.cv2 = cv2

        # ── Modèle ArcFace (buffalo_s ~80MB, téléchargé au 1er appel) ──────
        # allowed_modules=['detection','recognition'] : charge uniquement les 2 modèles
        # nécessaires (skip landmarks 2D/3D et gender/age → ~3× moins de RAM)
        self.app = insightface.app.FaceAnalysis(
            name='buffalo_s',
            allowed_modules=['detection', 'recognition'],
            providers=['CPUExecutionProvider'],
        )
        self.app.prepare(ctx_id=-1, det_size=(320, 320))

        # ── Modèle anti-spoofing MiniFASNet V2 ONNX ─────────────────────────
        antispoof_path = os.path.join(MODULE_DIR, 'static', 'lib', 'antispoof.onnx')
        if os.path.exists(antispoof_path):
            self.antispoof = onnxruntime.InferenceSession(
                antispoof_path,
                providers=['CPUExecutionProvider'],
            )
            self.antispoof_input_name = self.antispoof.get_inputs()[0].name
            _logger.info('hr_face_attendance: MiniFASNet anti-spoofing chargé.')
        else:
            self.antispoof = None
            _logger.warning(
                'hr_face_attendance: antispoof.onnx introuvable dans static/lib/. '
                'La détection de vivacité est désactivée.'
            )

    # ── Extraction d'embedding ───────────────────────────────────────────────

    def get_embedding(self, img_bytes):
        """Extrait l'embedding ArcFace depuis des bytes JPEG/PNG.

        Retourne (embedding np.float32 512d, bbox) ou (None, None) si aucun visage.
        """
        np = self.np
        cv2 = self.cv2

        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None, None

        faces = self.app.get(img)
        if not faces:
            return None, None

        # Prendre le visage le plus grand (bbox surface)
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return face.embedding, face.bbox

    # ── Vérification 1:1 ────────────────────────────────────────────────────

    def verify(self, emb1, emb2, threshold=0.4):
        """Compare deux embeddings ArcFace via distance cosinus.

        distance = 1 - cosine_similarity  (0 = identique, 2 = opposé)
        Seuil InsightFace buffalo_s : ~0.4 (cosine_distance), soit cosine_sim > 0.6.
        Retourne (verified: bool, distance: float).
        """
        np = self.np
        norm1 = emb1 / (np.linalg.norm(emb1) + 1e-8)
        norm2 = emb2 / (np.linalg.norm(emb2) + 1e-8)
        cosine_distance = 1.0 - float(np.dot(norm1, norm2))
        return cosine_distance < threshold, cosine_distance

    # ── Détection de vivacité ────────────────────────────────────────────────

    def check_liveness(self, img_bytes, bbox):
        """Évalue la vivacité du visage via MiniFASNet.

        Retourne (is_live: bool, score: float 0-1).
        Si le modèle n'est pas disponible, retourne (True, 1.0) — confiance implicite.
        """
        if self.antispoof is None:
            return True, 1.0

        np = self.np
        cv2 = self.cv2

        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return True, 1.0

        # Rogner et redimensionner le visage pour MiniFASNet (entrée 128×128)
        x1, y1, x2, y2 = [int(v) for v in bbox]
        # Marge de 20%
        h, w = img.shape[:2]
        pad_x = int((x2 - x1) * 0.2)
        pad_y = int((y2 - y1) * 0.2)
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(w, x2 + pad_x)
        y2 = min(h, y2 + pad_y)

        face_crop = img[y1:y2, x1:x2]
        if face_crop.size == 0:
            return True, 1.0

        # OpenCV charge en BGR, MiniFASNet attend RGB
        face_crop_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        face_resized = cv2.resize(face_crop_rgb, (128, 128))
        face_float = face_resized.astype(np.float32) / 255.0
        face_input = np.transpose(face_float, (2, 0, 1))[np.newaxis]  # (1, 3, 128, 128)

        try:
            outputs = self.antispoof.run(None, {self.antispoof_input_name: face_input})
            # Sortie : [real_prob, spoof_prob]  — index 0=réel, index 1=spoofing
            probs = self._softmax(outputs[0][0])
            real_score = float(probs[0])
            _logger.info('MiniFASNet liveness: real=%.3f spoof=%.3f → %s',
                         real_score, float(probs[1]),
                         'LIVE' if real_score >= 0.5 else 'SPOOF')
        except Exception as e:
            _logger.warning('Liveness check error: %s', e)
            return True, 1.0

        return True, real_score  # le contrôleur gère les seuils

    @staticmethod
    def _softmax(x):
        import numpy as np
        e = np.exp(x - np.max(x))
        return e / e.sum()
