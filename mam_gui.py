# mam_gui.py — 主界面（纯 UI，业务逻辑见 mam_core / mam_db / mam_meta）
import sys
import os
import re
import json
import cv2
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime

from mam_core import (load_config, save_config, get_phash, get_thumbnail,
                       get_file_size, get_asset_type, make_thumb_bytes,
                       hamming, ALL_EXTS, IMG_EXTS, VID_EXTS,
                       load_producer_codes, save_producer_codes, parse_producer_from_filename)
from mam_db   import DBManager
from mam_meta import write_metadata, read_metadata, get_phash_from_file, check_deps, exiftool_status

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTabWidget, QTableWidget, QTableWidgetItem,
    QMessageBox, QFormLayout, QFrame, QTextEdit, QHeaderView, QScrollArea,
    QDialog, QDialogButtonBox, QTreeWidget, QTreeWidgetItem, QSplitter, QComboBox,
    QFileDialog, QProgressBar
)
from PyQt6.QtCore  import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui   import QPixmap, QImage, QColor, QFont

# ── 全局日志总线 ────────────────────────────────────
class _Bus(QObject):
    sig = pyqtSignal(str)
log_bus = _Bus()
def gui_log(msg): log_bus.sig.emit(msg)

# ── 数据库单例 ───────────────────────────────────────
db = DBManager()

# ─────────────────────────────────────────────────────
# 辅助：确保素材已在库中（自动登记）
# ─────────────────────────────────────────────────────
def ensure_registered(filepath, operator_name):
    """
    若素材未登记则自动登记并写入元数据。
    返回 (phash, record_dict) 或 (None, None)
    """
    img = get_thumbnail(filepath)
    if img is None:
        gui_log(f"❌ 无法读取: {os.path.basename(filepath)}")
        return None, None

    ph, source = get_phash_from_file(filepath, img)
    if not ph:
        gui_log(f"❌ phash计算失败: {os.path.basename(filepath)}")
        return None, None

    existing = db.lookup(ph, threshold=12)
    if existing:
        return existing['phash'], existing

    # 新素材 → 登记
    fname = os.path.basename(filepath)
    atype = get_asset_type(filepath)
    fsize = get_file_size(filepath)
    now   = datetime.now()
    rec   = {
        "phash": ph, "filename": fname,
        "asset_type": atype, "file_size": fsize,
        "producer": operator_name, "created_at": now.isoformat()
    }
    write_metadata(filepath, rec)
    db.upsert_asset(ph, fname, atype, fsize, operator_name, now,
                    json.dumps(rec, ensure_ascii=False, default=str),
                    make_thumb_bytes(img))
    gui_log(f"📌 自动登记: {fname}  作者:{operator_name}  phash:{ph}")
    return ph, rec

# ─────────────────────────────────────────────────────
# 拖拽区
# ─────────────────────────────────────────────────────
class DropArea(QFrame):
    filesChanged = pyqtSignal(list)

    def __init__(self, title="拖入文件", multi=False):
        super().__init__()
        self.multi  = multi
        self._files = []
        self.setAcceptDrops(True)
        self.setMinimumHeight(100)
        self.setStyleSheet(
            "DropArea{border:2px dashed #3498db;border-radius:8px;background:#f8f9fa;}"
        )
        lay = QVBoxLayout(self)
        hdr = QHBoxLayout()
        lbl = QLabel(title); lbl.setStyleSheet("font-weight:bold;color:#555;")
        btn = QPushButton("清空"); btn.setFixedWidth(44); btn.clicked.connect(self.clear)
        hdr.addWidget(lbl); hdr.addStretch(); hdr.addWidget(btn); lay.addLayout(hdr)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        self._box = QWidget(); self._pv = QHBoxLayout(self._box)
        self._pv.setAlignment(Qt.AlignmentFlag.AlignLeft); sc.setWidget(self._box)
        lay.addWidget(sc); self._draw()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        added = []
        for u in e.mimeData().urls():
            p = os.path.abspath(u.toLocalFile())
            if os.path.isdir(p):
                for rt, _, fs in os.walk(p):
                    for f in fs:
                        if f.lower().endswith(ALL_EXTS): added.append(os.path.join(rt, f))
            elif p.lower().endswith(ALL_EXTS):
                added.append(p)
        if not added: return
        if self.multi:
            self._files.extend(f for f in added if f not in self._files)
        else:
            self._files = [added[0]]
        self._draw(); self.filesChanged.emit(self._files)

    def _draw(self):
        while self._pv.count():
            w = self._pv.takeAt(0).widget()
            if w: w.deleteLater()
        if not self._files:
            ph = QLabel("拖入文件或文件夹…"); ph.setStyleSheet("color:#aaa;font-size:12px;")
            self._pv.addWidget(ph); return
        for fp in self._files[:60]:
            box = QWidget(); bv = QVBoxLayout(box)
            bv.setContentsMargins(2, 2, 2, 2); bv.setSpacing(2)
            lbl = QLabel(); lbl.setFixedSize(70, 70)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("border:1px solid #ccc;background:#111;border-radius:3px;")
            th = get_thumbnail(fp)
            if th is not None:
                rgb = cv2.cvtColor(th, cv2.COLOR_BGR2RGB); h, w_img, ch = rgb.shape
                qi  = QImage(rgb.data, w_img, h, ch * w_img, QImage.Format.Format_RGB888)
                pm  = QPixmap.fromImage(qi).scaled(
                    68, 68,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                lbl.setPixmap(pm)
            else:
                lbl.setText("?")
            nm = QLabel(os.path.basename(fp)[:11])
            nm.setStyleSheet("font-size:9px;color:#555;")
            nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bv.addWidget(lbl); bv.addWidget(nm); self._pv.addWidget(box)

    def clear(self): self._files = []; self._draw()
    def files(self): return list(self._files)
    def file(self):  return self._files[0] if self._files else None

# ─────────────────────────────────────────────────────
# 批量扫描线程（支持随时停止）
# ─────────────────────────────────────────────────────
class ScanWorker(QThread):
    progress = pyqtSignal(int, int, int, int, int)  # total, done, added, skipped, failed
    log_line = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, folder, operator, known_phashes, code_map=None):
        super().__init__()
        self._folder       = folder
        self._operator     = operator
        self._known        = set(known_phashes)   # 线程内独立副本
        self._code_map     = code_map or {}
        self._should_stop  = False

    def stop(self):
        self._should_stop = True

    def run(self):
        folder      = self._folder
        folder_name = os.path.basename(folder.rstrip('/\\'))
        # 自动识别 Canva 文件夹名中的 【ID】
        m_id        = re.search(r'【(\d+)】', folder_name)
        canva_id    = m_id.group(1) if m_id else None
        canva_name  = re.sub(r'【\d+】', '', folder_name).strip() if m_id else None

        # ── 遍历收集所有媒体文件 ────────────────────────
        self.log_line.emit(f"📂 正在扫描文件列表: {folder}")
        all_files = []
        for rt, _, fs in os.walk(folder):
            for f in fs:
                if f.lower().endswith(ALL_EXTS):
                    all_files.append(os.path.join(rt, f))
        total = len(all_files)
        if canva_id:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件  |  🎨 Canva模板ID: 【{canva_id}】")
        else:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件")
        if total == 0:
            self.finished.emit({'total': 0, 'added': 0, 'skipped': 0,
                                'failed': 0, 'canva_id': canva_id, 'stopped': False})
            return

        added = skipped = failed = 0
        canva_phashes = []

        for i, fp in enumerate(all_files):
            if self._should_stop:
                break
            try:
                img = get_thumbnail(fp)
                if img is None:
                    failed += 1
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                ph, _ = get_phash_from_file(fp, img)
                if not ph:
                    failed += 1
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                if ph in self._known:
                    skipped += 1
                    if canva_id:
                        canva_phashes.append(ph)
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                # 新素材 → 注册并写入元数据
                fname    = os.path.basename(fp)
                atype    = get_asset_type(fp)
                fsize    = get_file_size(fp)
                now      = datetime.now()
                producer = parse_producer_from_filename(fname, self._code_map)
                rec   = {"phash": ph, "filename": fname, "asset_type": atype,
                         "file_size": fsize, "producer": producer,
                         "created_at": now.isoformat()}
                write_metadata(fp, rec)
                db.upsert_asset(ph, fname, atype, fsize, producer, now,
                                json.dumps(rec, ensure_ascii=False, default=str),
                                make_thumb_bytes(img))
                self._known.add(ph)
                if canva_id:
                    canva_phashes.append(ph)
                added += 1
                if added % 50 == 1:
                    self.log_line.emit(f"✅ 新增: {fname[:45]}  phash:{ph}")
            except Exception as e:
                failed += 1
                self.log_line.emit(f"❌ {os.path.basename(fp)}: {str(e)[:80]}")
            self.progress.emit(total, i + 1, added, skipped, failed)

        # ── Canva 模板自动登记 ───────────────────────────
        if canva_id and canva_phashes:
            try:
                unique_ph = list(dict.fromkeys(canva_phashes))
                db.add_canva_template(canva_id, canva_name, self._operator, unique_ph)
                self.log_line.emit(
                    f"🎨 Canva模板【{canva_id}】({canva_name}) 已登记，关联{len(unique_ph)}个素材"
                )
            except Exception as e:
                self.log_line.emit(f"⚠️ Canva模板登记失败: {e}")

        self.finished.emit({
            'total': total, 'added': added, 'skipped': skipped,
            'failed': failed, 'canva_id': canva_id, 'stopped': self._should_stop
        })


