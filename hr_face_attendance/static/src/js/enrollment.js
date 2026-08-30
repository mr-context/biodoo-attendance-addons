/** @odoo-module **/
/**
 * Composant OWL — Wizard enrollment facial.
 *
 * Séquence de capture : front → left → right
 * Head-pose estimé via MediaPipe FaceLandmarker (CDN, chargé lazily).
 * Capture automatique quand l'angle est stable 600ms dans la zone cible.
 */

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const SEQUENCE = ["front", "left", "right"];
const INSTRUCTIONS = {
    front: "Regardez droit devant la caméra",
    left:  "Tournez lentement la tête vers la gauche",
    right: "Tournez lentement la tête vers la droite",
};
const STABLE_MS = 600;

// Seuils yaw (degrés approximatifs) pour chaque angle.
// La vidéo est affichée en miroir (CSS scaleX(-1)) mais MediaPipe lit le flux miroir aussi,
// donc "gauche" perçu par l'utilisateur = yaw POSITIF dans le référentiel MediaPipe.
const YAW_ZONES = {
    front: { min: -12, max: 12 },
    left:  { min: 12,  max: 40 },   // tournez vers votre gauche → yaw positif (flux miroir)
    right: { min: -40, max: -12 },  // tournez vers votre droite → yaw négatif (flux miroir)
};

class FaceEnrollmentCamera extends Component {
    static template = "hr_face_attendance.FaceEnrollmentCamera";
    static props = { ...standardWidgetProps };

    setup() {
        this.notification = useService("notification");

        this.videoRef   = useRef("video");
        this.canvasRef  = useRef("canvas");
        this.overlayRef = useRef("overlay");

        this.state = useState({
            capturedFront: false,
            capturedLeft:  false,
            capturedRight: false,
            instruction:   INSTRUCTIONS.front,
            currentAngle:  "front",
            error:         "",
            isFlashing:    false,
            isLoading:     true,
        });

        this._stream = null;
        this._faceLandmarker = null;
        this._rafId = null;
        this._stableStart = null;
        this._sending = false;

        onMounted(() => this._init());
        onWillUnmount(() => this._destroy());
    }

    // ── Initialisation ───────────────────────────────────────────────────────

    async _init() {
        await this._startCamera();
        await this._loadMediaPipe();
        this._startLoop();
    }

