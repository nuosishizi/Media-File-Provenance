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
        self.setMinimumHeight(148)
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
        for fp in self._files[:30]:
            box = QWidget(); bv = QVBoxLayout(box); bv.setContentsMargins(2,2,2,2)
            lbl = QLabel(); lbl.setFixedSize(68, 68); lbl.setScaledContents(True)
            lbl.setStyleSheet("border:1px solid #ccc;background:#000;")
            th = get_thumbnail(fp)
            if th is not None:
                rgb = cv2.cvtColor(th, cv2.COLOR_BGR2RGB); h, w, ch = rgb.shape
                qi  = QImage(rgb.data, w, h, ch*w, QImage.Format.Format_RGB888)
                lbl.setPixmap(QPixmap.fromImage(qi))
            else:
                lbl.setText("?")
            nm = QLabel(os.path.basename(fp)[:9]); nm.setStyleSheet("font-size:9px;")
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

    # ── Tab5：溯源查询 ──────────────────────────────────
    def _tab_query(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("拖入任意文件（含从社交平台下载的），查看完整素材家谱 / 作者 / 层级关系"))
        sp = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); lv = QVBoxLayout(left)
        self._drop_query = DropArea("拖入待查询文件"); lv.addWidget(self._drop_query)
        btn = QPushButton("🔍  查询溯源（文件）")
        btn.setStyleSheet("background:#8e44ad;color:#fff;height:40px;font-size:14px;")
        btn.clicked.connect(self._do_query); lv.addWidget(btn)
        sep = QLabel("── 或按 Canva 模板ID 查询 ──")
        sep.setStyleSheet("color:#888;font-size:11px;margin-top:6px;")
        lv.addWidget(sep)
        canva_row = QHBoxLayout()
        self._canva_id_search = QLineEdit()
        self._canva_id_search.setPlaceholderText("输入Canva模板ID，例：20260313214500000")
        btn_cv = QPushButton("🎨 查询模板")
        btn_cv.setStyleSheet("background:#c0392b;color:#fff;")
        btn_cv.clicked.connect(self._do_query_canva)
        canva_row.addWidget(self._canva_id_search); canva_row.addWidget(btn_cv)
        lv.addLayout(canva_row)
        sp.addWidget(left)
        right = QWidget(); rv = QVBoxLayout(right)
        rv.addWidget(QLabel("📋  溯源家谱（展开查看完整层级）："))
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["素材 / 关系", "制作人", "时间", "类型", "phash前16位"])
        self._tree.setColumnWidth(0, 300); self._tree.setColumnWidth(1, 100)
        self._tree.setColumnWidth(2, 150); self._tree.setColumnWidth(3, 70)
        self._tree.setAlternatingRowColors(True); rv.addWidget(self._tree)
        sp.addWidget(right); sp.setSizes([360, 760]); v.addWidget(sp)
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
        fp = self._drop_query.file()
        if not fp: QMessageBox.warning(self, "提示", "请先拖入要查询的文件"); return
        def task():
            img = get_thumbnail(fp)
            ph, src = get_phash_from_file(fp, img)
            if not ph: gui_log("❌ 无法计算phash"); return None
            return db.get_lineage(ph)
        def done(lineage):
            self._tree.clear()
            if not lineage:
                self._log("❓ 该文件未登记，无溯源信息")
                self._tree.addTopLevelItem(QTreeWidgetItem(["❓ 未登记"])); return
            ast = lineage['asset']
            root = QTreeWidgetItem([
                f"🎯 {ast.get('filename','?')}",
                ast.get('producer', '?'),
                str(ast.get('created_at', ''))[:16],
                ast.get('asset_type', '?'),
                (ast.get('phash', '') or '')[:16] + "…"
            ])
            ff = QFont(); ff.setBold(True); root.setFont(0, ff)
            root.setForeground(0, QColor("#2c3e50"))
            if lineage['derived_from']:
                sec = QTreeWidgetItem(["⬆ 衍生来源（多级）"])
                sec.setForeground(0, QColor("#e67e22"))
                for row in lineage['derived_from']:
                    sec.addChild(self._make_ancestor_item(row))
                root.addChild(sec); sec.setExpanded(True)
            if lineage['derived_to']:
                sec = QTreeWidgetItem(["⬇ 衍生出（多级）"])
                sec.setForeground(0, QColor("#27ae60"))
                for row in lineage['derived_to']:
                    sec.addChild(self._make_descendant_item(row))
                root.addChild(sec); sec.setExpanded(True)
            if lineage['composed_from']:
                sec = QTreeWidgetItem(["📦 组件来源"])
                sec.setForeground(0, QColor("#8e44ad"))
                for row in lineage['composed_from']:
                    sec.addChild(self._make_component_item(row))
                root.addChild(sec); sec.setExpanded(True)
            if lineage['used_in']:
                sec = QTreeWidgetItem(["🎬 被用于成品"])
                sec.setForeground(0, QColor("#2980b9"))
                for row in lineage['used_in']:
                    child = QTreeWidgetItem([
                        f"  📄 {row.get('filename','?')}",
                        row.get('producer', '?'),
                        str(row.get('created_at', ''))[:16],
                        row.get('asset_type', '?'),
                        (row.get('product_phash', '') or '')[:16] + "…"
                    ])
                    sec.addChild(child)
                root.addChild(sec); sec.setExpanded(True)
            if lineage.get('canva_used'):
                sec = QTreeWidgetItem([f"🎨 Canva模板 ({len(lineage['canva_used'])}个)"])
                sec.setForeground(0, QColor("#c0392b"))
                for tmpl in lineage['canva_used']:
                    child = QTreeWidgetItem([
                        f"  🎨 {tmpl.get('template_name','?')}",
                        tmpl.get('creator', '?'),
                        str(tmpl.get('created_at', ''))[:16],
                        "canva模板",
                        tmpl.get('template_id', '')
                    ])
                    sec.addChild(child)
                root.addChild(sec); sec.setExpanded(True)
            self._tree.addTopLevelItem(root); root.setExpanded(True)
            self._log(f"✅ 溯源: {ast.get('filename','?')}")
        self._bg(task, done, msg="溯源查询")

    def _do_query_canva(self):
        tid = self._canva_id_search.text().strip()
        if not tid:
            QMessageBox.warning(self, "提示", "请输入Canva模板ID"); return
        def task():
            return db.get_lineage_by_canva_id(tid)
        def done(result):
            self._tree.clear()
            if not result:
                self._log(f"❓ Canva模板 [{tid}] 未找到"); return
            tmpl = result['template']
            root = QTreeWidgetItem([
                f"🎨 {tmpl.get('template_name','?')}",
                tmpl.get('creator', '?'),
                str(tmpl.get('created_at', ''))[:16],
                "Canva模板",
                tmpl.get('template_id', '')
            ])
            ff = QFont(); ff.setBold(True); root.setFont(0, ff)
            root.setForeground(0, QColor("#8e44ad"))
            for asset in result['assets']:
                a_item = QTreeWidgetItem([
                    f"  🖼 {asset.get('filename','?')}",
                    asset.get('producer', '?'),
                    str(asset.get('created_at', ''))[:16],
                    asset.get('asset_type', '?'),
                    (asset.get('phash', '') or '')[:16] + "…"
                ])
                a_item.setForeground(0, QColor("#2c3e50"))
                for anc in asset.get('ancestors', []):
                    a_item.addChild(self._make_ancestor_item(anc))
                root.addChild(a_item)
            self._tree.addTopLevelItem(root); root.setExpanded(True)
            for i in range(root.childCount()): root.child(i).setExpanded(True)
            self._log(f"✅ 模板溯源: {tmpl.get('template_name','?')}  素材{len(result['assets'])}个")
        self._bg(task, done, msg="Canva模板溯源")

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
