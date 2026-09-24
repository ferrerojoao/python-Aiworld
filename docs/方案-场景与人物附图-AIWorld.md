# 方案：场景附图 + 人物附图

> **状态：✅ 已落地（阶段 A 全做完）。** 2026-09-19 定方案 · 2026-09-23 补核源码 · 2026-09-24 补齐像素规格 + 改判"做服务端归一化"，**同日实施完成并过闸门**。实际落点、三处与本文初稿不同的改判、以及落地时新发现的两个坑，全在 **§11 落地记录**。
> **来源**：`MEMORY-DETAIL.md` §16 / §17A（原始论证），本文是**可直接开工的落地版**。
> ⚠️ **正文里的行号为 2026-09-23/24 复核时的值（HEAD = `4e2a44c`）；§3–§10 保留为"开工时的原始判据"，不要照抄行号，改动前按「锚点字符串」定位。**
> **姊妹文档**：`docs/方案-地图-AIWorld.md` · `docs/方案-位置面板-AIWorld.md`。

---

## 0. 一页纸摘要

**做什么**：给**场景**和**人物**各加一张附图（上传 or AI 生成），显示在**顶栏「场景」点开的场景卡**里——一张卡回答「此刻这一幕：**哪里** + **谁在**」。

**为什么成本低**：二进制走**一个新上传口**，元数据只存**相对路径串**搭上**既有资产写路径**；`assets/` 目录**自动随导出包走**（已核源码），`content/` 本就 gitignored ⇒ **大二进制天然不进版本库**。**不新增第二条改世界的路。**

**规格已定**（§4.5）：场景 **1280×720 / 16:9** · 人物 **512×512 / 1:1** —— 推导过程在 §4.5，不是拍脑袋。
**服务端会归一化**（§4.4）：**Pillow 12.3.0 已装**（2026-09-24，已写进 `pyproject.toml`）⇒ 上传时按规范尺寸**居中裁切 + 缩放**后落盘，`assets/` 里的尺寸是统一的。

**当前只做一半**：**AI 生图后端用户主动挂起** —— 先只做**上传 + 字段 + 界面**，`prompt / seed / model` **参数位预留但留空**。

**三条不可动摇的底座**（§2）：图片**永不进提示词** · **缺图 = 不画图** · **图永远不是真相层**。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **A** | 字段 + 上传口（含 Pillow 归一化）+ 取图口 + 界面 + 显示 | ✅ **2026-09-24 已落地**（实际落点见 §11） |
| **B** | AI 生图 | ⏸️ 用户主动挂起（后端两条路都不选） |

---

## 1. 目标与范围

**做**：场景图 · 人物图（含主角）· 上传口（含归一化）· 取图口 · 工作台附图槽 · 顶栏场景卡显示。

**不做**（§10 有完整防复活清单）：正文里自动识别说话人配头像 · 左栏缩略图 · 聊天区背景图 · 引擎生成占位形象 · **转正表单的肖像槽**。

---

## 2. 三条底座（不可动摇，先读这条）

1. 🔴 **图片永不进提示词。** 模型读的永远是 `appearance`（人物）/ `perceivable`（场景）**文本**；图**纯 UI**。→ 省 token、零注入风险、不动工作单。
2. 🔴 **缺图 = 不画图。** 引擎**不生成占位形象**（"引擎不代笔"的视觉版）。空字符串就是"没有图"，界面**就是不画图那一块**。
   - ⚠️ **"缺图"只吞掉图，不吞信息。** 场景卡「在场」段**每人占一格**：有图 = 头像 + 名字；无图 = **只有名字的文字格**（同格尺寸）。**不画占位剪影。**
   - ⚠️ **这条不是在救"没图就答不出谁在"** —— 那种情形不存在：左栏「在场」本来就是纯文字名单，而且**现在所有人都是无图状态**，那张行本来就等于一行名字。真正的理由是两条：① **一格一人** ⇒ 加了图之后，人数**不会因为谁有图谁没图而变化**，避免"有的显示头像、有的凭空消失"这种半截状态；② **移动端左栏是 `display: none`**（`style.css:1695`），场景卡是手机上**唯一**能看到"谁在"的地方。
3. 🔴 **图永远不是真相层。** 图**不产生任何事件 / 状态 / 目标**，不进注入、不参与升格与转正判定。它是**示意**，不是设定。

---

## 3. 字段与落盘（精确）

### 3.1 模型字段（现在**没有**，需要新增）

复核现状（`app/world/models.py`）：

- `Scene`（`:42-48`）当前字段 = `id` · `aliases` · `perceivable` · `region` —— **无 `image`**
- `NpcCard`（`:51-66`）当前字段 = `id` · `appearance` · `persona` · `private_note` · `personal_secrets` · `has_actor` · `is_player` · `region` · `attributes` —— **无 `portrait`**

新增（缺省空串 = 缺图，老档向后兼容）：

```python
# Scene
image: str = ""       # 相对路径，如 "assets/scenes/鱼市-3f9a1c72.png"；空 = 无图

# NpcCard（主角同字段，不另开）
portrait: str = ""    # 相对路径，如 "assets/npcs/朱明-8b21de04.png"；空 = 无图
```

⚠️ **存相对路径串，不存 URL、不存 base64** —— 路径串能跟着 `scenes.json` / `npcs/*.json` 的**全量重写**一起落盘，于是**复用既有资产写路径，不需要第二条改世界的路**。

### 3.2 存放位置（磁盘）

`world_dir` **与** `save_dir` **是同一目录的两个名字**（`app/runtime/session.py:19-26`），实际布局以 `content/qingshi2/` 为例：

