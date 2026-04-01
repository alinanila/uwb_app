from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import zmq
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import subprocess

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
    # z: Optional[float] = None
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

# if 3d
# def load_layout(path: Path) -> Dict[str, Tuple[float, float, float]]:
#     data = load_yaml_mapping(path)
#     layout_in = data.get("layout", data)
#     if not isinstance(layout_in, dict):
#         raise ValueError("layout must be a mapping")
#     anchors_in = layout_in.get("anchors", {})
#     if not isinstance(anchors_in, dict):
#         raise ValueError("layout.anchors must be a mapping")

#     anchors: Dict[str, Tuple[float, float, float]] = {}
#     for source_id, pos in anchors_in.items():
#         if not isinstance(pos, (list, tuple)) or len(pos) not in (2, 3):
#             raise ValueError(f"anchor {source_id} must be [x, y] or [x, y, z]")
#         x, y = float(pos[0]), float(pos[1])
#         z = float(pos[2]) if len(pos) == 3 else 0.0
#         anchors[str(source_id)] = (x, y, z)
#     return anchors


def save_layout(path: Path, anchors: Dict[str, Tuple[float, float]]) -> None:
    data = load_yaml_mapping(path)
    layout = data.setdefault("layout", {})
    if not isinstance(layout, dict):
        raise ValueError(f"layout in {path} must be a mapping")

    anchors_out = layout.get("anchors")
    if not isinstance(anchors_out, dict):
        anchors_out = {}
        layout["anchors"] = anchors_out

    for source_id, (x, y) in anchors.items():
        anchors_out[source_id] = [float(x), float(y)]

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)

# if 3d
# def load_layout(path: Path) -> Dict[str, Tuple[float, float, float]]:
#     data = load_yaml_mapping(path)
#     layout_in = data.get("layout", data)
#     if not isinstance(layout_in, dict):
#         raise ValueError("layout must be a mapping")
#     anchors_in = layout_in.get("anchors", {})
#     if not isinstance(anchors_in, dict):
#         raise ValueError("layout.anchors must be a mapping")

#     anchors: Dict[str, Tuple[float, float, float]] = {}
#     for source_id, pos in anchors_in.items():
#         if not isinstance(pos, (list, tuple)) or len(pos) not in (2, 3):
#             raise ValueError(f"anchor {source_id} must be [x, y] or [x, y, z]")
#         x, y = float(pos[0]), float(pos[1])
#         z = float(pos[2]) if len(pos) == 3 else 0.0
#         anchors[str(source_id)] = (x, y, z)
#     return anchors


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
            # z = event.get("z_m")
            peer_id = event.get("peer_id")
            ts = event.get("timestamp")

            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue

            with pose_lock:
                pose_state.x = float(x)
                pose_state.y = float(y)
                # pose_state.z = float(z) if isinstance(z, (int, float)) else None
                pose_state.peer_id = str(peer_id) if peer_id is not None else None
                pose_state.timestamp = float(ts) if isinstance(ts, (int, float)) else time.time()
    except Exception as e:
        print(f"pose_listener error: {e}")
    finally:
        sub.close()
        ctx.term()


def restart_localizer_service() -> None:
    """
    restart uwb-localize service with new layout
    assumes the app and the service are running on the same host
    """
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "uwb-localize"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"failed to restart uwb-localize: {e}")


app = FastAPI(title="UWB Localisation Visualiser")


@app.on_event("startup")
def startup_event() -> None:
    # start ZMQ listener thread on startup
    t = threading.Thread(target=pose_listener, daemon=True)
    t.start()


class Anchor(BaseModel):
    id: str
    x: float
    y: float
    # z: float = 0.0


class LayoutUpdate(BaseModel):
    anchors: List[Anchor]


@app.get("/api/layout")
def api_layout():
    # load anchor layout
    layout_file = get_layout_file()
    anchors = load_layout(layout_file)
    return LayoutUpdate(
        anchors = [
            Anchor(id=aid, x=pos[0], y=pos[1])
            # Anchor(id=aid, x=pos[0], y=pos[1], z=pos[2])
            for aid, pos in sorted(anchors.items())
        ]
    )


