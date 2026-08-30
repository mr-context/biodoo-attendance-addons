/** @odoo-module **/
import { Component, useState, useRef, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const MP_BASE   = "/zkteco_connector/static/lib/mediapipe";
const MP_BUNDLE = `${MP_BASE}/vision_bundle.js`;
const MP_WASM   = `${MP_BASE}/wasm`;
const MP_MODEL  = `${MP_BASE}/blaze_face_short_range.tflite`;

class FaceCaptureField extends Component {
    static template = "zkteco_connector.FaceCaptureField";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({
            streaming: false,
            captured: Boolean(this.props.record.data[this.props.name]),
            error: "",
            faceDetected: false,
            loadingDetector: false,
            detectorAvailable: false,
        });
        this.videoRef   = useRef("video");
        this.canvasRef  = useRef("canvas");
        this.overlayRef = useRef("overlay");
        this.stream    = null;
        this._detector = null;
        this._rafId    = null;
        this._lastFace = null;   // dernière bbox visage détectée (px vidéo natifs)

        onWillUnmount(() => this._stopStream());
    }

    async startCamera() {
        this.state.error = "";
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
                audio: false,
            });
            const video = this.videoRef.el;
            video.srcObject = this.stream;
            await video.play();
            this.state.streaming = true;
            await this._ensureDetector();
            this._startDetectionLoop();
        } catch (err) {
            this.state.error = `Impossible d'accéder à la caméra : ${err.message}`;
        }
    }

    async _ensureDetector() {
        if (this._detector) return;
        this.state.loadingDetector = true;
        try {
            const vision = await import(MP_BUNDLE);
            const { FaceDetector, FilesetResolver } = vision;
            const fileset = await FilesetResolver.forVisionTasks(MP_WASM);
            this._detector = await FaceDetector.createFromOptions(fileset, {
                baseOptions: { modelAssetPath: MP_MODEL },
                runningMode: "VIDEO",
                minDetectionConfidence: 0.5,
            });
            this.state.detectorAvailable = true;
        } catch (err) {
            // L'overlay reste désactivé ; la capture fonctionne quand même.
            console.warn("[zkteco] MediaPipe FaceDetector indisponible:", err);
            this.state.detectorAvailable = false;
        } finally {
            this.state.loadingDetector = false;
        }
    }

    _startDetectionLoop() {
        if (!this._detector) return;
        const video = this.videoRef.el;
        let lastTs = -1;

        const loop = () => {
            if (!this.state.streaming || !this._detector) return;
            if (video && video.readyState >= 2) {
                const ts = performance.now();
                if (ts !== lastTs) {
                    lastTs = ts;
                    try {
                        const res = this._detector.detectForVideo(video, ts);
                        let dets = (res && res.detections) || [];
                        // garde le plus grand visage
                        if (dets.length > 1) {
                            dets = [dets.reduce((a, b) =>
                                (b.boundingBox.width * b.boundingBox.height >
                                 a.boundingBox.width * a.boundingBox.height) ? b : a)];
                        }
                        this.state.faceDetected = dets.length > 0;
                        const bb = dets[0] && dets[0].boundingBox;
                        this._lastFace = bb
                            ? { x: bb.originX, y: bb.originY, w: bb.width, h: bb.height }
                            : null;
                        this._drawOverlay(dets);
                    } catch {
                        // frame momentanément indisponible
                    }
                }
            }
            this._rafId = requestAnimationFrame(loop);
        };
        this._rafId = requestAnimationFrame(loop);
    }

    _drawOverlay(detections) {
        const video  = this.videoRef.el;
        const canvas = this.overlayRef.el;
        if (!canvas || !video) return;

        if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
            canvas.width  = video.videoWidth;
            canvas.height = video.videoHeight;
        }
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        for (const det of detections) {
            const bb = det.boundingBox;
            if (!bb) continue;
            const pad = Math.round(Math.max(bb.width, bb.height) * 0.18);
            const rx = bb.originX - pad;
            const ry = bb.originY - pad;
            const rw = bb.width  + pad * 2;
            const rh = bb.height + pad * 2;

            const len = Math.round(Math.min(rw, rh) * 0.24);  // longueur des branches
            const r   = Math.round(Math.min(rw, rh) * 0.10);  // rayon d'arrondi

            ctx.strokeStyle = "#34d399";
            ctx.lineWidth   = 4;
            ctx.lineCap     = "round";
            ctx.lineJoin    = "round";
            ctx.shadowColor = "rgba(52,211,153,0.7)";
            ctx.shadowBlur  = 14;

            const corner = (cx, cy, hx, vy) => {
                ctx.beginPath();
                ctx.moveTo(cx, cy + vy);
                ctx.arcTo(cx, cy, cx + hx, cy, r);
                ctx.lineTo(cx + hx, cy);
                ctx.stroke();
            };
            corner(rx,       ry,        len,  len);   // haut-gauche
            corner(rx + rw,  ry,       -len,  len);   // haut-droit
            corner(rx,       ry + rh,   len, -len);   // bas-gauche
            corner(rx + rw,  ry + rh,  -len, -len);   // bas-droit

            ctx.shadowBlur = 0;
        }
    }

    capture() {
        const video  = this.videoRef.el;
        const canvas = this.canvasRef.el;
        const vw = video.videoWidth, vh = video.videoHeight;

        // Zone source : crop sur le visage détecté (marge 35 %), sinon image entière.
        let sx = 0, sy = 0, sw = vw, sh = vh;
        if (this._lastFace) {
            const f = this._lastFace;
            const mx = f.w * 0.35, my = f.h * 0.35;
            sx = Math.max(0, Math.round(f.x - mx));
            sy = Math.max(0, Math.round(f.y - my));
            sw = Math.min(vw - sx, Math.round(f.w + mx * 2));
            sh = Math.min(vh - sy, Math.round(f.h + my * 2));
        }

        // Downscale pour rester léger (côté le plus grand ≤ 480 px).
        const scale = Math.min(1, 480 / Math.max(sw, sh));
        const dw = Math.round(sw * scale);
        const dh = Math.round(sh * scale);
        canvas.width  = dw;
        canvas.height = dh;
        canvas.getContext("2d").drawImage(video, sx, sy, sw, sh, 0, 0, dw, dh);

        const base64 = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
        this.props.record.update({ [this.props.name]: base64 });
        this.state.captured = true;
        this._stopStream();
    }

    stopCamera() { this._stopStream(); }

    retake() {
        this.props.record.update({ [this.props.name]: false });
        this.state.captured     = false;
        this.state.faceDetected = false;
        this.startCamera();
    }

    _stopStream() {
        if (this._rafId) {
            cancelAnimationFrame(this._rafId);
            this._rafId = null;
        }
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
        this.state.streaming    = false;
        this.state.faceDetected = false;
        const ov = this.overlayRef && this.overlayRef.el;
        if (ov) ov.getContext("2d").clearRect(0, 0, ov.width, ov.height);
    }
}

registry.category("fields").add("face_capture", {
    component: FaceCaptureField,
    supportedTypes: ["char"],
});