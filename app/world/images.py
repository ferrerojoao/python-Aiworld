"""场景图 / 人物图的落盘管道（2026-09-24）。

方案与规格：``docs/方案-场景与人物附图-AIWorld.md``（像素口径在 §4.5，四条归一化口径在 §4.4）。

这一模块只做三件事，别混：

1. **校验**（能不能收）：magic bytes 廉价预筛 → Pillow 真解码兜底 → 尺寸拒绝线。
2. **归一化**（收成什么样）：摆正 EXIF 旋转；只在"原图比目标大"时缩到目标比例
   （``ImageOps.fit`` = 居中裁切，正是 CSS ``object-fit: cover`` 的语义）。
   **只缩不放**——不做插值放大制造假细节；**不改格式**——静默转格式会让用户困惑。
3. **命名**（叫什么）：``<安全化主体名>-<8位内容哈希>.<ext>``，内容寻址。

🔴 **图片永不进提示词**（方案 §2 底座 1）：本模块只产出**字节与路径**，不碰世界资产、
不碰账本、不进工作单。字段（``Scene.image`` / ``NpcCard.portrait``）由调用方写。

⚠️ 这里的 8 位哈希与 ``scripts/bump_frontend_version.py`` 的**前端资源哈希不是一回事**
（那边有一份"必须先 CRLF→LF 归一"的纪律，别交叉复用）。
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

#: 目标尺寸（上限），按 kind 分。依据 = 显示尺寸 × 2(DPR) 再留约 2 倍余量（方案 §4.5）。
SIZES: dict[str, tuple[int, int]] = {"scenes": (1280, 720), "npcs": (512, 512)}

#: 未被任何 kind 认领时的占位名（主体名安全化后为空才用到）。
FALLBACK_SUBJECT = {"scenes": "scene", "npcs": "npc"}

#: 拒绝线 = 目标尺寸的一半，按 cover 口径 ``min(sw/W, sh/H)`` 比较。
#: 低于它说明图太小（放大只会糊），早点报错比默默糊掉好。
MIN_SCALE = 0.5

#: 请求体硬闸。归一化之后落盘一般 < 1 MB，所以这里只卡输入、不卡输出。
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

#: Pillow 的解压炸弹护栏：8 MB 的 JPEG 解出来可能是上亿像素。64 MP 足够容纳任何
#: 合理的图，同时对炸弹更严（超过会抛 DecompressionBombError，被下方 except 收成 400）。
Image.MAX_IMAGE_PIXELS = 64_000_000

#: Pillow 报出的 format → 落盘扩展名。**只认这三种**（与方案 §4.5 白名单一致）。
FORMAT_TO_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}

#: 扩展名 → 取图端点的 Content-Type（不依赖 mimetypes 的平台差异）。
EXT_TO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: 取图端点直接读磁盘，这是那条路的名字白名单。服务端生成的名字必然命中。
ASSET_NAME_RE = re.compile(r"^[^/\\]{1,80}\.(png|jpg|jpeg|webp)$")

#: 廉价预筛：不解码就能否掉绝大多数垃圾；真判据是下面的 Pillow 解码。
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"))

#: EXIF 朝向标签编号（TIFF/EXIF 写死的 274，别写成会随版本飘的东西）。
#: `1` = 正常（等价于没有标签），其余值（2…8）都意味着要摆正。
_EXIF_ORIENTATION = 274


class ImageRejected(Exception):
    """上传被拒。``status_code`` 直接给 HTTP 层用；``message`` 直接给用户看。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def sniff_format(raw: bytes) -> str:
    """只看文件头。返回 ``""`` 表示不是白名单里的三种。"""
    for magic, fmt in _MAGIC:
        if raw.startswith(magic):
            return fmt
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "WEBP"
    return ""