@app.post("/api/layout")
def api_update_layout(update: LayoutUpdate):
    layout_file = get_layout_file()
    anchors = {a.id: (a.x, a.y) for a in update.anchors}
    # anchors = {a.id: (a.x, a.y, a.z) for a in update.anchors}
    save_layout(layout_file, anchors)

    # restart uwb-localize with new layout
    restart_localizer_service()

    return {"status": "ok"}


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
            # "z": pose_state.z,
            "peer_id": pose_state.peer_id,
            "timestamp": pose_state.timestamp,
        }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # Single page: anchor editor + visualization
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>UWB Anchor & Tag Viewer</title>
  <style>
    body { font-family: sans-serif; margin: 1rem; display: flex; flex-direction: column; gap: 1rem; }
    #top { display: flex; gap: 2rem; }
    #anchors { border-collapse: collapse; }
    #anchors th, #anchors td { border: 1px solid #ccc; padding: 0.3rem 0.6rem; }
    #anchors input[type="number"] { width: 5rem; }
    canvas { border: 1px solid #ccc; }
    button { padding: 0.3rem 0.8rem; margin-top: 0.5rem; }
  </style>
</head>
<body>
  <h1>UWB Anchor &amp; Tag Viewer</h1>
  <div id="top">
    <div>
      <h2>Anchor Layout</h2>
      <table id="anchors">
        <thead>
          <tr><th>Anchor ID</th><th>X (m)</th><th>Y (m)</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <button onclick="saveLayout()">Save Layout</button>
      <p id="status"></p>
    </div>
    <div>
      <h2>Visualization</h2>
      <canvas id="canvas" width="800" height="600"></canvas>
      <p id="info"></p>
    </div>
  </div>

  <script>
    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');
    const info = document.getElementById('info');
    const statusEl = document.getElementById('status') || { textContent: "" };

    let anchors = [];
    let scale = 60; // pixels per meter (will be recomputed dynamically)
    let currentBounds = null;  
    const margin = 40; // pixels around the drawing

    function worldToCanvas(x, y) {
      // Center the view on the middle of the bounds
      const centerX = (currentBounds.minX + currentBounds.maxX) / 2;
      const centerY = (currentBounds.minY + currentBounds.maxY) / 2;

      const cx = canvas.width / 2 + (x - centerX) * scale;
      const cy = canvas.height / 2 - (y - centerY) * scale;
      return { cx, cy };
    }

    function computeBounds(includeTag) {
      // Compute min/max x,y over anchors (and optionally tag)
      let minX = 0, maxX = 0, minY = 0, maxY = 0;
      let initialized = false;

      anchors.forEach(a => {
        if (!initialized) {
          minX = maxX = a.x;
          minY = maxY = a.y;
          initialized = true;
        } else {
          minX = Math.min(minX, a.x);
          maxX = Math.max(maxX, a.x);
          minY = Math.min(minY, a.y);
          maxY = Math.max(maxY, a.y);
        }
      });

      if (!initialized) {
        // No anchors; default small bounds
        minX = -1; maxX = 1; minY = -1; maxY = 1;
      }

      if (includeTag && lastPose.has_pose) {
        const x = lastPose.x;
        const y = lastPose.y;
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }

      return { minX, maxX, minY, maxY };
    }

    function computeScale(bounds) {
      const { minX, maxX, minY, maxY } = bounds;

      // World width/height in meters
      const width_m  = maxX - minX;
      const height_m = maxY - minY;

      // Add padding
      const padded_width_m  = width_m * 1.2;
      const padded_height_m = height_m * 1.2;

      // Available pixels (minus margins)
      const width_px  = canvas.width  - 2 * margin;
      const height_px = canvas.height - 2 * margin;

      // Choose scale so that both dimensions fit
      const sx = width_px  / padded_width_m;
      const sy = height_px / padded_height_m;

      // Use the smaller scale to fit both directions
      let s = Math.min(sx, sy);

      // Clamp scale to reasonable range
      const MIN_SCALE = 10;   // zoomed out
      const MAX_SCALE = 500;  // zoomed in
      s = Math.max(MIN_SCALE, Math.min(MAX_SCALE, s));

      return s;
    }

    function drawGrid(bounds) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const { minX, maxX, minY, maxY } = bounds;
      const maxExtent = Math.max(Math.abs(minX), Math.abs(maxX), Math.abs(minY), Math.abs(maxY)) + 1;

      ctx.strokeStyle = '#eee';
      ctx.lineWidth = 1;

      const step = 1; // 1 meter grid
      for (let x = -maxExtent; x <= maxExtent; x += step) {
        const p1 = worldToCanvas(x, -maxExtent);
        const p2 = worldToCanvas(x,  maxExtent);
        ctx.beginPath();
        ctx.moveTo(p1.cx, p1.cy);
        ctx.lineTo(p2.cx, p2.cy);
        ctx.stroke();
      }
      for (let y = -maxExtent; y <= maxExtent; y += step) {
        const p1 = worldToCanvas(-maxExtent, y);
        const p2 = worldToCanvas( maxExtent, y);
        ctx.beginPath();
        ctx.moveTo(p1.cx, p1.cy);
        ctx.lineTo(p2.cx, p2.cy);
        ctx.stroke();
      }

      // Axes
      ctx.strokeStyle = '#ccc';
      ctx.lineWidth = 1.5;
      let p1 = worldToCanvas(-maxExtent, 0);
      let p2 = worldToCanvas( maxExtent, 0);
      ctx.beginPath();
      ctx.moveTo(p1.cx, p1.cy);
      ctx.lineTo(p2.cx, p2.cy);
      ctx.stroke();
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

    function computeAndDraw(includeTag) {
      currentBounds = computeBounds(includeTag);
      scale = computeScale(currentBounds);
      drawGrid(currentBounds);
      drawAnchors();
      if (includeTag && lastPose.has_pose) {
        drawTag(lastPose.x, lastPose.y);
      }
    }

    let lastPose = { has_pose: false, x: 0, y: 0, peer_id: null };

    async function loadLayout() {
      const res = await fetch('/api/layout');
      if (!res.ok) {
        statusEl.textContent = 'Error loading layout';
        return;
      }
      const data = await res.json();
      anchors = data.anchors || [];
      statusEl.textContent = '';
      computeAndDraw(false);
    }

    async function saveLayout() {
      const inputs = document.querySelectorAll('#anchors input');
      const anchorsMap = {};
      inputs.forEach(input => {
        const id = input.dataset.anchorId;
        const coord = input.dataset.coord;
        const value = parseFloat(input.value);
        if (!anchorsMap[id]) {
          anchorsMap[id] = {id: id, x: 0, y: 0};
        }
        anchorsMap[id][coord] = value;
      });
      const anchorsArr = Object.values(anchorsMap);
      const res = await fetch('/api/layout', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({anchors: anchorsArr})
      });
      if (res.ok) {
        statusEl.textContent = 'Saved layout.';
        anchors = anchorsArr;
        computeAndDraw(true);
      } else {
        statusEl.textContent = 'Error saving layout.';
      }
    }

    async function pollPose() {
      const res = await fetch('/api/pose');
      const data = await res.json();
      if (data.has_pose) {
        lastPose = data;
        computeAndDraw(true);
        info.textContent = `Tag ${data.peer_id || ''} at x=${data.x.toFixed(2)} m, y=${data.y.toFixed(2)} m`;
      } else {
        info.textContent = 'No pose yet.';
      }
    }

    // Populate anchor table
    async function initAnchorsTable() {
      const res = await fetch('/api/layout');
      if (!res.ok) {
        statusEl.textContent = 'Error loading layout';
        return;
      }
      const data = await res.json();
      anchors = data.anchors || [];

      const tbody = document.querySelector('#anchors tbody');
      tbody.innerHTML = '';
      anchors.forEach(a => {
        const tr = document.createElement('tr');

        const tdId = document.createElement('td');
        tdId.textContent = a.id;
        tr.appendChild(tdId);

        const tdX = document.createElement('td');
        const inputX = document.createElement('input');
        inputX.type = 'number';
        inputX.step = '0.01';
        inputX.value = a.x;
        inputX.dataset.anchorId = a.id;
        inputX.dataset.coord = 'x';
        tdX.appendChild(inputX);
        tr.appendChild(tdX);

        const tdY = document.createElement('td');
        const inputY = document.createElement('input');
        inputY.type = 'number';
        inputY.step = '0.01';
        inputY.value = a.y;
        inputY.dataset.anchorId = a.id;
        inputY.dataset.coord = 'y';
        tdY.appendChild(inputY);
        tr.appendChild(tdY);

        tbody.appendChild(tr);
      });

      computeAndDraw(false);
    }

    initAnchorsTable();
    setInterval(pollPose, 200);
  </script>
</body>
</html>
    """