```
content/<世界名>/
  world.json          ← 资产
  lorebook.json       ← 资产
  scenes.json         ← 资产（全量重写）
  npcs/               ← 资产（每张卡一个 json，全量重写）
  assets/             ← 🆕 新增，与上面并列
    scenes/<场景名>-<8位内容哈希>.<ext>
    npcs/<人物名>-<8位内容哈希>.<ext>
  save.json           ← 运行态
  events.jsonl        ← 运行态
  candidates/         ← 运行态（提案，不入备份）
```

**文件名规格**：`<安全化主体名>-<8位内容哈希>.<ext>`

- ⚠️ **哈希是对「归一化后的字节」算的**（§4.4 先处理、后命名），不是对上传的原始字节 —— 否则"同一张图用不同压缩率传两次"会攒出两份内容相同的文件。
- **为什么带内容哈希**（2026-09-24 改）：如果叫 `assets/scenes/鱼市.png`，**同一路径换图后浏览器会继续用旧图**（URL 没变，HTTP 缓存命中），这是最容易被当成"上传没生效"的坑。带哈希 ⇒ **URL 随内容变，天然破缓存**，且"覆盖删旧"退化成"写新文件 + 删旧文件"这种最简单的事。
- 保留主体名是为了**人可读**（用户自己翻 `content/` 时认得出），不是给逻辑用的。
- **安全化**：主体名先去掉 `/\:*?"<>|` 与首尾点/空格、截到 40 字符，为空则退化成 `scene` / `npc`。
- ⚠️ **别信客户端给的主体名**：上传与取图都要先把它**在服务端查一遍白名单** —— 场景见 `world.scenes`（∪ 候选册 `save.locations`），人物见 `world.npcs` ∪ `save.unfiled`。查不到 = 400。这一步同时挡住了目录穿越。

**写路径**：JSON 侧走 `save_world_assets(root, data)`（`app/world/loader.py:128`，内部 `write_json_atomic`，`app/core/store.py:19`）—— **JSON 只多一个字符串字段，函数签名不用改**；二进制由上传口单独写（先写临时文件再 `os.replace`，与 `write_json_atomic` 同款原子语义）。

---

## 4. 两个新端点（上传口 + 取图口）

### 4.1 🔴 之前只写了上传口，**取图口是漏的**

字段里存的是**相对路径串**（`assets/scenes/鱼市-3f9a1c72.png`），这不是 URL，浏览器 `<img src>` 用不了。**必须有配套的读端点**：

**`GET /api/sessions/{sid}/assets/{kind}/{name}`** → `FileResponse`（带 `ETag` / `Cache-Control: private, max-age=31536000, immutable`）

- 🔴 **必须放在 `/api` 前缀下**：`app.mount("/", StaticFiles(...))`（`app/main.py:94`）在 `include_router` **之后**注册 ⇒ 只能保护 `/api/*`（`app/api/security.py:32` `PROTECTED_PREFIX`）。放 `/api` 之外 = **图片在局域网里裸奔**（文件名还含人名/场景名）。
- ⚠️ **`kind` 只有两个值**（`scenes` / `npcs`），`name` 必须再走一次"安全文件名"正则（`^[^/\\]{1,80}\.(png|jpg|jpeg|webp)$`）并 `resolve()` 后校验仍在 `assets/<kind>/` 内 —— 双保险，因为这条路直接读磁盘。
- `immutable` 能这么用，正是因为 §3.2 的内容寻址命名：**同一 URL 永远对应同一张图**。

### 4.2 🔴 `<img src>` 带不了 Authorization 头 —— 这是本方案最大的暗坑

项目的鉴权口径（`app/api/security.py:11-15`）明确写过：**静态面必须裸奔，因为浏览器取 `<script src>` 时带不了 Authorization 头**。图片遇到的是**同一个问题，但结论相反**：

- 前端令牌存在 `localStorage["aiworld_token"]`（`app.js:35`），由 `authHeaders()`（`app.js:48-50`）注进 fetch 的 `Authorization`。
- **`<img src="/api/...">` 不会走 `authHeaders()`** ⇒ 手机 / 局域网访问时**必然 401** ⇒ 而底座 2 是"缺图 = 不画图" ⇒ **图会静默消失**，是最难查的一种坏法（本机测试永远正常，只有换设备才复现）。

**解法（已定）：不走 `<img src>` 直连，走"带令牌的 fetch → blob"**：

```js
// 前端统一入口，替换掉任何 <img src="assets/...">
const _imgCache = new Map();            // path -> objectURL
async function imageUrl(path) {
  if (!path) return "";
  if (_imgCache.has(path)) return _imgCache.get(path);
  const res = await fetch(`/api/sessions/${state.sid}/${path}`, { headers: authHeaders() });
  if (!res.ok) return "";               // 401/404 一律当"缺图" → 不渲染那块
  const url = URL.createObjectURL(await res.blob());
  _imgCache.set(path, url);
  return url;
}
```

- 复用既有 `authHeaders()`；**401 自然退化成"缺图"**，不用额外分支。
- `objectURL` 有生命周期 ⇒ **世界切换 / `/reset` 时清空 `_imgCache` 并 `URL.revokeObjectURL`**（否则内存里攒着上一个世界的图）。同一世界内不回收（图数量很小，且换页要复用）。
- **明确否决的两种替代**：① 令牌塞查询串（`?token=`）——会进浏览器历史 / 访问日志，且 `app.js:59-66` 特意把 token 从 URL 里删掉，说明这条路当初就不想走；② 把图挪到 `/api` 之外裸奔——见 §4.1。

### 4.3 上传口规格

**`POST /api/sessions/{sid}/assets`**

**🔴 收"原始字节"，不用 multipart**（`kind` / `subject` 走 query 参数，`Content-Type` 只当声明）：

