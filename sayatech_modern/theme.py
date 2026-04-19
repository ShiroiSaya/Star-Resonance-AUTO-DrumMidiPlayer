from __future__ import annotations


def _base_palette(dark_mode: bool):
    if dark_mode:
        return {
            "bg": "#03060b",
            "surface": "#0f1419",
            "surface2": "#131a24",
            "surface3": "#1a2332",
            "text": "#e8ecf1",
            "muted": "#8b96a8",
            "border": "#232f3f",
            "accent": "#5b9eff",
            "accentText": "#ffffff",
            "tab": "#0d1117",
            "tabSelected": "#0a0f18",
            "track": "#2a3847",
            "slider": "#5b9eff",
            "selection": "#4a8fef",
            "selectionText": "#ffffff",
            "logBg": "#050a10",
            "success": "#34d399",
            "successLight": "#6ee7b7",
            "error": "#ff6b6b",
            "errorLight": "#ff8787",
            "warning": "#ffd43b",
            "warningLight": "#ffe066",
            "info": "#74c0fc",
            "infoLight": "#a5d8ff",
        }
    return {
        "bg": "#f8f9fa",
        "surface": "#ffffff",
        "surface2": "#fafbfc",
        "surface3": "#f0f2f5",
        "text": "#1a202c",
        "muted": "#6b7280",
        "border": "#e5e7eb",
        "accent": "#2563eb",
        "accentText": "#ffffff",
        "tab": "#f3f4f6",
        "tabSelected": "#ffffff",
        "track": "#d1d5db",
        "slider": "#2563eb",
        "selection": "#2563eb",
        "selectionText": "#ffffff",
        "logBg": "#fafbfc",
        "success": "#059669",
        "successLight": "#10b981",
        "error": "#dc2626",
        "errorLight": "#ef4444",
        "warning": "#d97706",
        "warningLight": "#f59e0b",
        "info": "#2563eb",
        "infoLight": "#3b82f6",
    }


def _apply_preset(c: dict, preset: str, dark_mode: bool) -> dict:
    preset = (preset or "ocean").strip().lower()
    if preset == "violet":
        c.update({
            "accent": "#7c3aed" if not dark_mode else "#a78bfa",
            "slider": "#7c3aed" if not dark_mode else "#a78bfa",
            "selection": "#7c3aed" if not dark_mode else "#7c3aed",
            "tab": "#f3e8ff" if not dark_mode else "#1e1b4b",
            "surface3": "#f5f3ff" if not dark_mode else "#2e1a47",
        })
    elif preset == "emerald":
        c.update({
            "accent": "#059669" if not dark_mode else "#10b981",
            "slider": "#059669" if not dark_mode else "#10b981",
            "selection": "#059669" if not dark_mode else "#059669",
            "tab": "#ecfdf5" if not dark_mode else "#064e3b",
            "surface3": "#f0fdf4" if not dark_mode else "#0f3d2d",
        })
    elif preset == "sunset":
        c.update({
            "accent": "#ea580c" if not dark_mode else "#fb923c",
            "slider": "#ea580c" if not dark_mode else "#fb923c",
            "selection": "#ea580c" if not dark_mode else "#ea580c",
            "tab": "#fff7ed" if not dark_mode else "#431407",
            "surface3": "#fffbf5" if not dark_mode else "#5a2d0c",
        })
    elif preset == "graphite":
        c.update({
            "accent": "#6b7280" if not dark_mode else "#9ca3af",
            "slider": "#6b7280" if not dark_mode else "#9ca3af",
            "selection": "#6b7280" if not dark_mode else "#6b7280",
            "tab": "#f3f4f6" if not dark_mode else "#1f2937",
            "surface3": "#f9fafb" if not dark_mode else "#374151",
        })
    elif preset == "rose":
        c.update({
            "accent": "#e11d48" if not dark_mode else "#f43f5e",
            "slider": "#e11d48" if not dark_mode else "#f43f5e",
            "selection": "#e11d48" if not dark_mode else "#e11d48",
            "tab": "#ffe4e6" if not dark_mode else "#500724",
            "surface3": "#fff1f2" if not dark_mode else "#831843",
        })
    elif preset == "cyan":
        c.update({
            "accent": "#0891b2" if not dark_mode else "#06b6d4",
            "slider": "#0891b2" if not dark_mode else "#06b6d4",
            "selection": "#0891b2" if not dark_mode else "#0891b2",
            "tab": "#ecf8fa" if not dark_mode else "#164e63",
            "surface3": "#f0fafb" if not dark_mode else "#0e3a47",
        })

    if "success" not in c:
        c["success"] = "#059669" if not dark_mode else "#10b981"
        c["successLight"] = "#10b981" if not dark_mode else "#6ee7b7"
    if "error" not in c:
        c["error"] = "#dc2626" if not dark_mode else "#ff6b6b"
        c["errorLight"] = "#ef4444" if not dark_mode else "#ff8787"
    if "warning" not in c:
        c["warning"] = "#d97706" if not dark_mode else "#ffd43b"
        c["warningLight"] = "#f59e0b" if not dark_mode else "#ffe066"
    if "info" not in c:
        c["info"] = "#2563eb" if not dark_mode else "#74c0fc"
        c["infoLight"] = "#3b82f6" if not dark_mode else "#a5d8ff"

    return c