def safe_subject(name: str, kind: str) -> str:
    """主体名 → 可安全做文件名的短名。

    ⚠️ **主体名完全由客户端给**（query 参数），所以这里必须自己清一遍：去掉路径
    分隔符与控制字符、去掉首尾的点与空格（``..`` 这类）、截到 40 字符。
    光靠这一步还不够——真正的防线是**文件名由服务端拼**，客户端给不出路径。
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip())
    cleaned = cleaned.strip(". ")
    fallback = FALLBACK_SUBJECT.get(kind, "asset")
    return cleaned[:40] or fallback


def digest8(raw: bytes) -> str:
    """内容哈希前 8 位（16 进制）。内容寻址命名就靠它。"""
    return hashlib.sha256(raw).hexdigest()[:8]


def normalize(raw: bytes, kind: str) -> tuple[bytes, str, str]:
    """校验 + 归一化。

    返回 ``(要落盘的字节, 扩展名, 8位哈希)``。被拒时抛 :class:`ImageRejected`。

    ⚠️ **不需要动就原样返回原始字节**（不重编码 = 不损失质量、不白烧 CPU）。
    """
    if kind not in SIZES:
        raise ImageRejected(f"未知图片类型：{kind}")
    if not raw:
        raise ImageRejected("空文件")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageRejected(
            f"图片太大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）", status_code=413
        )
    if not sniff_format(raw):
        raise ImageRejected("不是 PNG / JPEG / WebP 图片")

    # ① verify() 查结构，② load() 真解一遍像素。两条都要：
    #    前者便宜、后者才能识破"头对、身子烂"（截断的文件）。
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # Pillow 的异常家族很杂（UnidentifiedImageError / OSError / ValueError…）
        raise ImageRejected(f"图片已损坏或无法解码：{exc}") from exc

    fmt = (img.format or sniff_format(raw)).upper()
    if fmt not in FORMAT_TO_EXT:
        raise ImageRejected(f"不支持的图片格式：{fmt}")

    target_w, target_h = SIZES[kind]
    # 手机照片带 EXIF 旋转信息：不摆正就是躺倒的，而这个 bug 在电脑上传的图上
    # 永远看不到。
    #
    # 🔴 **判"改没改"只能看朝向标签本身**，不能看 `exif_transpose` 的返回值：
    # 它在**没有旋转信息时也会返回一份 copy**（`in_place=False` 的语义，见 Pillow
    # 源码里那个 `else: return image.copy()`），于是 `upright is not img` 恒为 True
    # —— 底下"不需要动就原样返回原始字节"那条路永远走不到，所有小图都被无谓地
    # 重编码一遍（JPEG 还白掉一次画质）。这个是靠覆盖率发现"第 156 行没被执行"才
    # 揪出来的：**尺寸对了不等于没动过**。
    orientation = img.getexif().get(_EXIF_ORIENTATION)
    img = ImageOps.exif_transpose(img)
    changed = orientation not in (None, 1)

    scale = min(img.width / target_w, img.height / target_h)
    if scale < MIN_SCALE:
        raise ImageRejected(
            f"图片太小：至少 {int(target_w * MIN_SCALE)}×{int(target_h * MIN_SCALE)}"
            f"（当前 {img.width}×{img.height}）"
        )
    if scale > 1:
        # 只缩不放；顺手裁到目标比例（= CSS cover）。
        img = ImageOps.fit(
            img,
            (target_w, target_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        changed = True

    if not changed:
        return raw, FORMAT_TO_EXT[fmt], digest8(raw)

    buf = io.BytesIO()
    params: dict = {"optimize": True}
    if fmt == "JPEG":
        params["quality"] = 88
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")  # JPEG 没有 alpha；P/RGBA 直接存会抛
    elif fmt == "WEBP":
        # WebP 默认是**有损**的：给一个不刺眼的质量，别把用户的无损图悄悄压坏。
        params["quality"] = 90
        params["method"] = 4
    img.save(buf, format=fmt, **params)
    out = buf.getvalue()
    return out, FORMAT_TO_EXT[fmt], digest8(out)


def write_asset(root: str | Path, kind: str, subject: str, raw: bytes) -> str:
    """校验 + 归一化 + 落盘，返回**相对路径串**（就是写进字段的那份）。

    同名即同内容（内容寻址），所以已存在时不重写——顺带让"同一张图传两次"只留一份。
    """
    data, ext, digest = normalize(raw, kind)
    folder = Path(root) / "assets" / kind
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_subject(subject, kind)}-{digest}{ext}"
    dest = folder / filename
    if not dest.exists():
        # 先写临时文件再 replace：与 write_json_atomic 同款原子语义，
        # 免得半张图落盘却被字段引用。
        tmp = folder / f"{filename}.tmp"
        tmp.write_bytes(data)
        tmp.replace(dest)
    return f"assets/{kind}/{filename}"


def prune_orphan_assets(root: str | Path, payload: dict) -> list[str]:
    """删掉 ``assets/`` 里没人引用的图，返回被删的相对路径清单。

    **为什么需要它**：上传口**只写文件、不改资产**——字段由工作台「保存」一起落盘
    （"保存才落盘"是工作台既有的事务边界，不该为图片破它）。于是"换图 / 移除 /
    传了但放弃改动"都会留下没人引用的文件。清理放在**保存那一刻**统一做，比在每个
    端点各删一次省事，也顺手兜住了"传了没保存"那一路。

    ⚠️ **只在 ``references`` 完整时调用**（``PUT /sessions/{sid}/world`` 的 payload
    就是完整的资产形状）。传半份资产进来会误删——所以**不做成
    ``save_world_assets`` 的默认行为**。

    ⚠️ 落卡等路径也写资产、也不删图，但它们永远不会新增孤儿，不必调用。
    """
    root = Path(root)
    referenced: set[str] = set()
    for scene in payload.get("scenes") or []:
        image = (scene or {}).get("image") or ""
        if image:
            referenced.add(Path(image).name)
    for card in (payload.get("npcs") or {}).values():
        portrait = (card or {}).get("portrait") or ""
        if portrait:
            referenced.add(Path(portrait).name)

    removed: list[str] = []
    assets = root / "assets"
    if not assets.is_dir():
        return removed
    for folder in sorted(p for p in assets.iterdir() if p.is_dir()):
        for path in sorted(p for p in folder.iterdir() if p.is_file()):
            # ⚠️ 这里**不需要**为 `.tmp` 单开一支：写到一半的残骸必然没人引用，
            #    被下面这条一并带走。曾写过一个 `endswith(".tmp") or ...`，
            #    变异测试证明删掉它全套测试仍全绿 —— 即那支是死重量，
            #    留着只会让人以为 `.tmp` 有特殊处理。**别再加回来。**
            if path.name not in referenced:
                path.unlink()
                removed.append(f"assets/{folder.name}/{path.name}")
    return removed


def resolve_asset(root: str | Path, kind: str, name: str) -> Path | None:
    """把「取图请求」的 kind + name 解析成磁盘路径；不合法或不存在返回 ``None``。

    取图端点直接读磁盘，所以这里是**双保险**：正则先挡形状，``resolve()`` 再挡越界
    （软链接 / 编码花招都算在这一步上）。
    """
    if kind not in SIZES or not ASSET_NAME_RE.match(name):
        return None
    folder = (Path(root) / "assets" / kind).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        return None
    return path