- **理由**：`python-multipart` **没装**（`pyproject.toml` 无此依赖，`.venv` 里也确认没有）。用 `UploadFile` 会直接报 `RuntimeError: Form data requires "python-multipart" to be installed.` ⇒ 要么加依赖，要么绕开。**本方案选绕开**：客户端两端都是我们自己写的，`fetch(url, { method: "POST", body: file })` 一句话的事，**零新依赖**（保持项目"依赖极简"的现状）。
- 服务端 `body = await request.body()`，配 `Content-Length` 预检。

**四道校验（一道都不能省）**：

| 校验 | 做法 | 为什么 |
|---|---|---|
| **文件名由服务端生成** | 客户端**只报**「类型 + 主体名」（`?kind=scenes&subject=鱼市`） | **防目录穿越**——绝不接受客户端给的路径 |
| **扩展名白名单** | 只收 `.png` / `.jpg` / `.jpeg` / `.webp` | 防塞任意文件 |
| 🔴 **真解码一次** | ① 先看 magic bytes（PNG `89 50 4E 47`；JPEG `FF D8 FF`；WebP `RIFF....WEBP`）做**廉价预筛**；② 再用 **Pillow `Image.open(BytesIO) + img.verify()`** 兜底，`.format` 与声明不符就拒 | 扩展名与 MIME 都能随便改。**装了 Pillow 之后 `verify()` 是真解码，比 magic bytes 强得多** —— 前者只能看头，后者能识破"头对、身子烂"的文件 |
| **大小上限** | 见 §4.5；先看 `Content-Length`，超了直接 413，再按累计字节二次确认 | 防误传原图把世界目录撑爆；也防解码炸弹拖垮内存 |

**其他**：

- **`kind` 只有两个值**（`scenes` / `npcs`）—— 别做成通用文件柜。
- **覆盖 = 写新文件 + 删旧文件**（内容寻址命名下旧名与新名必然不同；若哈希相同则内容一样，直接返回旧路径、不重写）。
- **移除**：清空字段 + 删文件，两个动作要一起（只清字段会留孤儿）。
- **鉴权**：项目已有请求级鉴权，新端点只要在 `/api` 下就自动被覆盖 —— 但落地时**按既有测试确认它确实被覆盖**（不要假设）。
- **响应**：回相对路径串，前端存进字段并渲染。

### 4.4 📐 服务端归一化（**Pillow 12.3.0 已装，本节由"不做"改判为"做"**）

**2026-09-24 用户要求装 Pillow**，于是之前"服务端无能力"的三条限制全部打开。**已定的用法**：

```python
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = 64_000_000     # 64MP；超了抛 DecompressionBombError → 捕获后 400

img = Image.open(io.BytesIO(raw))
img.verify()                            # ① 可信类型判定（这之后 img 不可再用，要重新 open）
img = Image.open(io.BytesIO(raw))       # ② 重新打开
img = ImageOps.exif_transpose(img)      # ③ 手机照片的旋转信息，不处理会躺着

# ④ 只缩不放；居中裁切到目标比例（ImageOps.fit 就是 CSS object-fit: cover 的语义）
if min(img.width / W, img.height / H) > 1:      # 原图在 cover 意义下比目标大
    img = ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.5))

img.save(dest, format=img.format, optimize=True, quality=88)   # JPEG 才认 quality
```

**四条口径**：

1. **只缩不放**（`min(sw/W, sh/H) > 1` 才动）—— 不放大、不制造假细节。小图原样保留（前端 `object-fit: cover` 照旧兜得住），所以 `assets/` 里尺寸**是"≤ 规范尺寸"而不是"恰好等于"**。
2. **比例不归一，尺寸归一**：不裁成 16:9 硬性统一（那会砍掉用户想留的内容）—— **只在缩小时顺便裁到目标比例**（`ImageOps.fit`）。用户传 4:3 的大图 → 落盘就是 1280×720；传 4:3 的小图 → 原样 640×480，前端裁。
3. **不改格式**（PNG→PNG / JPEG→JPEG / WebP→WebP）。静默转格式会让用户困惑（"我传的 png 怎么变 webp 了"）。转换（省 2/3 体积）做成**可选开关、默认关**。
4. **`exif_transpose` 必须有** —— 否则手机直出的照片会躺倒，而这种错在电脑上传的图上永远看不到。

⚠️ **落盘体积**：归一化 + `optimize=True` 之后一般 < 1 MB。所以体积策略变成 **"只卡输入，不管输出"**（§4.5）。

### 4.5 📐 图片规格（像素 / 比例 / 体积）

**推导口径**：显示尺寸（CSS px）× 2（DPR，笔记本与手机普遍 ≥2）＝ 需要的物理像素下限；推荐值再留约 2 倍余量，保证将来放大显示（点开看大图）也不糊。

| 用途 | **规范尺寸（上限）** | 宽高比 | **拒绝线（下限）** | CSS 显示尺寸 | DPR2 实际需要 |
|---|---|---|---|---|---|
| **场景图** | **1280×720** | 16:9 | **640×360** | **336×189**（场景卡内容宽） | 672×378 |
| **人物图** | **512×512** | 1:1 | **256×256** | **64×64**（头像行）/ 96×96（工作台槽） | 128×128 |

**为什么是这两个数**：

- **场景 1280×720**：2× 下需要 672×378，1280 有 1.9 倍余量；且 **16:9 是绝大多数生图模型的默认档**，二期接 AI 生图时不用改规格。
- **人物 512×512**：头像行显示 64px ⇒ 2× 需 128px，512 有 4 倍余量；256 是"再小就会糊"的红线。**1:1 是肖像的自然比例**，也和生图模型的人像档一致。
- **下限是硬拒绝线**（低于它就 400，提示"图片太小"）—— 不做插值放大糊图，早点告诉用户比默默糊掉好。

**体积**：

