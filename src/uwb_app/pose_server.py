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
    z: Optional[float] = None
    peer_id: Optional[str] = None
    timestamp: Optional[float] = None


pose_state = PoseState()
pose_lock = threading.Lock()


def get_layout_file(config_path: Path = DEFAULT_LOCALIZER_CONFIG) -> Path:
    cfg = load_localizer_cfg(config_path)
    if cfg.layout_path is not None:
        return config_path.parent / cfg.layout_path
    return config_path


# def load_layout(path: Path) -> Dict[str, Tuple[float, float]]:
#     data = load_yaml_mapping(path)
#     layout_in = data.get("layout", data)
#     if not isinstance(layout_in, dict):
#         raise ValueError("layout must be a mapping")
#     anchors_in = layout_in.get("anchors", {})
#     if not isinstance(anchors_in, dict):
#         raise ValueError("layout.anchors must be a mapping")

#     anchors: Dict[str, Tuple[float, float]] = {}
#     for source_id, pos in anchors_in.items():
#         if not isinstance(pos, (list, tuple)) or len(pos) != 2:
#             raise ValueError(f"anchor {source_id} must be [x, y]")
#         anchors[str(source_id)] = (float(pos[0]), float(pos[1]))
#     return anchors

# if 3d
def load_layout(path: Path) -> Dict[str, Tuple[float, float, float]]:
    data = load_yaml_mapping(path)
    layout_in = data.get("layout", data)
    if not isinstance(layout_in, dict):
        raise ValueError("layout must be a mapping")
    anchors_in = layout_in.get("anchors", {})
    if not isinstance(anchors_in, dict):
        raise ValueError("layout.anchors must be a mapping")

    anchors: Dict[str, Tuple[float, float, float]] = {}
    for source_id, pos in anchors_in.items():
        if not isinstance(pos, (list, tuple)) or len(pos) not in (2, 3):
            raise ValueError(f"anchor {source_id} must be [x, y] or [x, y, z]")
        x, y = float(pos[0]), float(pos[1])
        z = float(pos[2]) if len(pos) == 3 else 0.0
        anchors[str(source_id)] = (x, y, z)
    return anchors


# def save_layout(path: Path, anchors: Dict[str, Tuple[float, float]]) -> None:
#     data = load_yaml_mapping(path)
#     layout = data.setdefault("layout", {})
#     if not isinstance(layout, dict):
#         raise ValueError(f"layout in {path} must be a mapping")

#     anchors_out = layout.get("anchors")
#     if not isinstance(anchors_out, dict):
#         anchors_out = {}
#         layout["anchors"] = anchors_out

#     for source_id, (x, y) in anchors.items():
#         anchors_out[source_id] = [float(x), float(y)]

#     with path.open("w", encoding="utf-8") as f:
#         yaml.safe_dump(data, f, sort_keys=False)

# if 3d
def save_layout(path: Path, anchors: Dict[str, Tuple[float, float, float]]) -> None:
    data = load_yaml_mapping(path)
    layout = data.setdefault("layout", {})
    if not isinstance(layout, dict):
        raise ValueError(f"layout in {path} must be a mapping")

    anchors_out = layout.get("anchors")
    if not isinstance(anchors_out, dict):
        anchors_out = {}
        layout["anchors"] = anchors_out

    for source_id, (x, y, z) in anchors.items():
        anchors_out[source_id] = [float(x), float(y), float(z)]

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


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
            z = event.get("z_m")
            peer_id = event.get("peer_id")
            ts = event.get("timestamp")

            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue

            with pose_lock:
                pose_state.x = float(x)
                pose_state.y = float(y)
                pose_state.z = float(z) if isinstance(z, (int, float)) else None
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

    # print anchor layout on startup
    try:
        layout_file = get_layout_file()
        anchors = load_layout(layout_file)
        print("\nanchor layout:")
        for aid, pos in sorted(anchors.items()):
            # print(f"  {aid} = x={pos[0]:.3f} y={pos[1]:.3f}")
            print(f"  {aid} = x={pos[0]:.3f} y={pos[1]:.3f} z={pos[2]:.3f}")
        print()
    except Exception as e:
        print(f"could not load anchor layout on startup: {e}")


class Anchor(BaseModel):
    id: str
    x: float
    y: float
    z: float = 0.0


class LayoutUpdate(BaseModel):
    anchors: List[Anchor]


@app.get("/api/layout")
def api_layout():
    # load anchor layout
    layout_file = get_layout_file()
    anchors = load_layout(layout_file)
    return LayoutUpdate(
        anchors = [
            # Anchor(id=aid, x=pos[0], y=pos[1])
            Anchor(id=aid, x=pos[0], y=pos[1], z=pos[2])
            for aid, pos in sorted(anchors.items())
        ]
    )