def _palette(dark_mode: bool, preset: str = "ocean"):
    return _apply_preset(_base_palette(dark_mode), preset, dark_mode)


def build_stylesheet(dark_mode: bool = False, scale_percent: int = 100, preset: str = "ocean", backdrop_enabled: bool = False) -> str:
    c = _palette(dark_mode, preset)
    scale = max(0.8, min(1.4, scale_percent / 100.0))
    font = max(11, round(12.2 * scale))
    title = max(18, round(21 * scale))
    section = max(14, round(15 * scale))
    radius_card = max(14, round(18 * scale))
    radius_side = max(16, round(20 * scale))
    radius_control = max(9, round(12 * scale))
    padding_y = max(6, round(8 * scale))
    padding_x = max(10, round(13 * scale))
    field_height = max(18, round(20 * scale))
    slider_handle = max(14, round(16 * scale))
    checkbox_spacing = max(10, round(12 * scale))
    page_bg = "transparent" if backdrop_enabled and not dark_mode else c["bg"]
    scroll_bg = "transparent" if backdrop_enabled and not dark_mode else c["bg"]
    main_bg = c["bg"] if dark_mode else "transparent" if backdrop_enabled else c["bg"]
    return f"""
QApplication, QMainWindow, QWidget#Surface, QDialog#Surface, QWidget#Page, QWidget#CenterSurface, QStackedWidget {{
    background: {main_bg};
    color: {c['text']};
    font-family: 'Microsoft YaHei UI', 'Segoe UI Variable Text', 'PingFang SC', 'Noto Sans SC', sans-serif;
    font-size: {font}px;
}}
QWidget {{
    color: {c['text']};
    font-family: 'Microsoft YaHei UI', 'Segoe UI Variable Text', 'PingFang SC', 'Noto Sans SC', sans-serif;
    font-size: {font}px;
}}
QScrollArea, QAbstractScrollArea, QAbstractScrollArea::viewport, QStackedWidget {{
    background: {scroll_bg};
    border: none;
}}
QAbstractScrollArea > QWidget, QScrollArea > QWidget, QWidget#Page, QWidget#CenterSurface, QWidget#Surface {{
    background: {page_bg};
    border: none;
}}
QSplitter, QSplitterHandle {{
    background: {page_bg};
}}
QDialog#Surface {{
    border-radius: {radius_card}px;
}}
QLabel {{
    background: transparent;
    color: {c['text']};
}}
QMainWindow, QFrame#Surface {{
    background: {main_bg};
}}
QFrame#Card, QFrame#Sidebar {{
    background: transparent;
    border: none;
}}
QPushButton {{
    background: {c['surface2']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: {padding_y}px {padding_x}px;
    font-weight: 600;
    color: {c['text']};
}}
QPushButton:hover {{
    border-color: {c['accent']};
    background: {c['surface3']};
}}
QPushButton:pressed {{
    background: {c['accent']};
    color: {c['accentText']};
    padding: {padding_y+1}px {padding_x-1}px {padding_y-1}px {padding_x+1}px;
}}
QPushButton:focus {{
    border: 2px solid {c['accent']};
    padding: {padding_y-1}px {padding_x-1}px;
}}
QPushButton:disabled {{
    color: {c['muted']};
    background: {c['surface2']};
    border-color: {c['border']};
    opacity: 0.5;
}}
QPushButton[primary="true"] {{
    background: {c['accent']};
    color: {c['accentText']};
    border: none;
    font-weight: 700;
}}
QPushButton[primary="true"]:hover {{
    background: {c['accent']};
    opacity: 0.9;
}}
QPushButton[primary="true"]:pressed {{
    opacity: 0.8;
    padding: {padding_y+1}px {padding_x-1}px {padding_y-1}px {padding_x+1}px;
}}
QPushButton[primary="true"]:focus {{
    border: none;
}}
QPushButton[primary="true"]:disabled {{
    opacity: 0.5;
    background: {c['accent']};
}}
QPushButton[variant="success"] {{
    background: {c['success']};
    color: white;
    border: none;
    font-weight: 700;
}}
QPushButton[variant="success"]:hover {{
    background: {c['successLight']};
}}
QPushButton[variant="error"] {{
    background: {c['error']};
    color: white;
    border: none;
    font-weight: 700;
}}
QPushButton[variant="error"]:hover {{
    background: {c['errorLight']};
}}
QPushButton[variant="warning"] {{
    background: {c['warning']};
    color: white;
    border: none;
    font-weight: 700;
}}
QPushButton[variant="warning"]:hover {{
    background: {c['warningLight']};
}}
QPushButton[variant="info"] {{
    background: {c['info']};
    color: white;
    border: none;
    font-weight: 700;
}}
QPushButton[variant="info"]:hover {{
    background: {c['infoLight']};
}}
QToolButton {{
    background: {c['surface2']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: {padding_y}px {padding_x}px;
    font-weight: 700;
}}
QLineEdit, QListWidget, QTreeWidget, QTextEdit, QPlainTextEdit, QTabWidget::pane, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QAbstractSpinBox {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    selection-background-color: {c['selection']};
    selection-color: {c['selectionText']};
    color: {c['text']};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QKeySequenceEdit, QAbstractSpinBox {{
    padding: {padding_y}px {padding_x}px;
    min-height: {field_height}px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QKeySequenceEdit:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus {{
    border: 2px solid {c['accent']};
    outline: none;
    background: {c['surface']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QKeySequenceEdit:hover, QAbstractSpinBox:hover, QPlainTextEdit:hover {{
    border: 1px solid {c['accent']};
}}
QAbstractSpinBox QLineEdit, QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{
    background: transparent;
    border: none;
    color: {c['text']};
    selection-background-color: {c['selection']};
    selection-color: {c['selectionText']};
    padding: 0px;
}}
QPlainTextEdit {{
    background: {c['logBg']};
    font-family: 'Consolas', 'JetBrains Mono', 'Microsoft YaHei UI';
}}
QPlainTextEdit:focus {{
    border: 2px solid {c['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 18px;
    border: none;
}}
QCheckBox {{
    spacing: {checkbox_spacing}px;
    font-weight: 600;
}}
QCheckBox::indicator {{
    width: 0px;
    height: 0px;
}}
QListWidget, QTreeWidget {{
    outline: none;
}}
QListWidget#NavList, QListWidget#NavList:focus {{
    background: transparent;
    border: none;
}}
QListWidget#NavList::item {{
    margin: 3px 0;
    padding: 10px 12px;
    border: none;
    border-radius: 10px;
    color: {c['text']};
}}
QListWidget#NavList::item:hover {{
    background: {c['surface3']};
}}
QListWidget#NavList::item:selected {{
    background: {c['accent']};
    color: {c['accentText']};
    border: none;
    outline: none;
}}
QTreeWidget::item {{
    padding: 6px;
    border-radius: 4px;
}}
QTreeWidget::item:hover {{
    background: {c['surface3']};
}}
QTreeWidget::item:selected {{
    background: {c['selection']};
    color: {c['selectionText']};
    border-radius: 8px;
}}
QHeaderView::section {{
    background: {c['surface2']};
    border: none;
    border-bottom: 1px solid {c['border']};
    padding: 6px 8px;
    font-weight: 700;
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: {c['tab']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: {padding_y}px {padding_x}px;
    margin-right: 6px;
    color: {c['muted']};
    font-weight: 500;
}}
QTabBar::tab:hover {{
    background: {c['surface3']};
    color: {c['text']};
}}
QTabBar::tab:selected {{
    background: {c['tabSelected']};
    color: {c['text']};
    font-weight: 600;
    border-color: {c['accent']};
}}
QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {c['track']};
}}
QSlider::handle:horizontal {{
    background: {c['slider']};
    width: {slider_handle}px;
    margin: -5px 0;
    border-radius: {int(slider_handle/2)}px;
    border: 1px solid {c['border']};
}}
QSlider::handle:horizontal:hover {{
    background: {c['accent']};
    width: {int(slider_handle * 1.15)}px;
    margin: -5px 0;
    border: 1px solid {c['accent']};
}}
QSlider::groove:horizontal:hover {{
    background: {c['accent']};
    opacity: 0.3;
}}
QScrollBar:vertical {{
    width: 10px;
    background: transparent;
}}
QScrollBar::handle:vertical {{
    background: {c['track']};
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['accent']};
}}
QLabel[muted="true"] {{
    color: {c['muted']};
}}
QLabel[title="true"] {{
    font-size: {title}px;
    font-weight: 700;
}}
QLabel[sectionTitle="true"] {{
    font-size: {section}px;
    font-weight: 700;
    color: {c['text']};
}}
QLabel[sectionDesc="true"] {{
    color: {c['muted']};
    padding-bottom: 2px;
}}
QLabel[fieldLabel="true"] {{
    font-weight: 600;
    color: {c['text']};
}}
QLabel[watermark="true"] {{
    color: {c['muted']};
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QLabel[badge="true"] {{
    background: {c['accent']};
    color: {c['accentText']};
    border-radius: 14px;
    border: 1px solid {c['border']};
    padding: 6px 14px;
    font-weight: 700;
    font-size: {max(10, round(11 * scale))}px;
    min-height: 28px;
    min-width: 28px;
}}
QLabel[badge="true"][variant="success"] {{
    background: {c['success']};
    color: white;
    border-color: {c['successLight']};
}}
QLabel[badge="true"][variant="error"] {{
    background: {c['error']};
    color: white;
    border-color: {c['errorLight']};
}}
QLabel[badge="true"][variant="warning"] {{
    background: {c['warning']};
    color: white;
    border-color: {c['warningLight']};
}}
QLabel[badge="true"][variant="info"] {{
    background: {c['info']};
    color: white;
    border-color: {c['infoLight']};
}}
QLabel[kpiTitle="true"] {{
    color: {c['muted']};
    font-size: {max(12, round(12 * scale))}px;
    font-weight: 600;
}}
QLabel[kpiValue="true"] {{
    color: {c['text']};
    font-size: {max(16, round(17 * scale))}px;
    font-weight: 700;
}}
QLabel#StatusValue {{
    font-size: {max(15, round(16 * scale))}px;
    font-weight: 700;
}}
/* 增强的滑块交互 */
QSlider::handle:horizontal:hover {{
    background: {c['accent']};
    width: {int(slider_handle * 1.2)}px;
    margin: -5px 0;
}}
QSlider::groove:horizontal:hover {{
    background: {c['track']};
}}
/* 增强的列表项交互 */
QListWidget::item:hover {{
    background: {c['surface3']};
}}
QListWidget::item:selected {{
    background: {c['selection']};
}}
QTreeWidget::item:hover {{
    background: {c['surface3']};
}}
/* 增强的标签页 */
QTabBar::tab:hover {{
    background: {c['surface3']};
}}
QTabBar::tab:selected:hover {{
    background: {c['tabSelected']};
}}
/* 增强的对话框 */
QDialog {{
    background: {c['bg']};
}}
QDialog#Surface {{
    border-radius: {radius_card}px;
}}
/* 增强的分组框 */
QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: 12px;
    margin-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 3px;
}}
/* 增强的菜单 */
QMenu {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: 4px 0;
}}
QMenu::item:selected {{
    background: {c['surface3']};
    border-radius: {radius_control}px;
    margin: 2px 4px;
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 4px 0;
}}
/* 增强的工具提示 */
QToolTip {{
    background: {c['surface2']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    padding: 6px 10px;
}}
/* 增强的进度条 */
QProgressBar {{
    background: {c['track']};
    border: 1px solid {c['border']};
    border-radius: {radius_control}px;
    text-align: center;
    color: {c['text']};
}}
QProgressBar::chunk {{
    background: {c['accent']};
    border-radius: {radius_control - 2}px;
}}
/* 增强的分割线 */
QSplitter::handle {{
    background: {c['border']};
    width: 1px;
}}
QSplitter::handle:hover {{
    background: {c['accent']};
}}
/* 增强的卡片效果 */
QFrame#Card {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: {radius_card}px;
}}
/* 增强的侧边栏 */
QFrame#Sidebar {{
    background: {c['surface2']};
    border-right: 1px solid {c['border']};
}}
/* 增强的输入框焦点效果 */
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    background: {c['surface']};
    border: 2px solid {c['accent']};
    outline: none;
}}
/* 增强的按钮悬停效果 */
QPushButton:hover {{
    background: {c['surface3']};
    border-color: {c['accent']};
}}
/* 增强的列表项 */
QListWidget::item:hover {{
    background: {c['surface3']};
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background: {c['selection']};
    color: {c['selectionText']};
    border-radius: 6px;
}}
/* 增强的树形项 */
QTreeWidget::item:hover {{
    background: {c['surface3']};
    border-radius: 4px;
}}
QTreeWidget::item:selected {{
    background: {c['selection']};
    color: {c['selectionText']};
    border-radius: 4px;
}}
"""
