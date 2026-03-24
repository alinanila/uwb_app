from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import zmq
import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .local_apps_config import (
    load_localizer_cfg, 
    load_yaml_mapping,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_LOCALIZER_CONFIG = PROJECT_ROOT / "config" / "uwb_localizer.yaml"

POSE_ENDPOINT_DEFAULT = "tcp://127.0.0.1:5561"
POSE_TOPIC = b"pose"


@dataclass
class PoseState:
    """shared state for latest pose"""
    x: Optional[float] = None
    y: Optional[float] = None
    peer_id: Optional[str] = None
    timestamp: Optional[float] = None


pose_state = PoseState()
pose_lock = threading.Lock()


def get_layout_file(config_path: Path = DEFAULT_LOCALIZER_CONFIG) -> Path:
    cfg = load_localizer_cfg(config_path)
    if cfg.layout_path is not None:
        return config_path.parent / cfg.layout_path
    return config_path


def load_layout(path: Path) -> Dict[str, Tuple[float, float]]:
    data = load_yaml_mapping(path)
    layout_in = data.get("layout", data)
    if not isinstance(layout_in, dict):
        raise ValueError("layout must be a mapping")
    anchors_in = layout_in.get("anchors", {})
    if not isinstance(anchors_in, dict):
        raise ValueError("layout.anchors must be a mapping")

    anchors: Dict[str, Tuple[float, float]] = {}
    for source_id, pos in anchors_in.items():
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            raise ValueError(f"anchor {source_id} must be [x, y]")
        anchors[str(source_id)] = (float(pos[0]), float(pos[1]))
    return anchors


def pose_listener(endpoint: str = POSE_ENDPOINT_DEFAULT, topic: bytes = POSE_TOPIC) -> None:
    """background thread: subscribe to pose ZMQ and update pose_state"""
    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.connect(endpoint)
    sub.setsockopt(zmq.SUBSCRIBE, topic)

    try:
        while True:
            parts = sub.recv_multipart()
            if len(parts) < 2:
                continue
            _, payload = parts
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            x = event.get("x_m")
            y = event.get("y_m")
            peer_id = event.get("peer_id")
            ts = event.get("timestamp")

            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue

            with pose_lock:
                pose_state.x = float(x)
                pose_state.y = float(y)
                pose_state.peer_id = str(peer_id) if peer_id is not None else None
                pose_state.timestamp = float(ts) if isinstance(ts, (int, float)) else time.time()
    except Exception as e:
        print(f"pose_listener error: {e}")
    finally:
        sub.close()
        ctx.term()


app = FastAPI(title="UWB Localisation Visualiser")


@app.on_event("startup")
def startup_event() -> None:
    # start ZMQ listener thread on startup
    t = threading.Thread(target=pose_listener, daemon=True)
    t.start()


@app.get("/api/layout")
def api_layout():
    # load anchor layout
    layout_file = get_layout_file()
    anchors = load_layout(layout_file)
    return {
        "anchors": [
            {"id": aid, "x": x, "y": y}
            for aid, (x, y) in sorted(anchors.items())
        ]
    }


@app.get("/api/pose")
def api_pose():
    # get current tag coords
    with pose_lock:
        if pose_state.x is None or pose_state.y is None:
            return {"has_pose": False}
        return {
            "has_pose": True,
            "x": pose_state.x,
            "y": pose_state.y,
            "peer_id": pose_state.peer_id,
            "timestamp": pose_state.timestamp,
        }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # HTML+JS canvas with grid, anchors, and tag
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>UWB Anchor & Tag Viewer</title>
  <style>
    body { font-family: sans-serif; margin: 1rem; }
    canvas { border: 1px solid #ccc; }
  </style>
</head>
<body>
  <h1>UWB Anchor & Tag Viewer</h1>
  <p>Grid in meters (scaled). Anchors are blue squares, tag is red circle.</p>
  <canvas id="canvas" width="1000" height="700"></canvas>
  <p id="info"></p>

  <script>
    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');
    const info = document.getElementById('info');

    let anchors = [];
    let scale = 60; // pixels per meter (increase for larger drawing)
    let margin = 20;

    function worldToCanvas(x, y) {
      // Center (0,0) in the middle of the canvas, +y up
      const cx = canvas.width / 2 + x * scale;
      const cy = canvas.height / 2 - y * scale;
      return { cx, cy };
    }

    function drawGrid(maxExtent) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = '#eee';
      ctx.lineWidth = 1;

      const step = 1; // 1 meter grid
      // Draw vertical lines from -maxExtent to +maxExtent
      for (let x = -maxExtent; x <= maxExtent; x += step) {
        const p1 = worldToCanvas(x, -maxExtent);
        const p2 = worldToCanvas(x,  maxExtent);
        ctx.beginPath();
        ctx.moveTo(p1.cx, p1.cy);
        ctx.lineTo(p2.cx, p2.cy);
        ctx.stroke();
      }
      // Draw horizontal lines
      for (let y = -maxExtent; y <= maxExtent; y += step) {
        const p1 = worldToCanvas(-maxExtent, y);
        const p2 = worldToCanvas( maxExtent, y);
        ctx.beginPath();
        ctx.moveTo(p1.cx, p1.cy);
        ctx.lineTo(p2.cx, p2.cy);
        ctx.stroke();
      }

      // Draw axes
      ctx.strokeStyle = '#ccc';
      ctx.lineWidth = 1.5;
      // x-axis
      let p1 = worldToCanvas(-maxExtent, 0);
      let p2 = worldToCanvas( maxExtent, 0);
      ctx.beginPath();
      ctx.moveTo(p1.cx, p1.cy);
      ctx.lineTo(p2.cx, p2.cy);
      ctx.stroke();
      // y-axis
      p1 = worldToCanvas(0, -maxExtent);
      p2 = worldToCanvas(0,  maxExtent);
      ctx.beginPath();
      ctx.moveTo(p1.cx, p1.cy);
      ctx.lineTo(p2.cx, p2.cy);
      ctx.stroke();
    }

    function drawAnchors() {
      ctx.fillStyle = 'blue';
      ctx.font = '12px sans-serif';
      anchors.forEach(a => {
        const { cx, cy } = worldToCanvas(a.x, a.y);
        const size = 6;
        ctx.fillRect(cx - size / 2, cy - size / 2, size, size);
        ctx.fillText(a.id, cx + 6, cy - 6);
      });
    }

    function drawTag(x, y) {
      ctx.fillStyle = 'red';
      const { cx, cy } = worldToCanvas(x, y);
      ctx.beginPath();
      ctx.arc(cx, cy, 6, 0, 2 * Math.PI);
      ctx.fill();
    }

    function computeMaxExtent(extraMargin = 1) {
      // Determine symmetric extent around origin that covers anchors (and later tag)
      let maxAbs = 1;
      anchors.forEach(a => {
        maxAbs = Math.max(maxAbs, Math.abs(a.x), Math.abs(a.y));
      });
      return maxAbs + extraMargin;
    }

    async function loadLayout() {
      const res = await fetch('/api/layout');
      const data = await res.json();
      anchors = data.anchors || [];
      const maxExtent = computeMaxExtent();
      drawGrid(maxExtent);
      drawAnchors();
    }

    async function pollPose() {
      const res = await fetch('/api/pose');
      const data = await res.json();
      if (data.has_pose) {
        // Include tag in extents
        let maxAbs = computeMaxExtent();
        maxAbs = Math.max(maxAbs, Math.abs(data.x), Math.abs(data.y)) + 1;

        drawGrid(maxAbs);
        drawAnchors();
        drawTag(data.x, data.y);
        info.textContent = `Tag ${data.peer_id || ''} at x=${data.x.toFixed(2)} m, y=${data.y.toFixed(2)} m`;
      } else {
        info.textContent = 'No pose yet.';
      }
    }

    loadLayout();
    setInterval(pollPose, 200); // poll every 200 ms
  </script>
</body>
</html>
    """