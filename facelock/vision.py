"""Camera access, face detection/recognition and the liveness check."""

import threading
import time
from datetime import datetime

import cv2
import numpy as np

from . import config

LIVE_SCORE_FLOOR = 0.30   # frames below this similarity don't count toward the head-turn check
YAW_RANGE_NEEDED = 0.20   # how far (nose offset / eye distance) the head must swing overall
MATCH_FRAMES_NEEDED = 3
MIN_FACE_PX = 70
KEEP_WARM_SECONDS = 8     # keep the webcam open briefly so back-to-back prompts start instantly

# Scanner states shown by the UI ring
SEARCH, SCAN, TURN, FAIL, OK, OFF = "search", "scan", "turn", "fail", "ok", "off"


class CameraHub:
    """Shares one webcam between the lock screen, prompts and the presence monitor."""

    def __init__(self, index):
        self.index = index
        self._cap = None
        self._refs = 0
        self._lock = threading.Lock()
        self._close_timer = None

    def acquire(self):
        with self._lock:
            if self._close_timer:
                self._close_timer.cancel()
                self._close_timer = None
            if self._cap is None:
                cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(self.index)
                if not cap.isOpened():
                    return False
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._cap = cap
            self._refs += 1
            return True

    def read(self):
        with self._lock:
            if self._cap is None:
                return None
            ok, frame = self._cap.read()
            return frame if ok else None

    def release(self):
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0 and self._cap is not None and not self._close_timer:
                self._close_timer = threading.Timer(KEEP_WARM_SECONDS, self._close_if_idle)
                self._close_timer.daemon = True
                self._close_timer.start()

    def _close_if_idle(self):
        with self._lock:
            self._close_timer = None
            if self._refs == 0 and self._cap is not None:
                self._cap.release()
                self._cap = None

    def set_index(self, index):
        with self._lock:
            self.index = index
            if self._cap is not None:
                self._cap.release()
                self._cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)


def probe_cameras(max_index=4):
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened() and cap.read()[0]:
            found.append(i)
        cap.release()
    return found


_GAMMA_LUT = np.array([((i / 255.0) ** 0.55) * 255 for i in range(256)], np.uint8)
_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def enhance(frame):
    """Brighten dark frames so detection and matching keep working in dim rooms."""
    gray_mean = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
    if gray_mean >= 85:
        return frame
    frame = cv2.LUT(frame, _GAMMA_LUT)
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = _CLAHE.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


class FaceEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.detector = cv2.FaceDetectorYN.create(str(config.DETECTOR_MODEL), "", (320, 320), 0.85, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(config.RECOGNIZER_MODEL), "")
        self.templates = config.load_faces()

    @property
    def enrolled(self):
        return len(self.templates) > 0

    def set_templates(self, embeddings, append=False):
        embeddings = np.asarray(embeddings, np.float32).reshape(-1, 128)
        if append and self.enrolled:
            embeddings = np.vstack([self.templates, embeddings])
        config.save_faces(embeddings)
        self.templates = embeddings

    def delete_templates(self):
        config.FACES_PATH.unlink(missing_ok=True)
        self.templates = np.zeros((0, 128), np.float32)

    def prepare(self, frame):
        return enhance(frame) if self.cfg["low_light_boost"] else frame

    def detect(self, frame):
        """Return the largest face row (15 floats) or None."""
        h, w = frame.shape[:2]
        with self._lock:
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)
        if faces is None or len(faces) == 0:
            return None
        return max(faces, key=lambda f: f[2] * f[3])

    def embed(self, frame, face):
        with self._lock:
            aligned = self.recognizer.alignCrop(frame, face)
            feat = self.recognizer.feature(aligned).flatten().astype(np.float32)
        return feat / (np.linalg.norm(feat) + 1e-9)

    def similarity(self, feat):
        if not self.enrolled:
            return 0.0
        t = self.templates / (np.linalg.norm(self.templates, axis=1, keepdims=True) + 1e-9)
        return float(np.max(t @ feat))

    def is_owner(self, frame):
        frame = self.prepare(frame)
        face = self.detect(frame)
        if face is None or face[2] < MIN_FACE_PX:
            return False
        return self.similarity(self.embed(frame, face)) >= self.cfg["match_threshold"]


def yaw(face):
    """Signed horizontal head turn: nose offset from eye midpoint, in eye-distances."""
    rx, ry, lx, ly, nx = face[4], face[5], face[6], face[7], face[8]
    eye_dist = max(np.hypot(lx - rx, ly - ry), 1.0)
    return (nx - (rx + lx) / 2) / eye_dist


def face_center(frame, face):
    """Face centre as fractions of the frame (for centring the circular preview)."""
    h, w = frame.shape[:2]
    return ((face[0] + face[2] / 2) / w, (face[1] + face[3] / 2) / h, face[2] / w)