| 环节 | 数值 | 说明 |
|---|---|---|
| **请求体硬闸** | **8 MB** | `Content-Length` 预检 + 流式累计双确认；超了 413 |
| 解码像素上限 | 64 MP | `Image.MAX_IMAGE_PIXELS`，防解压炸弹 |
| 落盘后 | 不单独限制 | 归一化 + `optimize` 后一般 < 1 MB |

- 前端只做**软提示**（"建议 ≥ 640×360 / ≥ 256×256"），不再自己算体积 —— 服务端反正会压，拦在客户端的意义变小了。

### 4.6 显示位尺寸（界面）

| 显示位 | 位置 | 尺寸 | 说明 |
|---|---|---|---|
| **场景大图** | 顶栏「场景」点开的**场景卡** | 宽 100%（**608px**）× `aspect-ratio: 16/9` | 有图才渲染这一块；圆角 10px |
| **在场一行** | 同一张场景卡的「在场」段 | **88×88** 圆形头像 + 名字；**每行 5 个**（`grid-template-columns: repeat(auto-fill, minmax(104px, 1fr))`，`gap: 8px`），超出换行 | **无图 = 同尺寸的文字格**（只有名字），**不画剪影**（§2 底座 2） |
| **工作台附图槽·场景** | 「场景」页每张卡内 | 预览 **240×135** | 上传 / 换图 / 移除 |
| **工作台附图槽·人物** | 「人物」页每张卡内 | 预览 **120×120** | 同上 |

⚠️ **2026-09-24 按用户反馈放大过一轮**（原 336px 大图 / 64×64 头像 / 每行 4 个）：用户在电脑屏上看原尺寸**看不清**。改动与推导记在 §11.5，移动端在 `@media (max-width: 720px)` 里回退到原值。

**场景卡浮层规格**（新建，`#scene-card`）：

- **宽 640px**（`width: min(640px, calc(100vw - 24px))` ⇒ 窄屏不溢出），内边距 16px ⇒ **内容宽 608px**。
  - ✅ **这个宽度仍落在已定的上传规格里**：608 CSS px × 2(DPR) = **1216 ≤ 1280** ⇒ **不需要改 §4.5 的 1280×720**。想再往上加得先动那个规格（这是"显示尺寸倒推像素"那条口径在替我们兜底）。
- 结构：① 大图（有图才渲染，**点开可看大图**，见 §11.5 第 4 条）→ ② 场景名 → ③「在场」一格一人。
  - 🔴 **不放 `perceivable`**（2026-09-24 用户要求撤掉）：那是"能看见什么"的散文，属**正文的活儿**，抄到卡片上只会把"此刻在哪、谁在"淹掉。`scene_perceivable` 后端照旧发（工作台仍可看可改）⇒ **"字段在"不等于"卡片要读它"**。见 §11.5 第 3 条。
  - 🔴 **不放「最近去过」**（2026-09-24 用户要求撤掉）："去过哪儿"是**左栏 `#scene-nav`** 的活儿，这张卡只回答"此刻"两件事（**哪里 + 谁在**），多一栏就冲淡了它。`recent_scenes` 后端照旧发（左栏在用）⇒ **"字段在"不等于"卡片要读它"**。
- 入口：把现在纯文本的 `<span id="scene">`（`index.html:35`）改成按钮式触发器，点开 / `Esc` 关闭 / 点外部关闭。⚠️ 现有 CSS 有 `#topbar #scene { color: var(--muted) }`，改按钮时**保留这组配色**，别让它变成显眼的实心按钮。
- **主角排在场第一格 + 挂「主角」标**（与左栏「在场」保持一致）。⚠️ 这是 **UI 层**的一致，和记忆里「场景底稿正文**剔主角**」（提示词层）**不是一回事，别合并**。
- 排序**照抄左栏顺序**（主角 → 在场 NPC），零额外规则。

---

## 5. 入口与显示（已拍板，不要重议）

| 环节 | 决定 |
|---|---|
| **上传入口** | **世界工作台**「**场景**」/「**人物**」页各一个附图槽（上传 / 预览 / 移除）——**不放导演窗口**（沿用"人物卡 / 场景归工作台"分界） |
| 🔴 **人物图入口只有工作台** | **转正表单不设肖像槽**（2026-09-24 用户拍板：「转正时候不必留传头像的口子，让玩家去世界工作台搞」）。理由顺带解决了一个真问题：转正时那人**还没有卡**，上传的图会成孤儿；卡存在之后再加图，就没有这个状态 |
| **显示位置** | **顶栏「场景」点开 → 场景卡**：大图（**可点开看大图**）+ 场景名 + 在场一格一人（**不含 `perceivable` /「最近去过」**——见 §11.5） |
| **人物图显示** | **并入场景卡**——在场一格一人 ⇒ 一张卡回答"此刻这一幕：哪里 + 谁在" |
| **明确不采纳** | 左栏缩略图 · 聊天区背景图 · 转正表单图槽 |

**前端落点（复核所得）**：

- 顶栏场景：`index.html:35`（`<span id="scene">`）· `app.js:306`（填充）· `index.html:15` + `app.js:328`（`#scene-nav`，已有的"最近去过"）
- 工作台页：`app.js:1924`（`ASSET_TABS = ["overview","lore","scenes","npcs"]`）· `index.html:209-210`（`#world-tab-scenes` / `#world-tab-npcs`）· 渲染函数 `app.js:2238`（`renderEditScenes`，字段锚点 `data-field="perceivable"` 在 `:2276`）· `app.js:2283`（`renderEditNpcs`）
- 已在场人物渲染：`renderLeftRail`（`app.js:330+`）里的 `#present-npcs` —— 一格一人可**照抄它的数据来源**，但**不要改左栏本身**（左栏不放缩略图）
- ⚠️ 改 `web/dist/` 后**必须跑 `scripts/bump_frontend_version.py`**

---

## 6. 导出 / 导入 / 版本库（**已验证零改动**）