# ─────────────────────────────────────────────────────
# 后台线程
# ─────────────────────────────────────────────────────
class Worker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)
    def __init__(self, fn): super().__init__(); self._fn = fn
    def run(self):
        try:   self.done.emit(self._fn())
        except Exception as e: self.error.emit(str(e))

# ─────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────
class MamApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAM 素材溯源管理系统 v3.1")
        self.setMinimumSize(1280, 920)
        self._cfg     = load_config()
        self._workers = []
        self._lib_data = []
        self._last_canva_id = None
        self._build_ui()
        log_bus.sig.connect(self._log)
        ok, msg = db.connect()
        self._log("✅ 数据库连接成功" if ok else f"⚠️ 数据库: {msg}")
        # exiftool 状态
        self._log(exiftool_status())
        # 检查 Python 依赖
        missing = check_deps()
        for m in missing:
            self._log(f"⚠️ 缺少依赖: {m}")

    # ═══════════════════ UI 构建 ═══════════════════════
    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        vbox = QVBoxLayout(root); vbox.setContentsMargins(6,6,6,4)

        # 顶栏
        top = QHBoxLayout()
        self._lbl_user = QLabel(f"👤  操作员：{self._cfg['user_name']}")
        self._lbl_user.setStyleSheet("font-size:14px;font-weight:bold;")
        top.addWidget(self._lbl_user); top.addStretch()
        btn_cfg = QPushButton("⚙️  系统设置"); btn_cfg.clicked.connect(self._dlg_settings)
        top.addWidget(btn_cfg); vbox.addLayout(top)

        tabs = QTabWidget()
        tabs.addTab(self._tab_register(),  "  素材登记  ")
        tabs.addTab(self._tab_derive(),    "  处理关联  ")
        tabs.addTab(self._tab_compose(),   "  成品封装  ")
        tabs.addTab(self._tab_canva(),     "  Canva模板 ")
        tabs.addTab(self._tab_query(),     "  溯源查询  ")
        tabs.addTab(self._tab_library(),   "  全量库    ")
        tabs.addTab(self._tab_batch_scan(),  "  批量扫描  ")
        vbox.addWidget(tabs)

        self._log_box = QTextEdit(); self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(110)
        self._log_box.setStyleSheet("background:#1a1a2e;color:#00d4ff;font-size:12px;")
        vbox.addWidget(self._log_box)

    # ── Tab1：素材登记 ──────────────────────────────────
    def _tab_register(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("拖入原始素材，系统自动计算 phash 并写入文件元数据（备注字段）和数据库"))
        self._drop_raw = DropArea("拖入素材（可多个）", multi=True); v.addWidget(self._drop_raw)
        btn = QPushButton("⚡  执行批量登记")
        btn.setStyleSheet("background:#2980b9;color:#fff;height:40px;font-size:14px;")
        btn.clicked.connect(self._do_register); v.addWidget(btn)
        return w

    # ── Tab2：处理关联 ──────────────────────────────────
    def _tab_derive(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("用于：原素材经修改/转格式后，建立来源追踪。例：原图→修图、图→视频"))
        grp = QHBoxLayout()
        self._drop_src = DropArea("① 来源素材（原始）")
        self._drop_dst = DropArea("② 衍生素材（修改后）")
        grp.addWidget(self._drop_src); grp.addWidget(self._drop_dst); v.addLayout(grp)
        row = QHBoxLayout(); row.addWidget(QLabel("关系类型："))
        self._cmb_rel = QComboBox()
        self._cmb_rel.addItems(["image_to_image（修图）","image_to_video（生视频）",
                                  "video_to_video（视频剪辑）","其他"])
        row.addWidget(self._cmb_rel); row.addStretch(); v.addLayout(row)
        btn = QPushButton("🔗  建立衍生关联")
        btn.setStyleSheet("background:#e67e22;color:#fff;height:40px;font-size:14px;")
        btn.clicked.connect(self._do_derive); v.addWidget(btn)
        return w

    # ── Tab3：成品封装 ──────────────────────────────────
    def _tab_compose(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("用于：多素材合并为一个成品，记录所有组件的来源和作者"))
        grp = QHBoxLayout()
        self._drop_parts   = DropArea("① 组件素材（可多个）", multi=True)
        self._drop_product = DropArea("② 最终成品")
        grp.addWidget(self._drop_parts); grp.addWidget(self._drop_product); v.addLayout(grp)
        btn = QPushButton("🔒  封装成品")
        btn.setStyleSheet("background:#27ae60;color:#fff;height:40px;font-size:14px;")
        btn.clicked.connect(self._do_compose); v.addWidget(btn)
        return w

    # ── Tab4：Canva 模板 ────────────────────────────────
    def _tab_canva(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("为一组素材生成唯一ID，将ID复制到Canva模板名称中（如：夏日促销【20260313210000】）"))
        self._drop_canva = DropArea("拖入此次Canva使用的所有素材", multi=True); v.addWidget(self._drop_canva)
        r1 = QHBoxLayout(); r1.addWidget(QLabel("模板名称："))
        self._canva_name = QLineEdit(); self._canva_name.setPlaceholderText("例：夏日促销Banner")
        r1.addWidget(self._canva_name); v.addLayout(r1)
        r2 = QHBoxLayout(); r2.addWidget(QLabel("备注："))
        self._canva_remark = QLineEdit(); r2.addWidget(self._canva_remark); v.addLayout(r2)
        btn = QPushButton("🎨  生成模板ID并登记")
        btn.setStyleSheet("background:#9b59b6;color:#fff;height:40px;font-size:14px;")
        btn.clicked.connect(self._do_canva); v.addWidget(btn)
        # ID 显示行 + 一键复制按钮
        id_row = QHBoxLayout()
        self._canva_id_lbl = QLabel("(点击生成后显示)")
        self._canva_id_lbl.setStyleSheet(
            "font-size:17px;font-weight:bold;color:#2c3e50;"
            "background:#ecf0f1;padding:12px;border-radius:6px;"
        )
        self._canva_id_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_copy_canva = QPushButton("\U0001f4cb 复制ID")
        self._btn_copy_canva.setFixedWidth(90)
        self._btn_copy_canva.setEnabled(False)
        self._btn_copy_canva.clicked.connect(self._copy_canva_id)
        id_row.addWidget(self._canva_id_lbl, 1); id_row.addWidget(self._btn_copy_canva)
        v.addLayout(id_row)
        self._tbl_canva = QTableWidget(0, 4)
        self._tbl_canva.setHorizontalHeaderLabels(["模板ID", "模板名称", "创建人", "素材数"])
        self._tbl_canva.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._tbl_canva)
        btn2 = QPushButton("🔄 刷新列表"); btn2.clicked.connect(self._refresh_canva); v.addWidget(btn2)
        return w

    # ── Tab5：源迹查询 ──────────────────────────────────────────────
    def _tab_query(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(6, 4, 6, 4)
        sp = QSplitter(Qt.Orientation.Horizontal)
        # ── 左侧控制区
        left = QWidget(); lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4); lv.setSpacing(5)
        self._drop_query = DropArea("拖入文件（支持多个）", multi=True)
        lv.addWidget(self._drop_query)
        btn = QPushButton("🔍  批量查询源迹")
        btn.setStyleSheet("background:#8e44ad;color:#fff;height:36px;font-size:13px;")
        btn.clicked.connect(self._do_query); lv.addWidget(btn)
        sep = QLabel("── Canva 模板ID 查询 ──")
        sep.setStyleSheet("color:#888;font-size:11px;margin-top:4px;")
        lv.addWidget(sep)
        canva_row = QHBoxLayout()
        self._canva_id_search = QLineEdit()
        self._canva_id_search.setPlaceholderText("输入Canva模板ID…")
        btn_cv = QPushButton("🎨")
        btn_cv.setFixedWidth(34); btn_cv.setStyleSheet("background:#c0392b;color:#fff;")
        btn_cv.clicked.connect(self._do_query_canva)
        canva_row.addWidget(self._canva_id_search); canva_row.addWidget(btn_cv)
        lv.addLayout(canva_row)
        btn_copy_all = QPushButton("📋  全部复制（Google Sheets）")
        btn_copy_all.setStyleSheet(
            "background:#27ae60;color:#fff;height:32px;font-size:12px;")
        btn_copy_all.clicked.connect(self._copy_all_lineage); lv.addWidget(btn_copy_all)
        btn_clr = QPushButton("🗑  清空结果")
        btn_clr.setStyleSheet("height:28px;font-size:12px;")
        btn_clr.clicked.connect(self._clear_query_results); lv.addWidget(btn_clr)
        lv.addStretch()
        sp.addWidget(left)
        # ── 右侧结果区（滚动卡片）
        self._query_scroll = QScrollArea()
        self._query_scroll.setWidgetResizable(True)
        self._query_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._query_result_box = QWidget()
        self._query_result_lay = QVBoxLayout(self._query_result_box)
        self._query_result_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._query_result_lay.setSpacing(6)
        self._query_placeholder = QLabel(
            "← 拖入文件后点击查询，每个文件的源迹结果将在此展示"
        )
        self._query_placeholder.setStyleSheet(
            "color:#aaa;font-size:13px;padding:30px;")
        self._query_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._query_result_lay.addWidget(self._query_placeholder)
        self._query_scroll.setWidget(self._query_result_box)
        sp.addWidget(self._query_scroll)
        sp.setSizes([270, 890]); v.addWidget(sp)
        self._lineage_results = []
        return w
    # ── Tab6：全量库 ────────────────────────────────────
    def _tab_library(self):
        w = QWidget(); v = QVBoxLayout(w)
        sr = QHBoxLayout()
        self._search_box = QLineEdit(); self._search_box.setPlaceholderText("搜索文件名 / 作者…")
        self._search_box.textChanged.connect(self._filter_lib)
        btn = QPushButton("🔄 刷新"); btn.clicked.connect(self._refresh_lib)
        sr.addWidget(self._search_box); sr.addWidget(btn); v.addLayout(sr)
        self._tbl_lib = QTableWidget(0, 6)
        self._tbl_lib.setHorizontalHeaderLabels(["文件名","类型","作者","时间","大小","pHash前16位"])
        self._tbl_lib.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tbl_lib.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v.addWidget(self._tbl_lib)
        return w

    # ═══════════════════ 后台任务 ═════════════════════
    def _bg(self, fn, done_cb=None, msg="操作"):
        w = Worker(fn)
        w.done.connect(lambda r: (done_cb(r) if done_cb else None,
                                   self._log(f"✅ {msg}完成")))
        w.error.connect(lambda e: self._log(f"❌ {msg}失败: {e}"))
        w.start(); self._workers.append(w)

    def _log(self, msg):
        self._log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")

    # ═══════════════════ 业务处理 ═════════════════════
    def _do_register(self):
        fps = self._drop_raw.files()
        if not fps: QMessageBox.warning(self, "提示", "请先拖入素材文件"); return
        op = self._cfg['user_name']
        def task():
            for fp in fps:
                img = get_thumbnail(fp)
                if img is None: gui_log(f"⚠️ 无法读取: {os.path.basename(fp)}"); continue
                ph, src = get_phash_from_file(fp, img)
                if not ph: gui_log(f"❌ phash计算失败: {os.path.basename(fp)}"); continue
                fname = os.path.basename(fp); atype = get_asset_type(fp)
                fsize = get_file_size(fp); now = datetime.now()
                rec = {"phash": ph, "filename": fname, "asset_type": atype,
                       "file_size": fsize, "producer": op, "created_at": now.isoformat()}
                write_metadata(fp, rec)
                db.upsert_asset(ph, fname, atype, fsize, op, now,
                                json.dumps(rec, ensure_ascii=False, default=str),
                                make_thumb_bytes(img))
                gui_log(f"✅ 已登记: {fname}  作者:{op}  phash:{ph}")
            return {}
        self._bg(task, msg="素材登记")

    def _do_derive(self):
        src_fp = self._drop_src.file(); dst_fp = self._drop_dst.file()
        if not src_fp or not dst_fp:
            QMessageBox.warning(self, "提示", "请同时拖入来源素材和衍生素材"); return
        op = self._cfg['user_name']
        rel_type = self._cmb_rel.currentText().split("（")[0]
        def task():
            ph_src, rec_src = ensure_registered(src_fp, op)
            ph_dst, rec_dst = ensure_registered(dst_fp, op)
            if not ph_src or not ph_dst:
                gui_log("❌ 素材登记失败，无法建立关联"); return {}
            db.add_derive(ph_src, ph_dst, rel_type, op,
                          remark=f"{os.path.basename(src_fp)} → {os.path.basename(dst_fp)}")
            src_prod = rec_src.get('producer', op) if isinstance(rec_src, dict) else op
            src_chain = db.get_ancestry_string(ph_src)
            dst_rec = read_metadata(dst_fp) or {}
            dst_rec.update({
                "phash": ph_dst, "filename": os.path.basename(dst_fp),
                "asset_type": get_asset_type(dst_fp), "file_size": get_file_size(dst_fp),
                "producer": op, "created_at": datetime.now().isoformat(),
                "derived_from": {"phash": ph_src, "filename": os.path.basename(src_fp),
                                 "producer": src_prod, "rel_type": rel_type,
                                 "ancestry_chain": src_chain}
            })
            write_metadata(dst_fp, dst_rec)
            db.upsert_asset(ph_dst, os.path.basename(dst_fp), get_asset_type(dst_fp),
                            get_file_size(dst_fp), op, datetime.now(),
                            json.dumps(dst_rec, ensure_ascii=False, default=str))
            gui_log(f"✅ 关联: [{src_prod}]{os.path.basename(src_fp)}"
                    f" →({rel_type})→ [{op}]{os.path.basename(dst_fp)}")
            return {}
        self._bg(task, msg="关联")

    def _do_compose(self):
        part_fps   = self._drop_parts.files()
        product_fp = self._drop_product.file()
        if not product_fp: QMessageBox.warning(self, "提示", "请拖入最终成品文件"); return
        op = self._cfg['user_name']
        def task():
            ph_product, _ = ensure_registered(product_fp, op)
            if not ph_product: gui_log("❌ 成品文件无法处理"); return {}
            part_phashes = []; part_info = []
            for fp in part_fps:
                ph, rec = ensure_registered(fp, op)
                if ph:
                    part_phashes.append(ph)
                    prod = rec.get('producer', op) if isinstance(rec, dict) else op
                    part_info.append({"phash": ph, "filename": os.path.basename(fp),
                                      "producer": prod, "asset_type": get_asset_type(fp)})
                    gui_log(f"  ✅ 组件: [{prod}] {os.path.basename(fp)}")
            if part_phashes:
                db.add_compose(part_phashes, ph_product)
            for info in part_info:
                info['ancestry_chain'] = db.get_ancestry_string(info['phash'])
            product_rec = read_metadata(product_fp) or {}
            product_rec.update({
                "phash": ph_product, "filename": os.path.basename(product_fp),
                "asset_type": get_asset_type(product_fp), "file_size": get_file_size(product_fp),
                "producer": op, "created_at": datetime.now().isoformat(),
                "composed_from": part_info
            })
            write_metadata(product_fp, product_rec)
            db.upsert_asset(ph_product, os.path.basename(product_fp), get_asset_type(product_fp),
                            get_file_size(product_fp), op, datetime.now(),
                            json.dumps(product_rec, ensure_ascii=False, default=str))
            gui_log(f"✅ 成品封装: [{op}]{os.path.basename(product_fp)}  组件{len(part_phashes)}个")
            return {}
        self._bg(task, msg="封装")

    def _do_canva(self):
        fps = self._drop_canva.files()
        if not fps: QMessageBox.warning(self, "提示", "请拖入素材"); return
        tname  = self._canva_name.text().strip() or "未命名模板"
        remark = self._canva_remark.text().strip()
        op     = self._cfg['user_name']
        tid    = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        def task():
            phashes = []
            for fp in fps:
                ph, rec = ensure_registered(fp, op)
                if ph:
                    phashes.append(ph)
                    prod = rec.get('producer','?') if isinstance(rec, dict) else '?'
                    gui_log(f"  ✅ 素材: [{prod}] {os.path.basename(fp)}")
            if not phashes: gui_log("❌ 没有有效素材"); return {"id": None}
            db.add_canva_template(tid, tname, op, phashes, remark)
            gui_log(f"✅ 模板ID: 【{tid}】  素材{len(phashes)}个")
            return {"id": tid}
        def done(r):
            if r.get("id"):
                self._last_canva_id = r['id']
                self._canva_id_lbl.setText(f"【{r['id']}】")
                self._btn_copy_canva.setEnabled(True)
                self._refresh_canva()
        self._bg(task, done, msg="Canva登记")

    def _copy_canva_id(self):
        if self._last_canva_id:
            QApplication.clipboard().setText(f"【{self._last_canva_id}】")
            self._log(f"\u2705 已复制到剪切板：【{self._last_canva_id}】")

    # ────────────────────────────────────────────────────────────
    # 源迹查询律 — 卡片构建 & 复制
    # ────────────────────────────────────────────────────────────
    def _build_result_card(self, fp, img, lineage) -> QFrame:
        """\u4e3a单个文件构建源迹结果卡片：缩略图(1:1比例) + 家谱树 + 复制按钮"""
        card = QFrame()
        card.setStyleSheet(
            "QFrame{border:1px solid #d5d8dc;border-radius:8px;"
            "background:#fff;margin:1px;}")
        row_lay = QHBoxLayout(card); row_lay.setContentsMargins(8, 8, 8, 8); row_lay.setSpacing(8)
        # 缩略图（保持原始比例）
        lbl_th = QLabel(); lbl_th.setFixedSize(88, 88)
        lbl_th.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_th.setStyleSheet("border:1px solid #bbb;background:#111;border-radius:4px;")
        if img is not None:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h, w_img, ch = rgb.shape
            qi  = QImage(rgb.data, w_img, h, ch * w_img, QImage.Format.Format_RGB888)
            pm  = QPixmap.fromImage(qi).scaled(
                86, 86, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            lbl_th.setPixmap(pm)
        else:
            lbl_th.setText("?"); lbl_th.setStyleSheet("color:#aaa;font-size:22px;")
        row_lay.addWidget(lbl_th)
        # 右侧信息区
        rw = QWidget(); rv2 = QVBoxLayout(rw)
        rv2.setContentsMargins(0, 0, 0, 0); rv2.setSpacing(4)
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        lbl_fn = QLabel(os.path.basename(fp))
        lbl_fn.setStyleSheet("font-weight:bold;font-size:12px;color:#2c3e50;")
        hdr.addWidget(lbl_fn)
        if lineage:
            ast_d = lineage['asset']
            lbl_meta = QLabel(
                f"  {(ast_d.get('phash','') or '')[:16]}…"
                f"  \U0001f464{ast_d.get('producer','?')}"
                f"  \U0001f4c5{str(ast_d.get('created_at',''))[:10]}"
            )
            lbl_meta.setStyleSheet("font-size:11px;color:#7f8c8d;")
            hdr.addWidget(lbl_meta)
        hdr.addStretch()
        btn_cp = QPushButton("📋 复制此行")
        btn_cp.setFixedWidth(76); btn_cp.setFixedHeight(22)
        btn_cp.setStyleSheet("font-size:10px;background:#ecf0f1;border:1px solid #bdc3c7;")
        btn_cp.clicked.connect(lambda _, f=fp, lg=lineage: self._copy_lineage_row(f, lg))
        hdr.addWidget(btn_cp)
        rv2.addLayout(hdr)
        if not lineage:
            miss = QLabel("❓ 该文件未在数据库登记，无源迹信息")
            miss.setStyleSheet("color:#e74c3c;font-size:11px;padding:2px;")
            rv2.addWidget(miss)
        else:
            tree = QTreeWidget()
            tree.setHeaderLabels(["素材 / 关系", "制作人", "时间", "类型"])
            tree.setColumnWidth(0, 260); tree.setColumnWidth(1, 80)
            tree.setColumnWidth(2, 130); tree.setColumnWidth(3, 60)
            tree.setAlternatingRowColors(True)
            tree.setMinimumHeight(60); tree.setMaximumHeight(220)
            self._fill_lineage_tree(tree, lineage)
            rv2.addWidget(tree)
        row_lay.addWidget(rw, 1)
        return card

    def _fill_lineage_tree(self, tree: QTreeWidget, lineage: dict):
        """\u5c06 lineage dict \u586b\u5145\u5230\u6307\u5b9a QTreeWidget"""
        tree.clear()
        ast_d = lineage['asset']
        root = QTreeWidgetItem([
            f"\U0001f3af {ast_d.get('filename','?')}",
            ast_d.get('producer', '?'),
            str(ast_d.get('created_at', ''))[:16],
            ast_d.get('asset_type', '?'),
        ])
        ff = QFont(); ff.setBold(True); root.setFont(0, ff)
        root.setForeground(0, QColor("#2c3e50"))
        if lineage.get('derived_from'):
            sec = QTreeWidgetItem(["⬆ 衍生来源（多级）"])
            sec.setForeground(0, QColor("#e67e22"))
            for r in lineage['derived_from']:
                sec.addChild(self._make_ancestor_item(r))
            root.addChild(sec); sec.setExpanded(True)
        if lineage.get('derived_to'):
            sec = QTreeWidgetItem(["⬇ 衍生出（多级）"])
            sec.setForeground(0, QColor("#27ae60"))
            for r in lineage['derived_to']:
                sec.addChild(self._make_descendant_item(r))
            root.addChild(sec); sec.setExpanded(True)
        if lineage.get('composed_from'):
            sec = QTreeWidgetItem(["📦 组件来源"])
            sec.setForeground(0, QColor("#8e44ad"))
            for r in lineage['composed_from']:
                sec.addChild(self._make_component_item(r))
            root.addChild(sec); sec.setExpanded(True)
        if lineage.get('used_in'):
            sec = QTreeWidgetItem(["🎦 被用于成品"])
            sec.setForeground(0, QColor("#2980b9"))
            for r in lineage['used_in']:
                child = QTreeWidgetItem([
                    f"  \U0001f4c4 {r.get('filename','?')}",
                    r.get('producer','?'),
                    str(r.get('created_at',''))[:16],
                    r.get('asset_type','?'),
                ])
                sec.addChild(child)
            root.addChild(sec); sec.setExpanded(True)
        if lineage.get('canva_used'):
            sec = QTreeWidgetItem([f"\U0001f3a8 Canva\u6a21\u677f ({len(lineage['canva_used'])}\u4e2a)"])
            sec.setForeground(0, QColor("#c0392b"))
            for t in lineage['canva_used']:
                child = QTreeWidgetItem([
                    f"  \U0001f3a8 {t.get('template_name','?')}",
                    t.get('creator','?'), str(t.get('created_at',''))[:16], "canva",
                ])
                sec.addChild(child)
            root.addChild(sec); sec.setExpanded(True)
        tree.addTopLevelItem(root); root.setExpanded(True)

    def _lineage_to_tsv(self, fp: str, lineage) -> str:
        """\u8f6c\u6362\u4e3a Google Sheets \u53ef\u76f4\u63a5\u7c98\u8d34\u7684 TSV \u683c\u5f0f\uff08\u542b\u8868\u5934\u65f6\u4e00\u884c\uff09"""
        fname = os.path.basename(fp)
        if not lineage:
            return f"{fname}\t\u672a\u767b\u8bb0\t\t\t\t\t\t"
        ast_d = lineage['asset']
        ph    = ast_d.get('phash', '')
        prod  = ast_d.get('producer', '')
        date  = str(ast_d.get('created_at', ''))[:10]
        derived = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('derived_from', [])
        )
        parts = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('composed_from', [])
        )
        used = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('used_in', [])
        )
        canva = '; '.join(
            f"{t.get('template_id','')}({t.get('template_name','?')})"
            for t in lineage.get('canva_used', [])
        )
        return f"{fname}\t{ph}\t{prod}\t{date}\t{derived}\t{parts}\t{used}\t{canva}"

    def _copy_lineage_row(self, fp: str, lineage):
        QApplication.clipboard().setText(self._lineage_to_tsv(fp, lineage))
        self._log(f"\u2705 \u5df2\u590d\u5236: {os.path.basename(fp)}")

    def _copy_all_lineage(self):
        if not self._lineage_results:
            QMessageBox.information(self, "\u63d0\u793a", "\u6682\u65e0\u67e5\u8be2\u7ed3\u679c"); return
        header = "\u6587\u4ef6\u540d\tphash\t\u5236\u4f5c\u4eba\t\u65e5\u671f\t\u884d\u751f\u6765\u6e90\t\u5c01\u88c5\u7ec4\u4ef6\t\u88ab\u7528\u4e8e\tCanva\u6a21\u677f"
        rows = [self._lineage_to_tsv(r['fp'], r['lineage']) for r in self._lineage_results]
        QApplication.clipboard().setText(header + '\n' + '\n'.join(rows))
        self._log(f"\u2705 \u5df2\u590d\u5236 {len(rows)} \u6761\u8bb0\u5f55\uff08\u542b\u8868\u5934\uff09\uff0c\u53ef\u76f4\u63a5\u7c98\u8d34\u5230 Google Sheets")

    def _clear_query_results(self):
        while self._query_result_lay.count():
            item = self._query_result_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._lineage_results = []
        self._query_result_lay.addWidget(self._query_placeholder)
        self._query_placeholder.show()

    def _make_component_item(self, row) -> QTreeWidgetItem:
        """构建封装组件树节点（递归：含衍生祖先 + 子封装层级）"""
        ph = row.get('part_phash') or row.get('phash', '')
        item = QTreeWidgetItem([
            f"  \U0001f4c4 {row.get('filename','?')}",
            row.get('producer', '?'),
            str(row.get('created_at', ''))[:16],
            row.get('asset_type', '?'),
            (ph or '')[:16] + "…"
        ])
        item.setForeground(0, QColor("#8e44ad"))
        if row.get('part_role'):
            rn = QTreeWidgetItem([f"    角色: {row['part_role']}"])
            rn.setForeground(0, QColor("#888")); item.addChild(rn)
        # 该组件的衍生祖先
        for anc in row.get('ancestors', []):
            item.addChild(self._make_ancestor_item(anc))
        # 该组件本身也是封装品时，展示其子组件层级
        if row.get('sub_parts'):
            sub_sec = QTreeWidgetItem(["  🔧 子组件（该素材本身也是封装品）"])
            sub_sec.setForeground(0, QColor("#6c3483"))
            for sub in row['sub_parts']:
                sub_sec.addChild(self._make_component_item(sub))
            item.addChild(sub_sec); sub_sec.setExpanded(True)
        return item

    def _make_ancestor_item(self, row) -> QTreeWidgetItem:
        """构建衍生来源树节点（递归向上）"""
        item = QTreeWidgetItem([
            f"  \U0001f4c4 {row.get('filename','?')}",
            row.get('producer', '?'),
            str(row.get('created_at', ''))[:16],
            row.get('asset_type', '?'),
            (row.get('src_phash', '') or '')[:16] + "…"
        ])
        item.setForeground(0, QColor("#d35400"))
        info = []
        if row.get('rel_type'):  info.append(f"关系: {row['rel_type']}")
        if row.get('operator'): info.append(f"操作人: {row['operator']}")
        if info:
            rn = QTreeWidgetItem([f"    {'  |  '.join(info)}"])
            rn.setForeground(0, QColor("#888")); item.addChild(rn)
        for anc in row.get('ancestors', []):
            item.addChild(self._make_ancestor_item(anc))
        return item

    def _make_descendant_item(self, row) -> QTreeWidgetItem:
        """构建衍生出树节点（递归向下）"""
        item = QTreeWidgetItem([
            f"  \U0001f4c4 {row.get('filename','?')}",
            row.get('producer', '?'),
            str(row.get('created_at', ''))[:16],
            row.get('asset_type', '?'),
            (row.get('dst_phash', '') or '')[:16] + "…"
        ])
        item.setForeground(0, QColor("#16a085"))
        info = []
        if row.get('rel_type'):  info.append(f"关系: {row['rel_type']}")
        if row.get('operator'): info.append(f"操作人: {row['operator']}")
        if info:
            rn = QTreeWidgetItem([f"    {'  |  '.join(info)}"])
            rn.setForeground(0, QColor("#888")); item.addChild(rn)
        for desc in row.get('descendants', []):
            item.addChild(self._make_descendant_item(desc))
        return item

    def _refresh_canva(self):
        rows = db.get_all_canva(); self._tbl_canva.setRowCount(0)
        for r in rows:
            try: ph_list = json.loads(r['asset_phashes']) if r['asset_phashes'] else []
            except: ph_list = []
            idx = self._tbl_canva.rowCount(); self._tbl_canva.insertRow(idx)
            self._tbl_canva.setItem(idx,0, QTableWidgetItem(r['template_id']))
            self._tbl_canva.setItem(idx,1, QTableWidgetItem(r['template_name'] or ""))
            self._tbl_canva.setItem(idx,2, QTableWidgetItem(r['creator'] or ""))
            self._tbl_canva.setItem(idx,3, QTableWidgetItem(str(len(ph_list))))

    def _do_query(self):
        fps = self._drop_query.files()
        if not fps: QMessageBox.warning(self, "提示", "请先拖入要查询的文件"); return
        def task():
            results = []
            for fp in fps:
                try:
                    img = get_thumbnail(fp)
                    ph, _ = get_phash_from_file(fp, img)
                    lineage = db.get_lineage(ph) if ph else None
                except Exception as e:
                    gui_log(f"❌ {os.path.basename(fp)}: {e}")
                    img, lineage = None, None
                results.append({'fp': fp, 'img': img, 'lineage': lineage})
            return results
        def done(results):
            if hasattr(self, '_query_placeholder'):
                self._query_placeholder.hide()
            self._lineage_results.extend(results)
            for res in results:
                card = self._build_result_card(res['fp'], res['img'], res['lineage'])
                self._query_result_lay.addWidget(card)
            self._log(f"✅ 源迹查询完成，共 {len(results)} 个文件")
        self._bg(task, done, msg="源迹查询")
    def _do_query_canva(self):
        tid = self._canva_id_search.text().strip()
        if not tid:
            QMessageBox.warning(self, "提示", "请输入Canva模板ID"); return
        def task():
            return db.get_lineage_by_canva_id(tid)
        def done(result):
            if not result:
                self._log(f"❓ Canva模板 [{tid}] 未找到"); return
            if hasattr(self, '_query_placeholder'):
                self._query_placeholder.hide()
            tmpl    = result['template']
            tid_val = tmpl.get('template_id', '')
            tname   = tmpl.get('template_name', '?')
            tcreator = tmpl.get('creator', '?')
            card = QFrame()
            card.setStyleSheet(
                "QFrame{border:2px solid #9b59b6;border-radius:8px;"
                "background:#fdf8ff;margin:1px;}")
            cv2_lay = QVBoxLayout(card); cv2_lay.setContentsMargins(8, 8, 8, 8)
            hdr = QHBoxLayout()
            lbl = QLabel(f"🎨 {tname}  【{tid_val}】  👤{tcreator}")
            lbl.setStyleSheet("font-weight:bold;font-size:13px;color:#6c3483;")
            hdr.addWidget(lbl); hdr.addStretch()
            def _make_copy_fn(t):
                def fn():
                    QApplication.clipboard().setText(f"【{t}】")
                    self._log(f"✅ 已复制: 【{t}】")
                return fn
            btn_cp = QPushButton("📋 复制模板ID")
            btn_cp.setFixedWidth(90); btn_cp.setFixedHeight(22)
            btn_cp.setStyleSheet("font-size:10px;background:#ecf0f1;border:1px solid #bdc3c7;")
            btn_cp.clicked.connect(_make_copy_fn(tid_val))
            hdr.addWidget(btn_cp); cv2_lay.addLayout(hdr)
            tree = QTreeWidget()
            tree.setHeaderLabels(["素材 / 关系", "制作人", "时间", "类型"])
            tree.setColumnWidth(0, 280); tree.setColumnWidth(1, 80)
            tree.setColumnWidth(2, 130); tree.setColumnWidth(3, 60)
            tree.setAlternatingRowColors(True)
            tree.setMinimumHeight(80); tree.setMaximumHeight(300)
            for asset in result['assets']:
                fname_a  = asset.get('filename', '?')
                prod_a   = asset.get('producer', '?')
                date_a   = str(asset.get('created_at', ''))[:16]
                atype_a  = asset.get('asset_type', '?')
                a_item = QTreeWidgetItem([f"  🖼 {fname_a}", prod_a, date_a, atype_a])
                a_item.setForeground(0, QColor("#2c3e50"))
                for anc in asset.get('ancestors', []):
                    a_item.addChild(self._make_ancestor_item(anc))
                a_item.setExpanded(True)
                tree.addTopLevelItem(a_item)
            cv2_lay.addWidget(tree)
            self._query_result_lay.addWidget(card)
            self._log(f"✅ 模板源迹: {tname}  素材{len(result['assets'])}个")
        self._bg(task, done, msg="Canva模板源迹")
    def _refresh_lib(self):
        self._lib_data = db.get_all_assets(); self._fill_lib(self._lib_data)

    def _fill_lib(self, rows):
        self._tbl_lib.setRowCount(0)
        for r in rows:
            idx = self._tbl_lib.rowCount(); self._tbl_lib.insertRow(idx)
            self._tbl_lib.setItem(idx,0, QTableWidgetItem(r.get('filename','')))
            self._tbl_lib.setItem(idx,1, QTableWidgetItem(r.get('asset_type','')))
            self._tbl_lib.setItem(idx,2, QTableWidgetItem(r.get('producer','')))
            self._tbl_lib.setItem(idx,3, QTableWidgetItem(str(r.get('created_at',''))[:16]))
            self._tbl_lib.setItem(idx,4, QTableWidgetItem(f"{(r.get('file_size') or 0)/1024:.1f} KB"))
            ph = r.get('phash','')
            self._tbl_lib.setItem(idx,5, QTableWidgetItem((ph or '')[:16] + "…"))

    def _filter_lib(self, kw):
        kw = kw.lower()
        if not kw:
            self._fill_lib(self._lib_data); return
        self._fill_lib([r for r in self._lib_data
                        if kw in (r.get('filename') or '').lower()
                        or kw in (r.get('producer') or '').lower()])

    # ═══════════════════ 设置 ═════════════════════════
    def _dlg_settings(self):
        d = QDialog(self); d.setWindowTitle("系统设置"); d.setMinimumWidth(420)
        lay = QFormLayout(d)
        fn = QLineEdit(self._cfg['user_name'])
        fh = QLineEdit(db.conf['host'])
        fp = QLineEdit(str(db.conf['port']))
        fu = QLineEdit(db.conf['user'])
        fw = QLineEdit(db.conf['password']); fw.setEchoMode(QLineEdit.EchoMode.Password)
        fd = QLineEdit(db.conf['db'])
        lay.addRow("操作员姓名：", fn); lay.addRow("MySQL 地址：", fh)
        lay.addRow("MySQL 端口：", fp); lay.addRow("MySQL 用户：", fu)
        lay.addRow("MySQL 密码：", fw); lay.addRow("数据库名：",   fd)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(d.accept); btns.rejected.connect(d.reject); lay.addRow(btns)
        if d.exec():
            self._cfg['user_name'] = fn.text(); save_config(self._cfg)
            db.conf.update({'host': fh.text(), 'port': int(fp.text() or 3306),
                            'user': fu.text(), 'password': fw.text(), 'db': fd.text()})
            db.save_conf(db.conf)
            ok, msg = db.connect()
            self._lbl_user.setText(f"👤  操作员：{self._cfg['user_name']}")
            self._log("✅ 设置保存，数据库重连" + ("成功" if ok else f"失败: {msg}"))


    # ── Tab7：批量扫描 ─────────────────────────────────
    def _tab_batch_scan(self):
        w = QWidget(); v = QVBoxLayout(w)

        # ─── 人员代码管理区 ──────────────────────────────
        code_box = QFrame()
        code_box.setStyleSheet(
            "QFrame{border:1px solid #bdc3c7;border-radius:6px;"
            "background:#f8f9fa;padding:4px;margin-bottom:4px;}")
        cv = QVBoxLayout(code_box)
        cv.addWidget(QLabel("👤  人员代码对照表  （文件名中的CODE → 真实姓名）"))

        # 表格：展示层
        self._code_table = QTableWidget(0, 3)
        self._code_table.setHorizontalHeaderLabels(["CODE", "真实姓名", "操作"])
        self._code_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._code_table.setMaximumHeight(160)
        self._code_table.setAlternatingRowColors(True)
        cv.addWidget(self._code_table)

        # 添加行
        add_row = QHBoxLayout()
        self._code_input = QLineEdit(); self._code_input.setPlaceholderText("CODE（如 KS、57）")
        self._code_input.setFixedWidth(100)
        self._name_input = QLineEdit(); self._name_input.setPlaceholderText("真实姓名（如 张三）")
        self._name_input.setFixedWidth(150)
        btn_add_code = QPushButton("➕ 添加")
        btn_add_code.setFixedWidth(70)
        btn_add_code.clicked.connect(self._add_producer_code)
        btn_save_codes = QPushButton("💾 保存")
        btn_save_codes.setFixedWidth(70)
        btn_save_codes.clicked.connect(self._save_producer_codes)
        add_row.addWidget(QLabel("CODE:"))
        add_row.addWidget(self._code_input)
        add_row.addWidget(QLabel("姓名:"))
        add_row.addWidget(self._name_input)
        add_row.addWidget(btn_add_code)
        add_row.addWidget(btn_save_codes)
        add_row.addStretch()
        cv.addLayout(add_row)
        v.addWidget(code_box)
        self._load_code_table()  # 初始化载入已保存的表

        desc = QLabel(
            "批量扫描文件夹，将所有媒体文件自动登记入库。"
            "文件名中的CODE会自动匹配制作人，识别不到则写入「未知」。"
            "文件夹名含【ID】自动建立 Canva 模板关联。"
        )
        desc.setStyleSheet("color:#555;font-size:12px;padding:4px;")
        v.addWidget(desc)

        # 文件夹路径
        fr = QHBoxLayout()
        fr.addWidget(QLabel("扫描文件夹："))
        self._scan_path = QLineEdit()
        self._scan_path.setPlaceholderText("粘贴路径，或点击右侧选择…")
        fr.addWidget(self._scan_path)
        btn_br = QPushButton("📂 选择"); btn_br.setFixedWidth(80)
        btn_br.clicked.connect(self._browse_scan_folder)
        fr.addWidget(btn_br); v.addLayout(fr)

        # 操作按钮
        br = QHBoxLayout()
        self._btn_scan_start = QPushButton("▶  开始扫描")
        self._btn_scan_start.setStyleSheet(
            "background:#27ae60;color:#fff;height:40px;font-size:14px;")
        self._btn_scan_start.clicked.connect(self._do_scan_start)
        self._btn_scan_stop = QPushButton("⏹  停止")
        self._btn_scan_stop.setStyleSheet(
            "background:#c0392b;color:#fff;height:40px;font-size:14px;")
        self._btn_scan_stop.setEnabled(False)
        self._btn_scan_stop.clicked.connect(self._do_scan_stop)
        br.addWidget(self._btn_scan_start); br.addWidget(self._btn_scan_stop)
        br.addStretch(); v.addLayout(br)

        # 进度条
        self._scan_bar = QProgressBar()
        self._scan_bar.setRange(0, 100); self._scan_bar.setValue(0)
        self._scan_bar.setTextVisible(True)
        self._scan_bar.setStyleSheet("height:20px;")
        v.addWidget(self._scan_bar)

        # 统计标签
        self._scan_stats = QLabel("等待开始…")
        self._scan_stats.setStyleSheet(
            "font-size:13px;color:#2c3e50;padding:4px;"
            "background:#ecf0f1;border-radius:4px;")
        v.addWidget(self._scan_stats)

        # 扫描日志（独立于主日志）
        self._scan_log = QTextEdit(); self._scan_log.setReadOnly(True)
        self._scan_log.setStyleSheet(
            "background:#0d1117;color:#58a6ff;font-size:11px;font-family:Consolas,monospace;")
        v.addWidget(self._scan_log)
        return w

    def _browse_scan_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择扫描文件夹")
        if d:
            self._scan_path.setText(d)

    def _load_code_table(self):
        """从文件加载人员代码表并填充到 QTableWidget"""
        codes = load_producer_codes()
        self._code_table.setRowCount(0)
        for code, name in codes.items():
            self._insert_code_row(code, name)

    def _insert_code_row(self, code, name):
        idx = self._code_table.rowCount()
        self._code_table.insertRow(idx)
        self._code_table.setItem(idx, 0, QTableWidgetItem(code))
        self._code_table.setItem(idx, 1, QTableWidgetItem(name))
        btn_del = QPushButton("🗑")
        btn_del.setFixedWidth(32)
        btn_del.clicked.connect(lambda _, r=idx: self._del_code_row(r))
        self._code_table.setCellWidget(idx, 2, btn_del)

    def _del_code_row(self, row):
        # 按钮绑定的行号可能因删除偏移，重新找
        btn = self.sender()
        for r in range(self._code_table.rowCount()):
            if self._code_table.cellWidget(r, 2) is btn:
                self._code_table.removeRow(r); break

    def _add_producer_code(self):
        code = self._code_input.text().strip().upper()
        name = self._name_input.text().strip()
        if not code or not name:
            QMessageBox.warning(self, "提示", "CODE 和姓名不能为空"); return
        # 检查是否已存在
        for r in range(self._code_table.rowCount()):
            if self._code_table.item(r, 0) and \
               self._code_table.item(r, 0).text().upper() == code:
                self._code_table.item(r, 1).setText(name)
                self._code_input.clear(); self._name_input.clear()
                return
        self._insert_code_row(code, name)
        self._code_input.clear(); self._name_input.clear()

    def _save_producer_codes(self):
        codes = {}
        for r in range(self._code_table.rowCount()):
            k = self._code_table.item(r, 0)
            v = self._code_table.item(r, 1)
            if k and v and k.text().strip():
                codes[k.text().strip().upper()] = v.text().strip()
        save_producer_codes(codes)
        self._log(f"✅ 人员代码表已保存，共 {len(codes)} 条")

    def _get_code_map(self) -> dict:
        """从当前表格读取 code_map（不依赖磁盘文件，实时生效）"""
        codes = {}
        for r in range(self._code_table.rowCount()):
            k = self._code_table.item(r, 0)
            v = self._code_table.item(r, 1)
            if k and v and k.text().strip():
                codes[k.text().strip().upper()] = v.text().strip()
        return codes

    def _do_scan_start(self):
        folder = self._scan_path.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "提示", "请输入或选择有效的文件夹路径"); return
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接"); return
        op = self._cfg['user_name']
        code_map = self._get_code_map()
        self._scan_log.clear()
        self._scan_bar.setValue(0)
        self._scan_stats.setText("正在加载数据库已有素材列表…")
        known = db.get_all_phashes()
        self._scan_stats.setText(f"数据库已有 {len(known)} 个素材，开始扫描…")
        self._scan_worker = ScanWorker(folder, op, known, code_map)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.log_line.connect(self._scan_log.append)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.start()
        self._btn_scan_start.setEnabled(False)
        self._btn_scan_stop.setEnabled(True)

    def _do_scan_stop(self):
        if hasattr(self, '_scan_worker') and self._scan_worker.isRunning():
            self._scan_worker.stop()
            self._btn_scan_stop.setEnabled(False)
            self._scan_stats.setText("正在停止，等待当前文件处理完毕…")

    def _on_scan_progress(self, total, done, added, skipped, failed):
        pct = int(done / total * 100) if total else 0
        self._scan_bar.setValue(pct)
        self._scan_stats.setText(
            f"进度: {done} / {total}  |  "
            f"✅ 新增 {added}  |  ⏭ 跳过 {skipped}  |  ❌ 失败 {failed}"
        )

    def _on_scan_done(self, result):
        self._btn_scan_start.setEnabled(True)
        self._btn_scan_stop.setEnabled(False)
        if not result.get('stopped'):
            self._scan_bar.setValue(100)
        status = "⏸ 已手动停止" if result.get('stopped') else "✅ 扫描完成"
        msg = (f"{status}  |  总计 {result['total']} 个文件  |  "
               f"✅ 新增 {result['added']}  |  "
               f"⏭ 跳过 {result['skipped']}  |  "
               f"❌ 失败 {result['failed']}")
        if result.get('canva_id'):
            msg += f"  |  🎨 Canva【{result['canva_id']}】已登记"
        self._scan_stats.setText(msg)
        self._scan_log.append(f"\n{'─' * 60}\n{msg}")
        self._log(msg)
        self._refresh_lib()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MamApp(); win.show()
    sys.exit(app.exec())