class Verifier:
    """Frame processor: succeeds when the owner's face matches and (optionally) passes liveness."""

    def __init__(self, engine, cfg, context=""):
        self.engine = engine
        self.cfg = cfg
        self.context = context
        self.matches = 0
        self.yaws = []
        self.stranger_frames = 0
        self.intruder_saved = False
        self.state = SEARCH
        self.progress = 0.0
        self.focus = None

    def __call__(self, frame):
        frame = self.engine.prepare(frame)
        face = self.engine.detect(frame)
        if face is None:
            self.matches = max(0, self.matches - 1)
            self.state, self.progress, self.focus = SEARCH, 0.0, None
            return "Looking for your face…", False
        self.focus = face_center(frame, face)
        if face[2] < MIN_FACE_PX:
            self.state = SEARCH
            return "Move a little closer", False

        score = self.engine.similarity(self.engine.embed(frame, face))
        matched = score >= self.cfg["match_threshold"]
        if score >= LIVE_SCORE_FLOOR:
            self.yaws = (self.yaws + [yaw(face)])[-60:]
        self.matches = self.matches + 1 if matched else max(0, self.matches - 1)

        if score < 0.25:
            self.stranger_frames += 1
            if self.stranger_frames == 15:
                self._save_intruder(frame)
        else:
            self.stranger_frames = 0

        need_live = self.cfg["require_liveness"]
        live = (max(self.yaws) - min(self.yaws)) / YAW_RANGE_NEEDED if len(self.yaws) >= 4 else 0.0
        id_part = min(self.matches / MATCH_FRAMES_NEEDED, 1.0)
        self.progress = (id_part * 0.5 + min(live, 1.0) * 0.5) if need_live else id_part

        if self.matches < MATCH_FRAMES_NEEDED:
            self.state = SCAN if matched else FAIL
            return ("Recognizing…" if matched else "Face not recognized"), False
        if need_live and live < 1.0:
            self.state = TURN
            return "Slowly turn your head left and right", False
        self.state, self.progress = OK, 1.0
        return "Welcome back", True

    def _save_intruder(self, frame):
        if self.intruder_saved or not self.cfg["save_intruders"] or not self.engine.enrolled:
            return
        self.intruder_saved = True
        config.INTRUDER_DIR.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("intruder_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(str(config.INTRUDER_DIR / name), frame)
        config.log_event("intruder", f"Unknown face tried to unlock {self.context}".strip(), ok=False,
                         photo=name)


class Enroller:
    """Frame processor: collects embeddings across frontal, left and right poses."""

    QUOTA = {"center": 6, "left": 4, "right": 4}

    def __init__(self, engine):
        self.engine = engine
        self.samples = {"center": [], "left": [], "right": []}
        self.last_sample = 0.0
        self.state = SEARCH
        self.focus = None

    @property
    def progress(self):
        got = sum(min(len(self.samples[b]), n) for b, n in self.QUOTA.items())
        return got / sum(self.QUOTA.values())

    def _full(self, pose):
        return len(self.samples[pose]) >= self.QUOTA[pose]

    def _instruction(self):
        if not self._full("center"):
            return "Look straight at the camera"
        if self._full("left") and self._full("right"):
            return None
        if self._full("left") or self._full("right"):
            return "Now slowly turn to the other side"
        return "Slowly turn your head to one side"

    def __call__(self, frame):
        frame = self.engine.prepare(frame)
        face = self.engine.detect(frame)
        if face is None:
            self.state, self.focus = SEARCH, None
            return "Looking for your face…", False
        self.focus = face_center(frame, face)
        if face[2] < MIN_FACE_PX * 1.3:
            self.state = SEARCH
            return "Move a little closer", False
        self.state = SCAN
        y = yaw(face)
        pose = "center" if abs(y) < 0.08 else ("left" if y < 0 else "right")
        # Collect frontal samples first, then either side in whatever order the user turns.
        wanted = (pose == "center") if not self._full("center") else (pose != "center")
        now = time.monotonic()
        if wanted and not self._full(pose) and now - self.last_sample > 0.35:
            self.samples[pose].append(self.engine.embed(frame, face))
            self.last_sample = now
        text = self._instruction()
        if text is None:
            self.state = OK
        return (text or "All set!"), text is None

    def embeddings(self):
        return [e for b in self.samples.values() for e in b]


class VisionWorker(threading.Thread):
    """Reads frames and feeds them to a processor in the background; the UI polls `latest`."""

    def __init__(self, hub, processor):
        super().__init__(daemon=True)
        self.hub = hub
        self.processor = processor
        self.latest = None        # (rgb_frame or None, status)
        self.finished = False
        self.camera_failed = False
        self._stop = threading.Event()

    def run(self):
        if not self.hub.acquire():
            self.camera_failed = True
            self.latest = (None, "Camera not available — use your PIN")
            return
        try:
            misses = 0
            while not self._stop.is_set():
                frame = self.hub.read()
                if frame is None:
                    misses += 1
                    if misses > 50:
                        self.camera_failed = True
                        self.latest = (None, "Camera not responding — use your PIN")
                    time.sleep(0.05)
                    continue
                misses = 0
                status, done = self.processor(frame)
                rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
                self.latest = (rgb, status)
                if done:
                    self.finished = True
                    break
        finally:
            self.hub.release()

    def stop(self):
        self._stop.set()
