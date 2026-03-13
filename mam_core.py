# mam_core.py — 核心算法 & 文件工具
import os
import json
import numpy as np
import cv2
import imagehash
from PIL import Image

IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
VID_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
ALL_EXTS  = IMG_EXTS + VID_EXTS

CONFIG_FILE       = "mam_config.json"
DB_CONFIG_FILE    = "mam_db_config.json"
PRODUCER_CODE_FILE = "mam_producer_codes.json"


# ── 人员代码表 ────────────────────────────────────────────────
def load_producer_codes() -> dict:
    """载入人员代码表，格式: {"KS": "张三", "57": "李四"}"""
    if os.path.exists(PRODUCER_CODE_FILE):
        try:
            with open(PRODUCER_CODE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_producer_codes(codes: dict):
    with open(PRODUCER_CODE_FILE, 'w', encoding='utf-8') as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)


def parse_producer_from_filename(filename: str, code_map: dict) -> str:
    """
    从文件名解析制作人。
    格式规则： YYYYMMDD-CODE-描述
      - 第一段必须是 8 位数字（日期）
      - 第二段必须是纯字母或纯数字，长度 1~6 位
    识别到 CODE 后查 code_map 映射到真实姓名；查不到映射则直接返回 CODE。
    识别不到 CODE 返回 '未知'.
    """
    import re
    name = os.path.splitext(os.path.basename(filename))[0]
    # 去掉尾部空格+(N)
    name = re.sub(r'\s*\(\d+\)\s*$', '', name).strip()
    parts = name.split('-')
    if len(parts) < 2:
        return '未知'
    if not re.match(r'^\d{8}$', parts[0].strip()):
        return '未知'
    code = parts[1].strip()
    if not re.match(r'^[A-Za-z0-9]{1,6}$', code):
        return '未知'
    # CODE 查对照表（大小写不敏感）
    for k, v in code_map.items():
        if k.upper() == code.upper():
            return v
    return code  # 有 CODE 但对照表中没有，直接用 CODE


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"user_name": "操作员", "user_id": "001"}


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── pHash（使用 imagehash 标准库）─────────────────────
def _cv2_to_pil(img) -> Image.Image | None:
    """OpenCV BGR ndarray → PIL Image（RGB）"""
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def get_phash(img) -> str | None:
    """
    用 imagehash.phash 计算感知哈希，返回 16 位小写 hex 字符串。
    img 可以是 OpenCV ndarray 或 PIL Image。
    """
    try:
        if img is None:
            return None
        if not isinstance(img, Image.Image):
            img = _cv2_to_pil(img)
            if img is None:
                return None
        h = imagehash.phash(img, hash_size=8)   # 8×8 = 64 bit → 16 hex chars
        return str(h)   # imagehash 已保证 16 位小写 hex
    except Exception as e:
        return None


def get_phash_pil(pil_img: Image.Image) -> str | None:
    """直接接受 PIL Image，避免二次转换"""
    try:
        if pil_img is None:
            return None
        h = imagehash.phash(pil_img, hash_size=8)
        return str(h)
    except:
        return None


def hamming(h1: str, h2: str) -> int:
    """两个 16 位 hex phash 字符串的汉明距离"""
    try:
        a = imagehash.hex_to_hash(h1)
        b = imagehash.hex_to_hash(h2)
        return a - b   # imagehash 重载了减法运算符 = 汉明距离
    except:
        return 64


def phash_sim(h1: str, h2: str) -> str:
    return f"{int((1 - hamming(h1, h2) / 64) * 100)}%"


# ── 文件读取 ───────────────────────────────────────────
def cv2_read(filepath):
    """支持中文路径的 OpenCV 读取"""
    try:
        arr = np.fromfile(os.path.abspath(filepath), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.dtype != np.uint8:
            img = (img / 256).astype(np.uint8)
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img
    except:
        return None


def get_thumbnail(filepath):
    """返回缩略图 ndarray（图片直接读，视频取 0.5s 帧）"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMG_EXTS:
        return cv2_read(filepath)
    if ext in VID_EXTS:
        cap = cv2.VideoCapture(filepath)
        cap.set(cv2.CAP_PROP_POS_MSEC, 500)
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None
    return None


def get_file_size(filepath):
    try:
        return os.path.getsize(filepath)
    except:
        return 0


def get_asset_type(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMG_EXTS: return "image"
    if ext in VID_EXTS: return "video"
    return "unknown"


def make_thumb_bytes(img):
    if img is None:
        return None
    _, buf = cv2.imencode('.jpg', cv2.resize(img, (100, 100)))
    return buf.tobytes()