    async _startCamera() {
        try {
            this._stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user", width: 640, height: 480 },
            });
            const video = this.videoRef.el;
            video.srcObject = this._stream;
            await video.play();
        } catch (e) {
            this.state.error = "Impossible d'accéder à la caméra : " + e.message;
        }
    }

    async _loadMediaPipe() {
        try {
            const vision = await import(
                "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/+esm"
            );
            const FilesetResolver = vision.FilesetResolver;
            const FaceLandmarker = vision.FaceLandmarker;
            const filesetResolver = await FilesetResolver.forVisionTasks(
                "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm"
            );
            this._faceLandmarker = await FaceLandmarker.createFromOptions(filesetResolver, {
                baseOptions: {
                    modelAssetPath:
                        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
                    delegate: "CPU",
                },
                runningMode: "VIDEO",
                numFaces: 1,
                outputFaceBlendshapes: false,
                outputFacialTransformationMatrixes: true,
            });
            this.state.isLoading = false;
        } catch (e) {
            this.state.isLoading = false;
            this.state.error = "Chargement MediaPipe échoué. Vérifiez la connexion internet.";
        }
    }

    // ── Boucle d'analyse ─────────────────────────────────────────────────────

    _startLoop() {
        const loop = () => {
            this._rafId = requestAnimationFrame(loop);
            this._analyzeFrame();
        };
        this._rafId = requestAnimationFrame(loop);
    }

    _analyzeFrame() {
        const video = this.videoRef.el;
        if (!video || !this._faceLandmarker || video.readyState < 2) return;
        if (this._sending) return;

        const results = this._faceLandmarker.detectForVideo(video, Date.now());
        if (!results.faceLandmarks || !results.faceLandmarks.length) {
            this._stableStart = null;
            const overlay = this.overlayRef.el;
            if (overlay) overlay.getContext("2d").clearRect(0, 0, overlay.width, overlay.height);
            return;
        }

        this._drawLandmarks(results.faceLandmarks[0]);

        const yaw = this._estimateYaw(results.faceLandmarks[0]);
        const angle = this.state.currentAngle;
        const zone = YAW_ZONES[angle];

        if (yaw >= zone.min && yaw <= zone.max) {
            if (!this._stableStart) {
                this._stableStart = Date.now();
            } else if (Date.now() - this._stableStart >= STABLE_MS) {
                this._stableStart = null;
                this._captureAndSend(angle);
            }
        } else {
            this._stableStart = null;
        }
    }

    _drawLandmarks(landmarks) {
        const overlay = this.overlayRef.el;
        if (!overlay) return;
        const ctx = overlay.getContext("2d");
        ctx.clearRect(0, 0, overlay.width, overlay.height);
        ctx.fillStyle = "rgba(255,255,255,0.55)";
        for (const lm of landmarks) {
            // La vidéo est affichée en miroir (CSS scaleX(-1)) → on miroir x
            const x = (1 - lm.x) * overlay.width;
            const y = lm.y * overlay.height;
            ctx.beginPath();
            ctx.arc(x, y, 1.2, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    _estimateYaw(landmarks) {
        // Estimation simple du yaw via la position relative du nez et des yeux
        const nose = landmarks[1];
        const leftEye = landmarks[33];
        const rightEye = landmarks[263];
        const eyeMidX = (leftEye.x + rightEye.x) / 2;
        return (nose.x - eyeMidX) * 200;
    }

    // ── Capture et envoi ─────────────────────────────────────────────────────

    async _captureAndSend(angle) {
        this._sending = true;
        // Flash de capture
        this.state.isFlashing = true;
        setTimeout(() => { this.state.isFlashing = false; }, 350);
        try {
            const video = this.videoRef.el;
            const canvas = this.canvasRef.el;
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext("2d").drawImage(video, 0, 0);
            const imageData = canvas.toDataURL("image/jpeg", 0.85);

            const wizardId = this.props.record.resId;

            // Many2one en Odoo 19 → {id, display_name} ou false
            const empField = this.props.record.data.employee_id;
            const employeeId = empField && (empField.id || empField[0]);

            const result = await rpc("/hr/face/enroll", {
                wizard_id: wizardId,
                employee_id: employeeId,
                image_data: imageData,
                angle: angle,
            });

            if (result.success) {
                // Met à jour les booléens de progression (OWL réagit sur chaque champ)
                for (const a of result.captured) {
                    if (a === "front") this.state.capturedFront = true;
                    if (a === "left")  this.state.capturedLeft  = true;
                    if (a === "right") this.state.capturedRight = true;
                }

                if (result.done) {
                    // Enrollment terminé → recharger le formulaire
                    await this.props.record.save();
                    this.props.record.load();
                    return;
                }

                // Passer à l'angle suivant
                const nextIdx = SEQUENCE.indexOf(angle) + 1;
                if (nextIdx < SEQUENCE.length) {
                    const next = SEQUENCE[nextIdx];
                    this.state.currentAngle = next;
                    this.state.instruction = INSTRUCTIONS[next];
                }
            } else {
                this.state.error = this._errorMessage(result.error);
                setTimeout(() => { this.state.error = ""; }, 3000);
            }
        } catch (e) {
            this.state.error = "Erreur réseau : " + e.message;
        } finally {
            this._sending = false;
        }
    }

    _errorMessage(code) {
        const messages = {
            no_face:       "Aucun visage détecté. Repositionnez-vous.",
            not_authorized:"Accès refusé (droits insuffisants).",
            service_error: "Erreur du service de reconnaissance.",
            store_error:   "Erreur lors de la sauvegarde.",
        };
        return messages[code] || `Erreur : ${code}`;
    }

    // ── Nettoyage ────────────────────────────────────────────────────────────

    _destroy() {
        if (this._rafId) cancelAnimationFrame(this._rafId);
        if (this._stream) this._stream.getTracks().forEach((t) => t.stop());
        if (this._faceLandmarker) this._faceLandmarker.close();
    }
}

registry.category("view_widgets").add("face_enrollment_camera", {
    component: FaceEnrollmentCamera,
});