两个导出端点都走 `rglob("*")`，`assets/` 会**自动随包**：

| 端点 | 位置 | 排除 | `assets/` 随包？ |
|---|---|---|---|
| `GET /sessions/{sid}/world/export`（资产包） | `routes_sessions.py:837` | `candidates/` + `save.json` / `events.jsonl` / `presets.json` | ✅ |
| `GET /sessions/{sid}/export`（全量备份） | `routes_sessions.py:865` | 只排除 `candidates/` | ✅ |

- 于是「复制为新世界」「全量备份」**一并带上图片**，**不需要改导出代码**。
- `.gitignore` 有 **`/content/`**（整目录不进版本库）⇒ **大二进制天然不进版本库**，不需要新增忽略规则。
- ⚠️ **落地时要复验一次**：新增 `assets/` 后跑一遍导出，确认 zip 里真有图片（别只信这份文档的分析）。

---

## 7. AI 生图（**用户主动挂起**）

**用户原话**：「生图后端暂不定，以后可以改进的时候再搞」。

- **两条路都不选**：OpenAI 兼容 `POST /v1/images/generations` vs 本地 **ComfyUI API**（本机有 2080 Ti 环境）。
- **但要预留参数位**：人物卡 / 场景上留 `prompt`（角色一致性锚点）/ `seed` / `model`，**留空**。理由见 §9 的"角色一致性"——那几个字段的**存在**决定了二期能不能接上，**留空不花成本**。
- 若上生图，**出图尺寸直接按 §4.5 的规范尺寸**（1280×720 / 512×512），这样一期二期的图能混在同一个 `assets/` 里，界面无需区分，而且**归一化那条流水线可以原样复用**。

### 7.1 若将来上生图，**六条边界必须一起上**（缺一条就破"引擎不代笔"）

图比文字更"实"——**编出来的设定伪装成事实**这个风险在图上更大（同 `SCENE_PLACEHOLDER` 那条注释的道理）。所以：

1. **prompt 只准引用已有设定文字**（`perceivable` / `appearance` / 玩家输入）当草稿；**引擎不自由发挥编新设定**。
2. 生成物必须**玩家显式采纳**（同"目标设立 = 导演提案 + 玩家确认"那道闸）。
3. **图不产生任何事件 / 状态 / 目标**，不进注入、不参与升格与转正判定——**图永远不是真相层**。
4. 生成走**「候选区」同款心智**：生成 → 预览 → 采纳 / 重掷 / 放弃。
5. 长耗时需**异步 + 进度**（本地 ComfyUI 数十秒）。
6. 生图**不吃 LLM token 统计**，别混进 `/settings/usage` 的 LLM 表（会误导）。

---

## 8. 落地清单

> ✅ **已按本清单落地（2026-09-24）**：实际落点、三处改判、落地时新发现的坑、14 刀变异表全在 **§11**。
> 下面的勾选状态**保留开工时的原样**（没逐条回填）——要核对"到底做了什么"看 §11，别看勾选框。

### 8.1 后端

- [ ] ✅ **已完成**：`pyproject.toml` 加 `pillow>=10.0`（venv 里实装 12.3.0）
- [ ] `app/world/models.py` —— `Scene.image: str = ""` · `NpcCard.portrait: str = ""`
- [ ] 新增图片处理模块（建议 `app/world/images.py`）—— §4.4 的 `verify` / `exif_transpose` / `ImageOps.fit` / `save`；`MAX_IMAGE_PIXELS` 在这一处设定，不要散落
- [ ] `app/api/routes_sessions.py` —— 新增 `POST /sessions/{sid}/assets`（原始字节 + §4.3 四道校验 + §4.4 归一化 + §3.2 内容寻址命名 + 覆盖删旧 + 移除删文件）
- [ ] `app/api/routes_sessions.py` —— 新增 `GET /sessions/{sid}/assets/{kind}/{name}`（§4.1：白名单正则 + `resolve()` 兜底 + `immutable` 缓存头）
- [ ] `app/api/routes_sessions.py` —— `NpcCardBody`（`:349`）与场景 payload 增字段，否则**工作台保存会把图抹掉**（全量重写 ⇒ 前端不回传 = 置空）
- [ ] `app/world/loader.py` —— 确认 `save_world_assets` 读得到新字段（`scenes` / `npcs` 两个 payload 分支）
- [ ] 确认 `check_assets`（`app/world/draft.py:184`）不把新字段当未知字段报问题
- [ ] ⚠️ `check_assets` 走 `tempfile.mkdtemp()` 试写 —— **临时世界里没有 `assets/` 目录**，所以字段校验**不能**顺手去 `stat()` 那个文件，否则体检会误报（同"改名警告不能放 `check_world`"那条教训）

### 8.2 前端（`web/dist/`，改完跑 `bump_frontend_version.py`）

- [ ] `renderEditScenes`（`app.js:2238`）+ `renderEditNpcs`（`:2283`）—— 各加附图槽 + 回传字段（唯一上传入口）
- [ ] **场景卡浮层** `#scene-card`（新建，规格见 §4.6）—— 大图 + 在场一格
- [ ] `imageUrl()`（§4.2）—— 带令牌 fetch → blob，并在世界切换 / `/reset` 时清缓存
- [ ] `style.css` —— 场景卡 / 槽位 / 在场一格 / 空态（**缺图不画占位**）
- [ ] ❌ **不做**：转正表单图槽

### 8.3 测试

