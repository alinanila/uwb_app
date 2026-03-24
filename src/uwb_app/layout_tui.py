from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import yaml
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.reactive import reactive

from .local_apps_config import (
    load_localizer_cfg, 
    load_yaml_mapping,
)


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEFAULT_LOCALIZER_CONFIG = PROJECT_ROOT / "config" / "uwb_localizer.yaml"


def get_layout_file(config_path: Path = DEFAULT_LOCALIZER_CONFIG) -> Path:
    """get layout path from uwb_localizer"""
    cfg = load_localizer_cfg(config_path)
    # if separate layout yaml
    if cfg.layout_path is not None:
        return config_path.parent / cfg.layout_path
    # else use the localizer layout
    return config_path


def load_layout(path: Path) -> Dict[str, Tuple[float, float]]:
    """load anchors and return {id: (x, y)}."""
    data = load_yaml_mapping(path)
    # accept both layout:anchors and anchors: at root
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


def save_layout(path: Path, anchors: Dict[str, Tuple[float, float]]) -> None:
    """write input positions back into layout"""
    data = load_yaml_mapping(path)
    layout = data.setdefault("layout", {})
    anchors_out = layout.setdefault("anchors", {})
    for source_id, (x, y) in anchors.items():
        anchors_out[source_id] = [float(x), float(y)]
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


class StatusBar(Static):
    """simple status bar widget for messages"""
    message = reactive("")

    def watch_message(self, message: str) -> None:
        self.update(message)


class LayoutEditorApp(App):
    """Textual TUI for editing anchor layout"""

    CSS = """
    Screen {
        layout: vertical;
    }
    #table {
        height: 1fr;
    }
    #status {
        height: 3;
    }
    """

    BINDINGS = [
        ("e", "edit_cell", "edit selected cell"),
        ("s", "save_layout", "save layout"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, config_path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config_path = config_path or DEFAULT_LOCALIZER_CONFIG
        self.layout_path: Path | None = None
        self.anchors: Dict[str, Tuple[float, float]] = {}
        self.table: DataTable
        self.status: StatusBar

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        self.table = DataTable(id="table")
        self.table.add_columns("Anchor ID", "X (m)", "Y (m)")
        yield self.table
        self.status = StatusBar(id="status")
        yield self.status
        yield Footer()

    def on_mount(self) -> None:
        # load layout and fill table
        try:
            self.layout_path = get_layout_file(self.config_path)
            self.anchors = load_layout(self.layout_path)
        except Exception as e:
            self.status.message = f"error loading layout: {e}"
            return

        for anchor_id, (x, y) in sorted(self.anchors.items()):
            self.table.add_row(anchor_id, f"{x:.3f}", f"{y:.3f}")
        if self.table.row_count > 0:
            self.table.focus()
            self.table.cursor_type = "cell"
            self.table.cursor_coordinate = (0, 1)  # first row -> x
        self.status.message = "use arrow keys to move, 'e' to edit, 's' to save, 'q' to quit"

    def action_edit_cell(self) -> None:
        """edit selected cell"""
        if self.table.row_count == 0:
            return
        row, col = self.table.cursor_coordinate
        # only allow editing x and y (columns 1 and 2)
        if col not in (1, 2):
            self.status.message = "Select X or Y column to edit."
            return
        self.table.edit_cell_at(row, col)

    def on_data_table_cell_edited(self, event: DataTable.CellEdited) -> None:
        """update internal anchors dict when a cell is edited"""
        row, col = event.coordinate
        anchor_id = self.table.get_cell_at(row, 0)
        x_str = self.table.get_cell_at(row, 1)
        y_str = self.table.get_cell_at(row, 2)
        try:
            x = float(x_str)
            y = float(y_str)
        except ValueError:
            self.status.message = f"invalid value for anchor {anchor_id}."
            return
        self.anchors[str(anchor_id)] = (x, y)
        self.status.message = f"updated {anchor_id} to ({x:.3f}, {y:.3f})."

    def action_save_layout(self) -> None:
        """save anchors back to layout"""
        if not self.layout_path:
            self.status.message = "no layout path configured"
            return
        try:
            save_layout(self.layout_path, self.anchors)
        except Exception as e:
            self.status.message = f"error saving layout: {e}"
            return
        self.status.message = f"saved layout to {self.layout_path}."

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    # entrypoint for [project.scripts] in pyproject.toml
    app = LayoutEditorApp()
    app.run()


if __name__ == "__main__":
    main()