@app.post("/api/layout")
def api_update_layout(update: LayoutUpdate):
    layout_file = get_layout_file()
    # anchors = {a.id: (a.x, a.y) for a in update.anchors}
    anchors = {a.id: (a.x, a.y, a.z) for a in update.anchors}
    save_layout(layout_file, anchors)

    # print updated anchor layout
    print("\nupdated anchor layout:")
    for a in sorted(update.anchors, key=lambda a: a.id):
        print(f"  {a.id} = x={a.x:.3f} y={a.y:.3f}")
    print()
    
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
            "z": pose_state.z,
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
          <tr><th>Anchor ID</th><th>X (m)</th><th>Y (m)</th><th>Z (m)</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <button onclick="saveLayout()">Save Layout</button>
      <button onclick="resetView()">Reset View</button>
      <p id="status"></p>
      <p id="info"></p>
    </div>
    <div>
      <h2>Visualisation</h2>
      <canvas id="canvas" width="800" height="600"></canvas>
    </div>
  </div>

  <script>
    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');
    const info = document.getElementById('info');
    const statusEl = document.getElementById('status') || { textContent: "" };

    let anchors = [];
    let scale = 60;         // pixels per meter - changed by scroll wheel
    let offsetX = 0;        // pan offset in pixels
    let offsetY = 0;
    let isPanning = false;
    let panStartX = 0;
    let panStartY = 0;

    function worldToCanvas(x, y) {
        const cx = canvas.width / 2 + (x * scale) + offsetX;
        const cy = canvas.height / 2 - (y * scale) + offsetY;
        return { cx, cy };
    }

    function drawGrid() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // draw enough grid lines to always fill the canvas regardless of pan/zoom
        const maxExtent = Math.ceil(
            Math.max(canvas.width, canvas.height) / scale
        ) + 2;

        ctx.strokeStyle = '#eee';
        ctx.lineWidth = 1;

        const step = 1;
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

        // axes
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

    function draw() {
        drawGrid();
        drawAnchors();
        if (lastPose.has_pose) {
            drawTag(lastPose.x, lastPose.y);
        }
        // draw scale indicator in bottom-left corner
        const barMetres = 1.0;
        const barPixels = barMetres * scale;
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(20, canvas.height - 20);
        ctx.lineTo(20 + barPixels, canvas.height - 20);
        ctx.stroke();
        ctx.fillStyle = '#333';
        ctx.font = '11px sans-serif';
        ctx.fillText(`${barMetres} m`, 20, canvas.height - 25);
    }

    let lastPose = { has_pose: false, x: 0, y: 0, peer_id: null };

    // zoom with scroll wheel — zoom towards cursor position
    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
        const rect = canvas.getBoundingClientRect();

        // cursor position relative to canvas centre
        const mouseX = e.clientX - rect.left - canvas.width / 2;
        const mouseY = e.clientY - rect.top  - canvas.height / 2;

        // adjust offset so zoom is centred on cursor
        offsetX = mouseX - (mouseX - offsetX) * zoomFactor;
        offsetY = mouseY - (mouseY - offsetY) * zoomFactor;

        scale *= zoomFactor;

        // clamp scale to sensible range
        scale = Math.max(10, Math.min(500, scale));
        draw();
    }, { passive: false });

    // pan with click and drag
    canvas.addEventListener('mousedown', (e) => {
        isPanning = true;
        panStartX = e.clientX - offsetX;
        panStartY = e.clientY - offsetY;
        canvas.style.cursor = 'grabbing';
    });

    canvas.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        offsetX = e.clientX - panStartX;
        offsetY = e.clientY - panStartY;
        draw();
    });

    canvas.addEventListener('mouseup', () => {
        isPanning = false;
        canvas.style.cursor = 'default';
    });

    canvas.addEventListener('mouseleave', () => {
        isPanning = false;
        canvas.style.cursor = 'default';
    });

    function resetView() {
        scale = 60;
        offsetX = 0;
        offsetY = 0;
        draw();
    }

    async function loadLayout() {
      const res = await fetch('/api/layout');
      if (!res.ok) {
        statusEl.textContent = 'Error loading layout';
        return;
      }
      const data = await res.json();
      anchors = data.anchors || [];
      statusEl.textContent = '';
      draw();
    }

    async function saveLayout() {
      const inputs = document.querySelectorAll('#anchors input');
      const anchorsMap = {};
      inputs.forEach(input => {
        const id = input.dataset.anchorId;
        const coord = input.dataset.coord;
        const value = parseFloat(input.value);
        if (!anchorsMap[id]) {
          anchorsMap[id] = {id: id, x: 0, y: 0, z: 0};
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
        draw();
      } else {
        statusEl.textContent = 'Error saving layout.';
      }
    }

    async function pollPose() {
      const res = await fetch('/api/pose');
      const data = await res.json();
      if (data.has_pose) {
        lastPose = data;
        draw();
        const zStr = data.z != null ? `, z=${data.z.toFixed(2)} m` : '';
        info.textContent = `Tag ${data.peer_id || ''} at x=${data.x.toFixed(2)} m, y=${data.y.toFixed(2)} m${zStr}`;
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

        const tdZ = document.createElement('td');
        const inputZ = document.createElement('input');
        inputZ.type = 'number';
        inputZ.step = '0.01';
        inputZ.value = a.z ?? 0;
        inputZ.dataset.anchorId = a.id;
        inputZ.dataset.coord = 'z';
        tdZ.appendChild(inputZ);
        tr.appendChild(tdZ);

        tbody.appendChild(tr);
      });

      draw();
    }

    initAnchorsTable();
    setInterval(pollPose, 100);
  </script>
</body>
</html>
    """