- [ ] 后端：上传端点（合法 / 扩展名伪装 / **头对身子烂（magic bytes 过而 `verify()` 挂）** / MIME 与内容不符 / 超大 / **解压炸弹** / 穿越路径 / 主体名不在白名单）
- [ ] 后端：归一化（大图缩到规范尺寸 / **小图不被放大** / **EXIF 旋转被摆正** / 格式不变）
- [ ] 后端：取图端点的路径校验（`..`、绝对路径、非白名单扩展名）· 覆盖删旧 · 移除删文件 · 字段随 payload 往返不被抹
- [ ] 后端：**导出 zip 里含 `assets/`**（§6 的复验，钉成断言）
- [ ] 🔴 后端：**非本机访问（`client=("10.0.0.5", ...)`）取图无令牌 → 401**（这条是 §4.2 那个坑的守卫，本地测试看不到它）
- [ ] 前端：`web/tests/smoke.test.mjs` 加"有图渲染图 / 无图不渲染图 / **无图仍列出名字**"
- [ ] **守卫断言一律用变异测试验"有没有牙齿"**（skill `mutation-check-assertion`）——例如把 `verify()`、把取图的路径校验、把"只缩不放"的条件拔掉，测试必须变红

---

## 9. 未定项（落地时或二期需拍板）

1. 🔴 **角色一致性**（生图的真难点）：建议先在人物卡加 **prompt 锚点字段**，一致性靠 prompt；reference image / IP-Adapter 留**二期**。
2. **是否存 sidecar**（`assets/scenes/<id>.json` 记 `prompt` / `model` / `seed`）：便于复现，也能区分"**画的还是传的**"。建议**先留字段位、不一定立刻写文件**。
3. **是否默认转 WebP**：装了 Pillow 之后一条参数的事（省 2/3 体积），但会**静默改格式**。建议默认关，在附图槽上给个显式选项；若嫌麻烦就永远不转。
4. ✅ **已定**：大小上限 = §4.5；`.webp` **收**（列进白名单，且推荐）；**转正表单不设图槽**（§5）。

---

## 10. 明确不做（防复活 · 别再提）

1. ❌ **正文里自动识别说话人配头像**：正文是**自由文本、没有说话人标注**，靠正则猜必然越补越多（同"点名派活"那次的教训）。**要做先得给正文加结构化标注——那是另一个量级的改动。**
2. ❌ **左栏缩略图 / 聊天区背景图**（已明确不采纳）。
3. ❌ **引擎生成占位形象**（破"引擎不代笔"）。
4. ❌ **图片进提示词**（哪怕"帮助模型理解场景"也不行 —— 模型读 `perceivable` / `appearance` 文本）。
5. ❌ **把图当真相层**：不许拿图推导位置 / 在场 / 状态 / 关系。
6. ❌ **上传口做成通用文件柜**（只服务 `scenes` / `npcs` 两个主体）。
7. ❌ **转正表单的肖像槽**（2026-09-24 用户拍板；入口只留世界工作台）。
8. ❌ **把图放到 `/api` 之外以绕开令牌**（图片是世界数据；文件名还含人名/场景名）。
9. ❌ **令牌塞进图片 URL 的查询串**（进历史与访问日志；且前端本来就特意把 `?token=` 用完即删）。
10. ❌ **取消 §4.4 的"只缩不放"改为强制放大**（插值放大只会糊，还骗人以为图很清晰）。

---

## 11. 落地记录（2026-09-24）

### 11.1 实际落点

| 层 | 文件 | 内容 |
|---|---|---|
| 管道 | `app/world/images.py`（新增） | `normalize` 校验+归一化 · `write_asset` 落盘 · `prune_orphan_assets` 孤儿清理 · `resolve_asset` 取图解析 · `safe_subject` / `digest8` |
| 字段 | `app/world/models.py` | `Scene.image: str = ""` · `NpcCard.portrait: str = ""`（都存**相对路径串**，随资产全量重写走**既有**写路径） |
| 端点 | `app/api/routes_sessions.py` | `POST /sessions/{sid}/assets`（收**原始字节**，`Content-Length` 预检 413）· `GET /sessions/{sid}/assets/{kind}/{name}`（`FileResponse` + `immutable`）· `PUT /world` 末尾调 `prune_orphan_assets` |
| 状态 | 同上 `/state` | 新增 `scenes_by_id` · `scene_image` · `scene_perceivable`（**原文**，不拼"在场：…"）· `present_view`（主角第一 + `is_player`，**没图也给空串 `portrait`**） |
| 前端 | `web/dist/{app.js,index.html,style.css}` | `imageUrl()` 带令牌 fetch → `URL.createObjectURL` · `hydrateImages()` · 顶栏场景卡（大图 + 一格一人）· 工作台附图槽 `.img-slot` · `readScenes/readNpcs` **回传字段** |
| 测试 | `tests/test_assets.py`（新增 **29** 条）· `web/tests/smoke.test.mjs`（+5 → **24**） | |

闸门：`ruff` 绿 · `pytest` **347** · 覆盖率 **91.77%**（`images.py` 单独 **100%**）· 前端 `node --test` **24 pass** · `bump_frontend_version.py` 对得上（无需改）。

### 11.2 与本文初稿不同的三处（改判）

1. **转正表单不设肖像槽**（用户拍板）。落卡表单收的是"能从他自己的正文里推导出来的字段"，图不在正文里 ⇒ 不该出现在那儿；要配图去世界工作台。顺带消掉了"上传时人还没有卡 ⇒ 孤儿图"这个未定项。
2. **上传口不做主体名白名单**（§4.3 初稿想做）。工作台里"新加的人物 / 场景"在**保存前**还不存在于资产里，白名单会把这一路直接堵死。穿越风险由「**文件名完全由服务端生成**」（`safe_subject` + 内容哈希）消掉 —— 客户端给不出路径。
3. **孤儿清理放在 `PUT /world` 那一刻**，不是上传时。上传口**只写文件、不改资产**（"保存才落盘"是工作台既有的事务边界，不为图片破它）⇒ "换图 / 移除 / 传了但放弃改动"三路统一在保存那一刻带走。⚠️ 它**不能**做成 `save_world_assets` 的默认行为：半份资产会误删（docstring 里写明了）。

