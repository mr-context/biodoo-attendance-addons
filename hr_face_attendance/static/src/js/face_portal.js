/**
 * Portail pointage facial.
 *
 * Anti-spoofing assuré côté serveur par MiniFASNet ONNX (analyse texture/relief 2D vs 3D).
 * Le JS se contente de détecter un visage stable 800ms puis l'envoie au serveur.
 */
(function () {
    "use strict";

    // ── Durée de stabilité requise avant capture (ms) ────────────────────────
    const STABLE_MS = 800;

    // ── État ─────────────────────────────────────────────────────────────────
    let stream         = null;
    let faceLandmarker = null;
    let rafId          = null;
    let isSending      = false;
    let gpsLat         = null;
    let gpsLon         = null;
    let stableStart    = null;

    // ── DOM ──────────────────────────────────────────────────────────────────
    const video    = document.getElementById("face_video");
    const canvas   = document.getElementById("face_canvas");
    const overlay  = document.getElementById("face_overlay");
    const result   = document.getElementById("face_result");
    const scanning = document.getElementById("face_scanning");
    const config   = document.getElementById("face_config");

    if (!video || !config) return;

    const geofenceEnabled = config.dataset.geofence === "1";

    // ── Messages d'erreur ────────────────────────────────────────────────────
    const ERROR_LABELS = {
        not_authorized: "Pointage facial non autorisé pour votre compte. Contactez les RH.",
        not_enrolled:   "Votre visage n'est pas enregistré. Contactez les RH.",
        no_face:        "Aucun visage détecté. Approchez-vous de la caméra.",
        spoof_detected: "Fraude détectée. Utilisez votre vrai visage.",
        face_mismatch:  "Identité non reconnue.",
        out_of_range:   (d, r) => `Vous êtes à ${d}m du site (max ${r}m).`,
        gps_required:   "La géolocalisation est requise. Autorisez l'accès GPS.",
        service_error:  "Erreur interne. Réessayez dans quelques instants.",
        no_employee:    "Compte non lié à une fiche employé.",
    };

    // ── GPS ──────────────────────────────────────────────────────────────────
    function haversineJS(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
        const dp = (lat2 - lat1) * Math.PI / 180;
        const dl = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function updateGpsUI(lat, lon) {
        const dot  = document.getElementById('gps_dot');
        const text = document.getElementById('gps_text');
        const bar  = document.getElementById('geofence_bar');
        const barWrap = document.getElementById('geofence_bar_wrap');
        if (!dot || !text) return;

        const siteLat = parseFloat(config.dataset.lat);
        const siteLon = parseFloat(config.dataset.lon);
        const radius  = parseFloat(config.dataset.radius) || 200;

        // Toujours afficher la position GPS
        dot.className = 'gps-dot gps-ok';
        text.textContent = `GPS actif — ${lat.toFixed(5)}, ${lon.toFixed(5)}`;

        // Barre de géofence uniquement si site configuré
        if (siteLat && geofenceEnabled) {
            const dist   = Math.round(haversineJS(lat, lon, siteLat, siteLon));
            const inZone = dist <= radius;
            const pct    = Math.min(100, Math.round((dist / radius) * 100));

            dot.className = 'gps-dot ' + (inZone ? 'gps-ok' : (dist < radius * 1.5 ? 'gps-warning' : 'gps-error'));
            text.textContent = inZone
                ? `Dans le périmètre — ${dist}m`
                : `Hors périmètre — ${dist}m (max ${radius}m)`;

            if (bar && barWrap) {
                barWrap.style.display = '';
                bar.style.width  = pct + '%';
                bar.style.background = inZone ? '#198754' : (dist < radius * 1.5 ? '#ffc107' : '#dc3545');
            }
        }
    }

    function startGPS() {
        if (!navigator.geolocation) return;
        navigator.geolocation.watchPosition(
            (pos) => {
                gpsLat = pos.coords.latitude;
                gpsLon = pos.coords.longitude;
                updateGpsUI(gpsLat, gpsLon);
            },
            (err) => console.warn("GPS:", err.message),
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    // ── Caméra ───────────────────────────────────────────────────────────────
    function stopCamera() {
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
        if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
        video.srcObject = null;
        video.classList.remove("scanning", "success", "error");
        showScanning(false);
        clearOverlay();
    }

    async function startCamera() {
        stableStart = null;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "user", width: 640, height: 480 },
            });
            video.srcObject = stream;
            await video.play();
            video.classList.add("scanning");
            showStatus('<i class="fa fa-camera me-1"/> Initialisation...');
            if (!faceLandmarker) await loadMediaPipe();
            showStatus('<i class="fa fa-user me-1"/> Regardez la caméra...');
            startLoop();
        } catch (e) {
            showError("Impossible d'accéder à la caméra : " + e.message);
        }
    }

    // ── MediaPipe ────────────────────────────────────────────────────────────
    async function loadMediaPipe() {
        try {
            const vision = await import(
                "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/+esm"
            );
            const filesetResolver = await vision.FilesetResolver.forVisionTasks(
                "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.3/wasm"
            );
            faceLandmarker = await vision.FaceLandmarker.createFromOptions(filesetResolver, {
                baseOptions: {
                    modelAssetPath:
                        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
                    delegate: "CPU",
                },
                runningMode: "VIDEO",
                numFaces: 1,
                outputFaceBlendshapes: false,
                outputFacialTransformationMatrixes: false,
            });
        } catch (e) {
            showError("Chargement détection échoué. Vérifiez la connexion.");
            throw e;
        }
    }

    // ── Landmarks overlay ────────────────────────────────────────────────────
    function drawLandmarks(landmarks) {
        if (!overlay) return;
        // Synchronise la résolution interne du canvas avec sa taille affichée
        if (overlay.width !== overlay.offsetWidth || overlay.height !== overlay.offsetHeight) {
            overlay.width  = overlay.offsetWidth;
            overlay.height = overlay.offsetHeight;
        }
        const ctx = overlay.getContext("2d");
        ctx.clearRect(0, 0, overlay.width, overlay.height);
        ctx.fillStyle = "rgba(255,255,255,0.6)";
        for (const lm of landmarks) {
            // Pas de CSS scaleX(-1) sur la vidéo portail → pas de miroir
            const x = lm.x * overlay.width;
            const y = lm.y * overlay.height;
            ctx.beginPath();
            ctx.arc(x, y, 1.3, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    function clearOverlay() {
        if (!overlay) return;
        overlay.getContext("2d").clearRect(0, 0, overlay.width, overlay.height);
    }

    // ── Boucle analyse ───────────────────────────────────────────────────────
    function startLoop() {
        function loop() {
            rafId = requestAnimationFrame(loop);
            if (!faceLandmarker || video.readyState < 2 || isSending) return;

            const res = faceLandmarker.detectForVideo(video, Date.now());
            const faceDetected = res.faceLandmarks && res.faceLandmarks.length > 0;

            if (!faceDetected) {
                stableStart = null;
                clearOverlay();
                return;
            }

            drawLandmarks(res.faceLandmarks[0]);

            if (!stableStart) {
                stableStart = Date.now();
                return;
            }

            const elapsed = Date.now() - stableStart;
            const pct = Math.min(100, Math.round((elapsed / STABLE_MS) * 100));
            showStatus(`<i class="fa fa-user me-1"/> Analyse... ${pct}%`);

            if (elapsed >= STABLE_MS) {
                stableStart = null;
                captureAndSend();
            }
        }
        rafId = requestAnimationFrame(loop);
    }

    // ── Capture et envoi ─────────────────────────────────────────────────────
    async function captureAndSend() {
        if (video.readyState < 2) return;
        isSending = true;
        showStatus('<i class="fa fa-lock me-1"/> Vérification identité...');
        try {
            canvas.width  = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext("2d").drawImage(video, 0, 0);
            const imageData = canvas.toDataURL("image/jpeg", 0.8);

            const resp = await fetch("/my/attendance/checkin", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    jsonrpc: "2.0",
                    method: "call",
                    params: { image_data: imageData, latitude: gpsLat, longitude: gpsLon },
                }),
            });
            const data = await resp.json();
            handleResult(data.result || data);
        } catch (e) {
            console.error("Checkin error:", e);
            resetScan();
        } finally {
            isSending = false;
        }
    }

    // ── Traitement résultat ──────────────────────────────────────────────────
    function handleResult(res) {
        if (!res) return;
        if (res.error === 'retry') {
            // Frame de mauvaise qualité → réessayer silencieusement
            resetScan();
            return;
        }
        if (res.success) {
            stopCamera();
            const actionLabel = res.action === "check_in" ? "Entrée" : "Sortie";
            const hours = res.hours_today > 0
                ? ` — ${Math.floor(res.hours_today)}h${String(Math.round((res.hours_today % 1) * 60)).padStart(2, "0")} aujourd'hui`
                : "";
            showSuccess(`✅ ${actionLabel} enregistrée — ${res.employee_name}${hours}`);
            showRetryButton();
        } else {
            const error = res.error || "unknown";
            const msg = error === "out_of_range"
                ? ERROR_LABELS.out_of_range(res.distance, res.max_distance)
                : (ERROR_LABELS[error] || `Erreur : ${error}`);
            showError(msg);
            setTimeout(() => resetScan(), 4000);
        }
    }

    function resetScan() {
        stableStart = null;
        showStatus('<i class="fa fa-user me-1"/> Regardez la caméra...');
    }

    // ── Affichage ────────────────────────────────────────────────────────────
    function showStatus(html) {
        if (!scanning) return;
        scanning.classList.remove("d-none");
        scanning.innerHTML = html;
    }

    function showSuccess(msg) {
        if (!result) return;
        result.className = "alert alert-success";
        result.textContent = msg;
        showScanning(false);
    }

    function showError(msg) {
        if (!result) return;
        result.className = "alert alert-danger";
        result.textContent = msg;
        video.classList.remove("scanning");
        video.classList.add("error");
        setTimeout(() => {
            video.classList.remove("error");
            video.classList.add("scanning");
            clearResult();
        }, 4000);
    }

    function clearResult() {
        if (!result) return;
        result.className = "alert d-none";
        result.textContent = "";
    }

    function showScanning(show) {
        if (!scanning) return;
        scanning.classList.toggle("d-none", !show);
    }

    function showRetryButton() {
        const btn = document.getElementById("face_retry_btn");
        if (btn) btn.classList.remove("d-none");
    }

    function hideRetryButton() {
        const btn = document.getElementById("face_retry_btn");
        if (btn) btn.classList.add("d-none");
    }

    // ── Démarrage ────────────────────────────────────────────────────────────
    function init() {
        startGPS();
        startCamera();
        const retryBtn = document.getElementById("face_retry_btn");
        if (retryBtn) {
            retryBtn.addEventListener("click", () => {
                clearResult();
                hideRetryButton();
                startCamera();
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