### 11.3 落地时才发现的坑（都已修 + 有守卫）

1. 🔴 **`ImageOps.exif_transpose` 在没有旋转信息时也返回一份 copy**（Pillow 源码里那个 `else: return image.copy()`）。所以原计划的 `changed = upright is not img` **恒为 True** ⇒「不需要动就原样返回原始字节」那条路**永远走不到**，所有小图都被无谓重编码一遍（JPEG 还白掉一次画质）。**尺寸对了不等于没动过** —— 这个是靠覆盖率发现"`return raw` 那行从没被执行"揪出来的。判据改成看**朝向标签本身**：`changed = orientation not in (None, 1)`。
2. **`prune_orphan_assets` 里原本还有一支 `path.name.endswith(".tmp") or …`**：变异测试证明**删掉它全套测试仍全绿** —— 因为"未被任何字段引用"已经涵盖了 `.tmp` 残骸。那是**死重量**，留着只会让人以为 `.tmp` 需要特殊处理。已删，代码里留了删除记录。

### 11.4 变异检验（14 刀全部有牙）

| 刀 | 变异体 | 红的用例 |
|---|---|---|
| M1 | 拔掉 Pillow `verify()+load()` | `test_png_header_with_broken_body_is_rejected` |
| M2 | 拔掉 `resolve_asset` 的 `path.parent != folder` | `test_resolve_asset_second_layer_blocks_escape_on_its_own` |
| M3 | `scale > 1` → `scale > 0`（只缩不放 → 也放） | `test_small_enough_image_is_kept_as_is` |
| M4 | 朝向判据恒 `False` | `test_exif_orientation_is_applied` |
| M4b | 朝向判据改成 `is not None`（正常朝向也重编码） | `test_exif_orientation_1_is_not_a_reason_to_reencode` |
| M5 | 去掉 `if not assets.is_dir(): return` 早退 | `test_pruning_a_world_without_any_images_is_a_no_op` |
| M6 | 孤儿清理不再删未被引用的文件 | 同上 |
| M7 | 去掉尺寸下限拒绝线 | `test_too_small_is_rejected_with_the_floor_in_the_message` |
| M8 | 去掉 `FORMAT_TO_EXT` 白名单出口 | `test_format_outside_the_whitelist_is_rejected` |
| M10 | 去掉路由侧 `Content-Length` 预检 | `test_oversize_body_is_rejected_by_the_route_guard` |
| M11 | 保存世界时不清理孤儿图 | `test_saving_world_prunes_unreferenced_assets` |
| M12 | 取图去掉 `immutable` 缓存头 | `test_fetch_returns_bytes_with_immutable_cache` |
| M14 | 前端 `readNpcs` 不回传 `portrait` | 工作台附图槽：路径必须随全量回传 |
| M15 | 前端 `imageUrl` 不带 `Authorization` | 取图必带 Authorization 头 |

⚠️ **M1 / M2 的第一版用例是没牙的**，记下来免得以后再犯：
- M1 原来只喂「PNG 头 + 垃圾」，那是被 `Image.open` 自己挡的（**走不到**真解码那一层）⇒ 必须补「**真 PNG 砍掉尾巴**」那个输入。
- M2 原来用的越界名字（`../x`、`/etc/passwd`）全被**第一层正则**先挡掉，`path.parent != folder` 在真实输入下**摸不到**（现实里只有软链接/目录联接能碰到它）⇒ 改成"放宽正则、单独验第二层"。

### 11.5 上线后按用户反馈调的四项（2026-09-24 同日）

用户看过实际界面后的四条反馈，**都只改前端，后端零改动**：

1. **撤掉场景卡的「最近去过」**。"去过哪儿"左栏 `#scene-nav` 已经在讲；这张卡的职责只有"此刻"两件事——**哪里 + 谁在**，多一栏就冲淡它。
   - ⚠️ `recent_scenes` **后端照旧发**（左栏 `renderLeftRail` 还在用）⇒ **"字段在"不等于"卡片要读它"**，别看到字段就加回来。
   - 顺手删掉随之变成死样式的 `.scene-card-recent` / `.scene-chip`（这两个原本就只服务那一栏）。
   - **守卫**：冒烟用例里**故意**保留 `recent_scenes`，断言 `.scene-chip` 数为 0、且正文里不含「最近去过」。**变异检验**：把那一栏塞回去 ⇒ `not ok 20`（失败在「chip 不该再出现」那条），源码已还原。
2. **卡片放大**（用户："在电脑屏幕上看这太小了，看不清楚"）：宽 **360 → 640px** · 内边距 12 → 16 · 大图 **336×189 → 608×342** · 头像 **64 → 88px** · 在场格 `minmax(72px) → minmax(104px)` · 名字 11 → 12px · `max-height` 70 → 78vh。
   - ✅ **没有破上传规格**：608 CSS px × 2(DPR) = **1216 ≤ 1280** ⇒ §4.5 那条 **1280×720 不用动**（这正是"从显示尺寸倒推像素"那条口径的价值）。
   - 手机在 `@media (max-width: 720px)` 里回退到原值。⚠️ 那个断点块位于 `.scene-card*` 基础规则**之前**，所以**回退规则必须另起一个断点块、放在本节之后**——同特异性下后者胜，塞进原来那个块会**被基础规则盖掉**。
   - ⚠️ 尺寸是纯 CSS，**jsdom 断言不了**（它不排版）⇒ 这一处**靠人工看，没有守卫**，别误以为测试覆盖了它。
3. **撤掉场景图下面那行可感知描述**（`scene_perceivable`）。同 1. 的道理：那是"能看见什么"的散文，属于**正文的活儿**，卡片上再抄一遍只会把"此刻在哪、谁在"淹掉。
   - ⚠️ `scene_perceivable` **后端照旧发**（工作台仍可看可改）⇒ 同样别看到字段就加回来；`.scene-card-desc` 样式一并删净，留了注释说明。
   - **守卫**：同一个冒烟用例里夹具**故意**塞着 `scene_perceivable`，断言 `hasDesc === false` **且** `cardText` 里不含描述里的字（`/鱼干/`）——只断"没有那个 class"挡不住"换个容器照样渲染"。
4. **点图看大图**（用户："点击图片可以看大图"）：场景卡里的**任何** `<img[data-path]>`（场景大图 + 在场头像）都可点开浮层。
   - **`bindLightbox()`**：点击委托挂在 `#scene-card` 上；`e.target.closest("img[data-path]")` 命中后**直接把被点那张的 `src` 交给浮层**——那是「带令牌 fetch → blob」的产物，**复用它最省事**，也避免"再查一次缓存"出现"卡片有图、大图打不开"的不同步。取不到 `src`（= 缺图，`hydrateImages` 已把图藏了）就什么都不做，**不弹空浮层**。
   - **关闭只清浮层那张 `<img>` 的 `src`，绝不 `revokeObjectURL`**：那个 blob 归 `_imgCache` 所有（换世界时由 `clearImageCache` 统一放掉），在这里 revoke 会把卡片上那张图一起弄坏。⚠️ 有一条断言钉住这点（关掉大图后卡片那张的 `src` 必须原样还在）。
   - **Esc 让位**：`bindSceneCard` 与 `bindLightbox` 两个 `keydown` 都挂在 `document` 上，靠注册顺序分先后太脆 ⇒ 在 `bindSceneCard` 里显式判 `!lightboxOpen()`，大图开着时 Esc 只关大图，第二次才轮到卡片。
   - **两个 `stopPropagation` 只有一个是真的**（变异检验分开验过）：**浮层**那个必须有（浮层在卡片**外面**，不挡就会触发「点空白处关卡片」）；**卡片里图片点击**那个是**死重量**（唯一的 document 级 click 监听开头就有 `card.contains(e.target)` 早退），已删。
   - 🔴 **`.lightbox { display: flex }` 必须配 `.lightbox[hidden] { display: none }`**：类选择器的 `display` 会盖掉 UA 的 `[hidden] { display: none }`，少了那条浮层就**一直挂在屏幕上**（`.scene-card[hidden]` 早就踩过同一个坑）。
     - ⚠️ **jsdom 验不了这个**：它的 `getComputedStyle` 对带 `hidden` 属性的元素**硬编码**成 `none` —— 实测**连一条覆盖规则都没有**、只留 `.lightbox{display:flex}` 也算出 `none`。写成断言就是**恒真的假守卫**（变异：删掉覆盖规则，绿灯照旧）。所以这条退一步**只查源码里那对规则还在不在**；"没删除这对规则"能守，"渲染对不对"守不了，靠人工看。

---

## 附录：本次复核核对到的现状（供落地时对齐，别凭记忆）

| 项 | 结论 | 位置 |
|---|---|---|
| `Scene` 有无 `image` | ❌ 没有 | `app/world/models.py:42` |
| `NpcCard` 有无 `portrait` | ❌ 没有 | `app/world/models.py:51` |
| 资产写路径 | `save_world_assets(root, data)`，data keys = overview/lorebook/scenes/npcs；强制"恰好一张主角卡" | `app/world/loader.py:128` |
| 原子写 | `write_json_atomic` | `app/core/store.py:19` |
| 世界目录 = 存档目录 | **同一个目录**，两个属性名 | `app/runtime/session.py:19-26` |
| 导出是否自动带 `assets/` | ✅ 两个端点都 `rglob`，会自动带 | `routes_sessions.py:837` / `:865` |
| `content/` 是否 gitignored | ✅ `/content/` | `.gitignore` |
| 资产体检 | `check_assets`（**在 `tempfile.mkdtemp()` 里试写，只有 JSON、没有 `assets/`**） | `app/world/draft.py:184` |
| 工作台资产页 | `ASSET_TABS` 四项 + `#world-tab-*` 面板 | `app.js:1924` · `index.html:209-210` |
| ✅ **Pillow** | **已装 12.3.0**（2026-09-24），已写进 `pyproject.toml` | ⇒ §4.4 服务端归一化 |
| 🔴 `python-multipart` | **未装**（`pyproject.toml` 无、`.venv` 无） | ⇒ §4.3 收原始字节，零新依赖 |
| 🔴 静态挂载顺序 | `app.mount("/", StaticFiles(web/dist))` 在 `include_router` **之后** | ⇒ 新端点放 `/api` 下既安全又受令牌保护（`app/main.py:94`） |
| 🔴 令牌怎么传 | `localStorage["aiworld_token"]` → `Authorization: Bearer`（`?token=` 只用来灌一次，随即从 URL 删掉） | `app.js:32-66` |
| 🔴 鉴权只认 Authorization 头 | `bearer_token()` 只解 `Authorization: Bearer` | `app/api/security.py:43-50` |
| 抽屉宽 | `360px`（`.drawer`），`.tab-panel` 内边距 14/16 ⇒ 内容宽 **328px** | `style.css:295` · `:345` |
| 工作台弹窗宽 | `1080px`（`.modal-content`，`max-width:100%`），`.world-tab` 内边距 14/16 ⇒ 内容宽约 **1048px** | `style.css:628` · `:862` |
| 在场一格样式 | **无既有样式**，需新建（左栏 `#present-npcs` 只有文字行） | `style.css:107` · `:1548-1570` |
| 移动端断点 | `max-width:720px`：**左栏隐藏**、抽屉与弹窗 100% 宽 | `style.css:1690-1706` |
