"""Work order assembly (TDD §6): objective ledger snapshot + subjective contract.

Objective blocks are pure data (facts queried from the ledger/content pack);
subjective contracts are instructions (identity, discipline, output format).

Assembly order（2026-09-11 三级分层）：写作纪律**分级**并连成一块梯队，排在身份之后；
资料区紧随其后、完整不被切开；事件日志收尾紧贴玩家输入：

    一级 输出格式 —— 格式出错 = 界面直接失败、玩家无法继续；全表唯一的硬边界
    二级 情节合理性 —— 信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏
    三级 文风与剧情倾向 —— 预设；默认遵循，可按情境灵活

    实际顺序：A 身份与任务 → 二级 → 三级 → 一级 →
    资料区（世界概要 / 世界书命中 / 角色资料 / 幕后注 / 场景快照 /
    已知集 / 剧情目标——不标规则等级，与事件日志同性质）→
    E 事件日志（最近原文在后） → 玩家输入消息

    段落抬头只写等级名、不带解释句（同日用户判定自我说明是废话）；
    一级置于梯队末尾而非资料区之后，是为了让资料区连成整块（同日用户调整）。

分级的目的：让模型分清「错了会坏数据」与「这样写更好」。此前两者混在同一个
「绝对不可违背」的箱子里，模型只能一律照办——越写越保守，什么都不敢写。

同日另补（深抉择流程修复）：
- 二级「抉择归属」条末尾下发**本轮可上缴名单**（在场 ∩ 配 Actor），编剧不必再从
  各人名后的括号自行推断谁不能上缴；
- 一级收下 ``actor_questions`` 的**条目结构**（此前错放在二级的括号里），并给带值样例。
"""

from __future__ import annotations

import datetime as dt

from app.config import DEFAULT_LIMITS, InjectionLimits
from app.ledger.queries import Ledger
from app.rules.lorebook import subject_hit_ids
from app.world.models import NarrativePreset, WorldContent

# ---------------------------------------------------------------------------
# 客观层 · 账本快照（纯函数，只陈述事实，不含指令）
# ---------------------------------------------------------------------------

def world_summary_block(world: WorldContent) -> list[str]:
    """世界概要：与事件日志同性质的**资料**，不标规则等级（2026-09-11）。

    世界设定天然公开、人人可引（REQ 〇章）。抬头不再写「硬规则 / 不可违背」——
    规则等级只留给真正需要分级的三级写作纪律；资料一律平等地摆在资料区，
    模型自会取材，不需要额外的强制标签。
    """
    return [
        "世界概要：",
        *world.meta.summary,
    ]


# ---------------------------------------------------------------------------
# 角色状态 · 长期事实（REQ 〇章「角色状态」，2026-09-14）
# ---------------------------------------------------------------------------
#
# "他此刻是什么"（免疫普通武器 / 独臂 / 病倒）：引擎一行都不读，
# 唯一职能是**让 LLM 知道**（编剧 / 审计 / Actor 三处）。与 lifecycle 的分界 =
# 引擎会不会读它——所以两类不合并（合并等于让 LLM 去猜"这条要不要影响引擎行为"）。
#
# **只写现状，不写成因**（2026-09-15 用户拍板）：一条状态 + 一个 public 布尔
# 表达不了"成因私密、表现公开"两层——写成"沐浴龙血，普通刀剑伤不了他"，
# public=true 会把成因一起交出去，public=false 又连表现一起藏起来，两头不对。
# 所以让状态只承载**表现**（"普通刀剑伤不了他"），成因留在产生它的那条事件里
# （``source_event`` 指得着）；public 只表示这条状态旁人看不看得见。
#
# **三处口径在同一天定稿**（2026-09-15）：
#   · 编剧 = 全知 + 不公开条目挂「秘」——他填 actor 的 context 时要知道避让谁；
#   · 审计 = 全知、不带标记（它不写 NPC 情境）；
#   · Actor = 自己的**全量**（public 说的是旁人，对本人没有意义）+ 在场旁人的
#     public 条目；不带 `（至 …）` / `（秘）`（那两个是给编剧排布用的）。
#
# 位置：紧贴该角色的人物卡行（主角在「玩家资料」段，NPC 在他自己那行之后）。
# 空 = 整块不出现，零成本；不带任何规则等级抬头（与事件日志同性质的资料）。

STATE_CAP_PLAYER = 6  # 主角状态上限
STATE_CAP_NPC = 3  # 每个在场 NPC 的状态上限
STATE_TEXT_MAX = 20  # 单条状态的字数上限（审计提议侧同时用它截断，单一事实源）
# 2026-09-15 由 30 收到 20（用户："状态用词得精炼些，不能太长"）：一条状态该是**一句话
# 一个事实**（"左腿瘸了" / "普通刀剑伤不了他"），不是"事实 + 解释"。30 字够塞两个分句，
# 实际会诱导出"左小腿骨裂，走路一瘸一拐，跑不动"这种堆叠；20 字只够一个分句，
# 配合"不写括号补注"的纪律把成因与补充全都挡在外面。
STATE_ADD_PER_CHAR = 1  # 同一角色单回合最多新增几条（防"给同一个人一口气编一串"）
# NPC 落卡机制（2026-09-19）：未落卡的确定人物（有键无卡）进提示词时的上限。
# 他们**有名字但无档案**，提示词要显式说清这条边界（否则编剧会顺手给他编一段
# 来历，下次交互就变成"事实"，与未来的正式人物卡打架）。人多了会挤占版面，
# 所以同样按上限截断 + 显式说明"另有 N 个未列出"。
UNFILED_CARD_CAP = 3
# 截断按**录入顺序**（重要的往上放），与 rules.lorebook 归属条目的口径一致——
# 不按时间排：重要的状态未必是最新的（"天生盲眼"比"今天擦伤膝盖"重要得多）。
# 被截断时**必须显式说明**（"另有 N 条未列出"）：静默消失会让玩家困惑
# "我明明沐浴了龙血"，也让编剧以为不存在。


def _visible_items(entities: dict, name: str, cap: int) -> tuple[list, int]:
    """取该角色要注入的状态条目（按录入顺序截断）+ 未列出的条数。

    **已失效的条目一律不算**（``expired_at`` 非空 = 到期了）：它们仍留在存档里
    供玩家查看与撤销，但编剧与本轮审计都读不到——"到期"要真的让它停止影响正文。
    """
    runtime = entities.get(name)
    items = [
        it
        for it in (runtime.states if runtime else [])
        if (it.text or "").strip() and not it.expired_at
    ]
    return items[:cap], max(0, len(items) - cap)


def _item_text(item, mark_secret: bool = False) -> str:
    """单条状态的人话（带可选到期日；``mark_secret`` 时给不公开的条目挂「秘」）。

    ``mark_secret`` 只给**编剧**用（2026-09-15 加）：编剧是全知的，而全流程里
    唯一由他决定"谁该知道什么"的地方就是 ``actor_questions[].context``——不把
    秘辛标出来，他很可能把"你亲眼看见他喝下龙血"写进某个 NPC 的情境，
    ``public=false`` 就形同虚设。审计不需要（它不写 NPC 情境），Actor 也不需要
    （对他自己的状态，"秘"是相对谁而言？）。
    """
    text = (item.text or "").strip()
    notes: list[str] = []
    if mark_secret and not item.public:
        notes.append("秘")
    if item.until:
        notes.append(f"至 {item.until[:10]}")
    return f"{text}（{'，'.join(notes)}）" if notes else text


def state_line(
    entities: dict, name: str, cap: int, label: str = "", mark_secret: bool = True
) -> str:
    """某角色的一行状态（编剧侧用的合并形态）；无状态返回空串。

    只吐 ``text``（外加 ``until`` 的到期日，以及不公开条目的「秘」标记）——
    ``source_event`` 依旧不进提示词（它是给机器看的可追溯锚点）；``id`` 只在
    审计侧露（``states_block(include_ids=True)``），因为只有审计要按 id 精确移除。

    「秘」（``public=false``，2026-09-15 加）：编剧全知，唯一会把"谁该知道什么"
    写出去的地方是他填的 ``actor_questions[].context``。不给标记，他可能把
    "你亲眼看见他喝下龙血"写进那个 NPC 的情境，``public=false`` 就白设了。

    ``label`` 只用于抬头（主角在编剧工作单里叫「主角」，与紧邻的人物卡行成对；
    其余一律用角色中文名——审计工作单通篇用中文名）。
    """
    items, hidden = _visible_items(entities, name, cap)
    if not items:
        return ""
    body = "；".join(_item_text(it, mark_secret=mark_secret) for it in items)
    line = f"[{label or name}] 状态：{body}"
    if hidden:
        line += f"（另有 {hidden} 条状态未列出）"
    return line


def states_block(
    ledger: Ledger, present_ids: list[str], include_ids: bool = False
) -> list[str]:
    """角色状态块（审计侧；编剧侧由 ``character_block`` 逐人贴身渲染）。

    名单 = 主角 + 在场者（去重）。全空则返回空列表（零成本，与 ``active_lore_block``
    同一口径）。为主角保留更高上限：``states`` 的主用户就是主角。

    ``include_ids=True``（审计）：**一条一行**并露出 ``[st_xxx]``——审计要靠它
    移除状态（不吐 id 就只剩按原文猜，"骨裂"与"左臂骨裂"必有一场误伤）。
    """
    player = ledger.world.player_name()
    names: list[str] = []
    for name in [player, *present_ids]:
        if name and name not in names:
            names.append(name)
    lines: list[str] = []
    for name in names:
        cap = STATE_CAP_PLAYER if name == player else STATE_CAP_NPC
        items, hidden = _visible_items(ledger.save.entities, name, cap)
        if not items:
            continue
        if include_ids:
            lines.extend(f"[{name}] [{it.id}] {_item_text(it)}" for it in items)
        else:
            lines.append(f"[{name}] 状态：{'；'.join(_item_text(it) for it in items)}")
        if hidden:
            lines.append(f"（{name} 另有 {hidden} 条状态未列出）")
    if not lines:
        return []
    head = (
        "角色状态（长期事实，正文必须与之自洽；方括号内为状态 id，移除时引用它）："
        if include_ids
        else "角色状态（长期事实，正文必须与之自洽）："
    )
    return [head, *lines]


def state_view(ledger: Ledger, present_ids: list[str], full: bool = False) -> list[dict]:
    """状态面板的数据（Step 2b，2026-09-14）：按角色分组的**当前全量**视图。

    给前端用的，但**刻意由引擎侧生成**——复用的就是注入提示词的那一套（同一组
    cap、同一条"已失效不算"的过滤），所以面板上"编剧能看到哪几条"与提示词
    字面同源，不会各写一份然后漂移。

    与 ``states_block`` 的三处不同（都是刻意的）：
      · **全量而非在场**：玩家查"朱明身上还有没有旧伤"时他可能不在场——所以
        不在场者也在列，只是 ``present=False``（前端灰显 + 注明"编剧本轮看不到"）。
      · ``hidden`` 单独给（**条目本身，不只是条数**）：超出 cap 的条目在面板里
        灰显 + 注明"编剧看不到"，玩家据此清理——否则玩家会误以为编剧知道全部，
        也找不到该撤哪条来腾格子。**只对会注入的人（主角 + 在场者）才有截断**：
        不在场 / 已退场者不注入，对他们截断会说出错误的原因（"超出上限" vs
        "他不在场"），所以他们的条目一律全给，由前端整组灰显 + 组头标签说明。
      · ``expired`` 另起一个桶（2026-09-14 晚加）：``until`` 已到、被标记失效的
        条目。它们**不进 visible / hidden**（编剧读不到），但仍留在面板上、仍带
        撤销入口——旧语义（到点直接删）会让玩家想撤都没有对象。

    ``full=True``（导演窗口用）：**不截断**，所有未失效条目都进 ``visible``、
    ``hidden`` 恒空。导演要撤"被上限藏起来的那条"，看不见就无从下手。

    顺序 = 主角 → 在场 → 不在场 → 已退场（后端定序，前端照渲染即可，排序规则
    只有这一处）。**没有任何状态的角色一律不返回**（2026-09-16 用户要求）——面板
    是"此刻谁是什么"的查询视图，列一堆空组只是噪音，主角与在场者也不例外；
    唯一的例外是"只剩失效条目"的人：面板是唯一的撤销入口，漏掉就永远清不掉。
    """
    player = ledger.world.player_name()
    present = {n for n in present_ids if n}
    names: list[str] = []
    for name in (
        [player]
        + [n for n in present if n != player]
        + [n for n in ledger.save.entities if n != player and n not in present]
    ):
        if name and name not in names:
            names.append(name)
    view: list[dict] = []
    for name in names:
        runtime = ledger.save.entities.get(name)
        retired = bool(runtime and runtime.lifecycle == "retired")
        is_player = name == player
        # 主角 = 镜头本人，永远在场；调用方传进来的 present_ids 通常已把他剔掉
        # （左侧栏"在场"不该列玩家自己），面板不跟着那个口径走。
        in_present = is_player or name in present
        all_items = [
            it for it in (runtime.states if runtime else []) if (it.text or "").strip()
        ]
        dead = [it for it in all_items if it.expired_at]
        live = [it for it in all_items if not it.expired_at]
        if in_present and not full:
            cap = STATE_CAP_PLAYER if is_player else STATE_CAP_NPC
            items, rest = live[:cap], live[cap:]
        else:
            # cap 是**注入上限**，只对"这一轮真的会注入"的人有意义（主角 + 在场者）。
            # 不在场 / 已退场者压根不注入，对他们截断只会造出假的"超上限"分层
            # ——面板会说"这几条超出注入上限"，而真实原因是"他不在场"。所以全给。
            items, rest = live, []
        # 一条状态都没有（含失效条目）的角色不进面板——列个空组只是噪音，**主角与
        # 在场者也不例外**（2026-09-16 用户要求："无状态的人物不要显示"）。
        # 有失效条目的必须进：否则那条永远撤不掉（面板是唯一的撤销入口）。
        if not items and not dead:
            continue
        view.append(
            {
                "name": name,
                "is_player": is_player,
                "present": in_present,
                "retired": retired,
                "visible": [it.model_dump() for it in items],
                "hidden": [it.model_dump() for it in rest],
                "expired": [it.model_dump() for it in dead],
            }
        )
    return view


def director_states_block(ledger: Ledger, present_ids: list[str]) -> list[str]:
    """导演侧的状态**操作清单**（Step 2c）：全量、不截断、逐条一行带 ``[st_xxx]``。

    为什么复用 ``state_view(full=True)`` 而不是另写一遍：面板上玩家看到的 id 与
    导演窗口里模型看到的 id 必须**逐字一致**——玩家说"撤掉朱明那条腿伤"，导演要
    填的就是面板上那个 id。两处各算一遍迟早漂移。

    为什么要全量：撤一条**被注入上限藏起来**的状态（面板上灰显的那些）时，
    只给"能注入的几条"就等于让导演无从下手。

    已失效的条目也列出来（标「已到期」）——它们还能被撤掉，撤掉才算真清干净。

    行尾的括注与编剧工单**同词**（``秘`` / ``至 …`` / ``已到期``，2026-09-16 统一）：
    导演要按玩家的原话去改字段，而玩家说的词来自面板那颗「秘」标签——三处（编剧
    工单、导演清单、状态面板）用同一个词，改一个字段就只需要一次对照。缺了「秘」
    这一项，导演看不到某条是私密的，改 ``public`` 时只能瞎猜现值。
    """
    lines: list[str] = []
    for entry in state_view(ledger, present_ids, full=True):
        marks: list[str] = []
        if entry["retired"]:
            marks.append("已退场")
        elif not entry["present"]:
            marks.append("不在场")
        head = f"[{entry['name']}]" + (f"（{'、'.join(marks)}）" if marks else "")
        rows: list[str] = []
        for it in entry["visible"]:
            notes: list[str] = []
            if not it["public"]:
                notes.append("秘")
            if it["expired_at"]:
                notes.append("已到期")
            elif it["until"]:
                notes.append(f"至 {it['until'][:10]}")
            tail = f"（{'，'.join(notes)}）" if notes else ""
            rows.append(f"  [{it['id']}] {it['text']}{tail}")
        for it in entry["expired"]:
            notes = ["已到期"] if it["public"] else ["秘", "已到期"]
            rows.append(f"  [{it['id']}] {it['text']}（{'，'.join(notes)}）")
        if rows:
            lines.append(head)
            lines.extend(rows)
    return lines


def actor_state_block(ledger: Ledger, npc_id: str, present_ids: list[str]) -> list[str]:
    """Actor 侧的两段状态：**自己的全给、旁人的只给 ``public``**（2026-09-15 接）。

    为什么自己的**不过滤 public**：``public`` 的语义是"**旁人**看不看得见"——对
    本人毫无意义。他知道自己腿上打着石膏、知道自己刀枪不入；不给他，就会出现
    "腿伤在身却健步如飞"这种自相矛盾的动作（接这一路要修的就是这个病）。

    为什么旁人的**只给 ``public=true``**：这才是这个字段唯一的正经用途——他当场
    看得出来的样子（"刘星挨了一刀没事"）。旁人不在场一律不给（物理隔离视角），
    别人身上的秘辛一律不给。逐个按 ``STATE_CAP_NPC`` 截断，且**先筛 public 再截断**
    ——否则三条秘辛会把第 4 条公开的表现挤掉，Actor 反而"看不见"一件明明看得见的事。

    为什么这里**不吐 ``（至 …）`` 与 ``（秘）``**：那两个标记是给编剧排布用的
    （定终点、防泄漏）。Actor 只要事实本身——他知道自己腿伤着，但不知道"世界账上
    三天后自愈"这种信息。

    ``present_ids`` 由调用方给（``ledger.present_at``）；本函数自己再补一次主角，
    与 ``scene_snapshot_block`` 同口径——工作单永远是主角所在场景的工作单。
    """
    player = ledger.world.player_name()
    entities = ledger.save.entities

    def words(items) -> str:
        return "；".join(
            (it.text or "").strip() for it in items if (it.text or "").strip()
        )

    lines: list[str] = []
    own, own_hidden = _visible_items(entities, npc_id, STATE_CAP_NPC)
    if own:
        tail = f"（另有 {own_hidden} 条未列出）" if own_hidden else ""
        lines.append("你此刻的状态：" + words(own) + tail)

    present = [pid for pid in present_ids if pid and pid != npc_id]
    if player != npc_id and player not in present:
        present.insert(0, player)
    others: list[str] = []
    for pid in present:
        runtime = entities.get(pid)
        if runtime is None:
            continue
        shown = [
            it
            for it in runtime.states
            if (it.text or "").strip() and not it.expired_at and it.public
        ][:STATE_CAP_NPC]
        if not shown:
            continue
        label = pid + ("（玩家）" if pid == player else "")
        others.append(f"{label}：{words(shown)}")
    if others:
        lines += [
            "在场旁人的状态（你看得出来的部分）：",
            *["  " + row for row in others],
        ]
    return lines


def character_block(
    world: WorldContent,
    ledger: Ledger,
    present_ids: list[str],
    include_ids: bool = False,
    with_states: bool = True,
) -> list[str]:
    """主角资料 + 在场 NPC 名片（含 Actor 档位标注，可选附带 id）+ 各人状态行。

    主角与 NPC 分两段（2026-09-13 主角入人物表后仍保留）：主角是对话对象，
    有独立视角，混在"在场 NPC"里读起来会像第三方。主角卡在 NPC 段被跳过。

    状态行（2026-09-14）紧贴本人卡片行：主角在「玩家资料」段后、NPC 在各自行后。

    ``with_states=False``（导演窗口用，2026-09-14 晚加）：导演另有一份**全量带 id
    的操作清单**（``director_states_block``），这里再渲染一遍会出问题——两份口径
    不同（这里套 cap、只给在场者），现场会出现"名片上 3 条、清单上 5 条"的分歧。
    所以导演侧关掉这里的状态行，状态只从那一处出现。
    """
    player = world.player()
    player_name = world.player_name()
    lines = ["玩家资料："]
    if player is not None:
        lines.append(
            f"[主角] 名字：{player.id}；外貌：{player.appearance or '未设定'}；"
            f"人格：{player.persona or '未设定'}"
        )
    else:
        lines.append(f"[主角] 名字：{player_name}；外貌：未设定；人格：未设定")
    if with_states:
        player_state = state_line(
            ledger.save.entities, player_name, STATE_CAP_PLAYER, label="主角"
        )
        if player_state:
            lines.append(player_state)
    npc_lines = []
    temp_ids: list[str] = []
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角另有独立段，不重复列
        npc = world.npcs.get(pid)
        if npc:
            ticket = "（配 Actor）" if npc.has_actor else "（导演代笔）"
            npc_lines.append(
                f"[{npc.id}]{ticket} 外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
            if with_states:
                npc_state = state_line(ledger.save.entities, npc.id, STATE_CAP_NPC)
                if npc_state:
                    npc_lines.append(npc_state)
        else:
            # 有键无卡：未落卡的确定人物（NPC 落卡机制，2026-09-19）。引擎认他是
            # "人"（有名字 + 与玩家有往来），但世界资产里还没有他的档案。
            temp_ids.append(pid)
    if npc_lines:
        lines.append("在场 NPC：")
        lines.extend(npc_lines)
    if temp_ids:
        shown = temp_ids[:UNFILED_CARD_CAP]
        rest = len(temp_ids) - len(shown)
        tail = f"（另有 {rest} 个未列出）" if rest else ""
        lines.append("在场临时角色：" + "、".join(shown) + tail)
        lines.append(
            "  临时角色 = **有名字但引擎没有档案**的人：只按正文**已经写出**的事实演"
            "（名字、身份、说过的话），不要给他补前史、来历、人际、动机或隐藏设定"
            "——那些一旦写进正文就会被当成既成事实。"
        )
    return lines


def event_log_block(
    world: WorldContent,
    ledger: Ledger,
    *,
    limit: int,
    recent_full: int,
    summary_len: int = 60,
    include_ids: bool = False,
) -> list[str]:
    """Event log for the writer: older ones as summaries, the newest as
    full prose (so the writer can "continue" straight after them).

    **摘要行不带玩家原话**（2026-09-18）：`玩家：「…」` 已整条移除——本轮的玩家
    输入本就作为最后一条 user 消息单独下发，历史各轮的原话再糊进日志只会重复并
    挤占版面。事件本身照旧保存 ``player_input``（史实不变，见 events.py）。

    ``limit`` = 取最近几条，**0 = 全部**（2026-09-16 改：旧口径是"0 = 不给"，
    与玩家的直觉相反）。``recent_full`` = 其中最近几条给正文原文（>= 1）。
    两者**不留默认值**——数字只在系统设置里有权威版本，函数默认值再写一遍，
    改一处忘一处就是 bug；调用方一律从 ``Settings.limits()`` 取。

    每行标注发生地、在场者与时间——写手据此执行「角色不是你」知情总纲
    （公开事件 ≠ 人人皆知、异地不知、即兴角色只知眼前）。
    """

    def _id_tag(ev: dict) -> str:
        return f"[{ev['id']}] " if include_ids and ev.get("id") else ""

    def _where(ev: dict) -> str:
        loc = ev.get("location") or ""
        if not loc:
            return ""
        scene = next((s for s in world.scenes if s.id == loc), None)
        return scene.id if scene else (ev.get("location_name") or loc)

    def _ctx_tag(ev: dict) -> str:
        parts = []
        where = _where(ev)
        if where:
            parts.append(where)
        pids = ev.get("participants", [])
        if pids:
            parts.append("在场者：" + "、".join(pids))
        return f"（{' · '.join(parts)}）" if parts else ""

    def _summary_line(ev: dict) -> str:
        summary = (ev.get("summary") or (ev.get("body") or ""))[:summary_len]
        at = ev.get("at", "")
        return f"- {_id_tag(ev)}{at} {_ctx_tag(ev)} {summary}"

    # 0 = 全部（旧口径"0 = 不给"已废）：必须显式判断，`[-0:]` 在 Python 里等于
    # `[0:]`（取全部）——正好是想要的结果，但那是巧合，不是可读的意图；
    # 负数同理会把尾部砍掉，装配层不该依赖这种巧合。
    recent = ledger.narratives if limit <= 0 else ledger.narratives[-limit:]
    if not recent:
        return []
    # 原文名额只发给**有正文**的事件（2026-09-18 修）：状态记账事件与审计
    # npc_moves 的移动事件形状都是 `body=""`——它们进 narratives 索引是对的
    # （亲历、召回要走索引），但当作"最近剧情原文"渲染只会吐出一行空壳
    # `- 时间（在场者：X）：`，还把最贵的名额（正文原文才是 token 大头）从真正的
    # 正文嘴上抢走。它们本来就有 summary → 一律按摘要渲染：信息不丢、名额不占。
    prose = [ev for ev in recent if (ev.get("body") or "").strip()]
    full_ids = {ev["id"] for ev in prose[-recent_full:]}
    lines = ["事件日志（世界近期发生的事）："]
    # 摘要段因此可能含**比原文段更新**的记账事件（同时刻落账），抬头仍叫"更早"
    # 是按"早于最近剧情那一拍、且不给原文"读的——它同时刻的正文在下一段里。
    summary_events = [ev for ev in recent if ev["id"] not in full_ids]
    if summary_events:
        lines.append("更早事件（摘要）：")
        lines.extend(_summary_line(ev) for ev in summary_events)
    if full_ids:
        lines.append("最近剧情（原文）：")
        for ev in recent:
            if ev["id"] in full_ids:
                lines.append(f"- {_id_tag(ev)}{ev.get('at', '')} {_ctx_tag(ev)}：{ev.get('body', '')}")
    return lines


def _rel_seen(clock: str, at: str) -> str:
    """快照新鲜度：最后目击时刻相对当前时钟的人类表述；任一侧解析失败返回空。"""
    try:
        now = dt.datetime.fromisoformat(clock)
        seen = dt.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return ""
    minutes = int((now - seen).total_seconds() // 60)
    if minutes < 60:
        return "刚刚" if minutes < 10 else f"{minutes} 分钟前"
    if minutes < 1440:
        return f"{minutes // 60} 小时前"
    return f"{minutes // 1440} 天前"


def player_display_name(ledger: Ledger) -> str:
    """主角在名单/契约里的显示名。

    主角是人物表里的一员（2026-09-13），名字就是他自己的中文名。唯一保留的
    退化：作者若把主角名写成占位符「你」，Actor 视图里会出现「你（玩家）」，
    与契约「你 = 你自己」正面打架——此时退化为「玩家」（2026-09-11 原逻辑）。
    """
    name = ledger.world.player_name().strip()
    return name if name and name != "你" else "玩家"


def scene_snapshot_block(
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
    exclude: list[str] | None = None,
) -> list[str]:
    """场景快照：当前时间 / 在场名单 / 当前场景 / 可感知区。

    主角是人物表里的普通一员（2026-09-13），其位置同样由事件流水推导，因此
    名单里正常就有他，只是**多一个「（玩家）」标注**——Actor 视图据此认出谁是
    对话对象。真正的特例只剩"移动途中"：工作单的 scene 是本场正要写的场景
    （可能是本轮才解析出的目的地），主角的位置事实还停在旧场景，此时把他补进
    名单最前——工作单永远是主角所在场景的工作单。

    ``exclude`` 供 Actor 视图剔掉自己。
    """
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    skip = set(exclude or ())
    player = ledger.world.player_name()
    present_ids = [pid for pid in ledger.present_at(scene_id) if pid not in skip]
    if player not in present_ids and player not in skip:
        present_ids = [player, *present_ids]
    clock = ledger.save.clock or ""
    name = player_display_name(ledger)
    parts = []
    for pid in present_ids:
        ev = ledger.where_is(pid)
        seen = _rel_seen(clock, ev.get("at", "")) if ev else ""
        # 玩家写其姓名；姓名本身退化成「玩家」时补 id 以示区分
        label = (name if name == "玩家" else f"{pid}（玩家）") if pid == player else pid
        parts.append(f"{label}（最后目击：{seen}）" if seen else label)
    return [
        "当前时间：" + (clock or "-"),
        "在场（括号内 = 该角色最后被记录在此的时刻，久未见面的要考虑他是否还在）："
        + ("、".join(parts) or "暂无"),
        "当前场景：" + (scene.id if scene else "主街"),
        scene.perceivable if scene else "未知场景",
    ]


def known_set_block(
    world: WorldContent,
    ledger: Ledger,
    present_ids: list[str],
    scene_id: str,
    *,
    known_limit: int,
) -> list[str]:
    """在场 NPC 已知集：机械计算的知识边界（亲历 ∪ known_by ∪ 同区域公开）。

    ``known_limit`` = 每个 NPC 几条，**0 = 全部**（2026-09-16 改，旧口径是
    "0 = 不给"）；同样**不留默认值**，由调用方从 ``Settings.limits()`` 取，
    与 QC / Actor 三处同步。

    取代旧"近况块"——写手与 QC 共用同一份清单，单一事实源。
    """
    lines = []
    player_name = ledger.world.player_name()
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角另有独立段，不重复列
        npc = ledger.world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.known_set(pid, scene_id, known_limit)
        if mem:
            lines.append(f"[{npc.id}] 知道的事：{'；'.join(mem)}")
        else:
            lines.append(f"[{npc.id}] 知道的事：（无——该角色的知识从眼前开始）")
    if not lines:
        return []
    return [
        "在场 NPC 已知集（每个角色的知识边界，表述以其为准；清单外的事件这些角色一律不知道，"
        "包括事件日志里发生在别处的事）：",
        *lines,
    ]


def private_notes_block(world: WorldContent, ledger: Ledger, present_ids: list[str]) -> list[str]:
    """幕后注：作者底牌与角色自知隐秘（主角同 NPC，字段一致）。

    消费方：编剧/导演（作者侧全知）。永不进任何 Actor 切片与玩家视角。
    保密要求由抬头统一声明一次，各条目只列内容（2026-09-11 去重）。
    """
    notes_lines: list[str] = []
    player = world.player()
    player_name = world.player_name()
    if player is not None and (player.private_note or player.personal_secrets):
        notes = []
        if player.private_note:
            notes.append(player.private_note)
        if player.personal_secrets:
            notes.append(f"[{player.id} 自知] {player.personal_secrets}")
        notes_lines.append(f"[主角·{player.id}] {'；'.join(notes)}")
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角的幕后注已在上面独立成条
        npc = world.npcs.get(pid)
        if npc and (npc.private_note or npc.personal_secrets):
            notes = []
            if npc.private_note:
                notes.append(npc.private_note)
            if npc.personal_secrets:
                notes.append(f"[{npc.id} 自知] {npc.personal_secrets}")
            notes_lines.append(f"[{npc.id}] {'；'.join(notes)}")
    if not notes_lines:
        return []
    return [
        "幕后注（仅你可读，只作暗示铺垫用）：",
        *notes_lines,
    ]


def active_lore_block(ledger: Ledger, present_ids: list[str] | None = None) -> list[str]:
    """World book entries for this turn (常驻 + 归属 + save.active_lore_ids).

    三条通道（2026-09-13 归属通道上线）：

    - **常驻**（always_on）每轮必注入、不占触发名额、不吃 5 条上限；
    - **归属**（subject）归属者在场即注入、同样不占关键词名额（每人上限见
      ``LORE_SUBJECT_CAP``）——"一个 NPC 的信息 = 卡 + 归属条目"才齐全，
      他被谈论而本人不在场时，仍回到关键词通道；
    - **触发**（关键词命中玩家输入/已采纳正文）走 save.active_lore_ids，全局 5 条。
    """
    lines = ["世界书（本场可能相关的背景设定，按此设定写）："]
    by_id = {entry.id: entry for entry in ledger.world.lorebook}
    seen: set[str] = set()
    count = 0
    for entry in ledger.world.lorebook:
        if entry.always_on and entry.id not in seen:
            seen.add(entry.id)
            count += 1
            lines.append(f"- 【常驻】{entry.body}")
    for entry_id in subject_hit_ids(ledger.world, list(present_ids or [])):
        entry = by_id.get(entry_id)
        if entry and entry.id not in seen:
            seen.add(entry_id)
            count += 1
            lines.append(f"- 【归属·{entry.subject}】{entry.body}")
    for entry_id in ledger.save.active_lore_ids:
        entry = by_id.get(entry_id)
        if entry and entry.id not in seen:
            seen.add(entry_id)
            count += 1
            lines.append(f"- {entry.body}")
    if not count:
        return []
    return lines


def goal_brief(ledger: Ledger, goal, *, include_ids: bool = False) -> str:
    """单个目标的单行摘要：``[id] [大目标/主线·玩家] 文本（关联：X）``。

    subject 空串 = 主角的目标（默认），否则是该角色的中文名（2026-09-13 改口径，
    此前是魔法串 "player"）。
    """
    id_part = f"[{goal.id}] " if include_ids else ""
    tag = "大目标/主线" if goal.kind == "big" else "小目标/支线"
    owner = goal.subject or "玩家"
    npc_tag = (
        f"（关联：{goal.npc_id}）"
        if goal.npc_id and goal.npc_id in ledger.world.npcs
        else ""
    )
    return f"{id_part}[{tag}·{owner}] {goal.text}{npc_tag}"


def _goals_discipline(has_big: bool, has_loose: bool, has_npc: bool) -> str:
    """引导纪律：按实际存在的目标形态裁剪（不空谈不存在的层级）。

    2026-09-12 分层——大目标是方向盘（每回至少一步、且沿其下子目标走），
    小目标择机落地（场景/在场者相关才推）。这是"大目标与小目标不再只是标签
    不同"的落点：父给方向、子给动作。
    """
    lead = "把本回剧情**自然地**朝这些目标推进——NPC 提起线索、机会现前、冲突冒头；"
    parts: list[str] = []
    if has_big:
        parts.append(
            lead
            + "**大目标是方向盘**：每回至少推进主线一步，且优先沿它下面的小目标走——"
            "小目标就是这条主线的「下一步该发生什么」。"
        )
        if has_loose:
            parts.append("未挂靠的支线只在与当前场景/在场者相关时顺带推一推。")
    elif has_loose:
        parts.append(lead + "只在与当前场景/在场者相关时推一推。")
    # 2026-09-12 去否定化：原句下半是"禁止一轮内生硬给出全部结果"，改为正向——
    # 说清"结果放在哪里"（后续场次），而不是禁止某个动作。
    parts.append("一次只推进一小步，目标的全部结果留给后面的场次逐拍铺开；目标之外的自由展开不受限制。")
    if has_npc:
        parts.append(
            "目标归属者为 NPC 时，由**该 NPC** 在戏里主动推进：NPC 在场 → 让她自然提及"
            "（几句话、一个试探）；NPC 不在场 → 安排她主动来找玩家（登门/路遇/托人带话）；"
            "只对挂在未完成大目标下的子目标这么做（防止支线无限拉人登门）。"
        )
    return "引导纪律：" + "".join(parts)


def goals_block(ledger: Ledger, include_ids: bool = False) -> list[str]:
    """剧情目标（M14）：玩家在导演窗口设立的方向（玩家目标与 NPC 目标），
    编剧写作时必须自然地向其引导。

    大目标 = 章节（往哪去），小目标 = 节拍（下一步做什么）：按 ``big_goal_id``
    渲染成两级树并标章节进度——父子层级在此第一次真正参与运转（2026-09-12）。
    位置未动：仍在资料区尾部、事件日志之前（用户 2026-09-12 明确"位置不要动"）。

    ``include_ids``：导演窗口需要 id 才能废弃/挂父（此前一律不吐 id，导致
    这两个动作实际填不出来）；编剧不需要 id，保持 False。
    """
    from app.ledger.goals import child_progress, goal_tree

    tree, loose = goal_tree(ledger)
    if not tree and not loose:
        return []
    lines = ["剧情目标（玩家设立的方向）："]
    for big, kids in tree:
        done, total = child_progress(ledger, big.id)
        head = "- " + goal_brief(ledger, big, include_ids=include_ids)
        if total:
            head += f"　（子目标 {done}/{total} 已完成）"
        lines.append(head)
        for kid in kids:
            lines.append("　　· " + goal_brief(ledger, kid, include_ids=include_ids))
    if loose:
        lines.append("未挂靠的支线：")
        for goal in loose:
            lines.append("- " + goal_brief(ledger, goal, include_ids=include_ids))
    has_npc = any(g.npc_id for _b, kids in tree for g in (_b, *kids)) or any(
        g.npc_id for g in loose
    )
    lines.append(_goals_discipline(bool(tree), bool(loose), has_npc))
    return lines


def audit_goals_lines(ledger: Ledger) -> list[str]:
    """审计看到的活动目标：带 id 的两级树 + 章节进度。

    父子层级给审计一个**客观锚点**：判大目标完成时能看到"其下子目标 x/y 已完成"。
    只呈现证据、不设硬门锁——判不判仍由审计定（避免机械兜底，也避免误伤
    "玩家绕道达成主线"）。
    """
    from app.ledger.goals import child_progress, goal_tree

    tree, loose = goal_tree(ledger)
    if not tree and not loose:
        return ["（无）"]
    lines: list[str] = []
    for big, kids in tree:
        done, total = child_progress(ledger, big.id)
        head = goal_brief(ledger, big, include_ids=True)
        if total:
            head += f"——其下子目标 {done}/{total} 已完成"
        lines.append(head)
        for kid in kids:
            lines.append("  └ " + goal_brief(ledger, kid, include_ids=True))
    for goal in loose:
        lines.append(goal_brief(ledger, goal, include_ids=True))
    return lines


# ---------------------------------------------------------------------------
# 主观层 · 角色契约（按 agent 身份选择；每个契约 = 身份/写作纪律/输出格式）
# ---------------------------------------------------------------------------

def writer_identity() -> list[str]:
    return [
        "你是 AIWorld 的编剧：你同时负责排戏的走向和正文执笔。",
        "拿到玩家输入后，先在脑中排好本场戏的节拍（先后顺序、谁说什么、情绪转折），再一次性直接写成正文。",
    ]


def writer_story_rules(roster: list[str] | None = None) -> list[str]:
    """二级 · 情节合理性：信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏。

    2026-09-11 由「金科玉律（绝对不可违背）」改名并**降级**：这些规则违反会让
    戏说不通（重掷或改写即可救），但不损坏数据、不影响系统运转——不该与输出
    格式同处「绝对不可违背」的等级。段落抬头只保留等级名，**不带解释句**
    （同日用户判定：抬头的自我说明是废话，等级靠位置与命名即可读出来）。

    ``roster`` 是**本轮可上缴名单**（在场 ∩ 配 Actor，由 ``build_work_order`` 算）。
    这份名单必须显式下发：此前编剧只能自己聚合各人名后「（配 Actor）/（导演代笔）」
    的括号去推谁不能上缴，推错一格就会把无票角色的深抉择上缴上去，而引擎按票
    丢弃 → 那一拍永远是空的（同日用户报的第二类漏洞）。字段结构不在这里讲——
    那属于一级「输出格式」。

    ``player_name`` 参数已于 2026-09-13 移除：context 的人称约定是**字段写法**，
    随字段定义一起搬到一级（二级先于一级，在这里提 context 时它还没被定义）。

    2026-09-12 去否定化：本节原有多处"不得 / 不要 / 绝不能"与枚举式禁令
    （最重的是「不出戏」里逐一列出 AIWorld、系统、编剧、玩家输入四个禁词），
    改为描述**目标状态**（把这一拍写到抉择点为止 / 正文就是玩家能感知到的一切）。
    用户实测：否定措辞越多的提示词，模型表现越差——枚举禁词等于把禁项递到
    模型眼前，注意力从写作挪去自查。
    """
    if roster:
        roster_clause = (
            "本轮可上缴深抉择的角色：" + "、".join(roster) + "，其余你直接写。"
        )
    else:
        roster_clause = "本轮没有任何角色可上缴深抉择，所有抉择你直接写。"
    return [
        "【二级 · 情节合理性】",
        "信息边界（角色不是你）：事件日志、幕后注、世界书都是你案头的编剧资料，角色本人并不知道——"
        "每个角色开口前核对台词是否在他已知范围内（在场 NPC 见工作单「已知集」清单）；"
        "真相只经由知情者之口进入正文（知情者当场坦白，那是新戏）；"
        "无人物卡的即兴角色只知道眼前可见的东西。",
        # actor_questions / context 的**字段写法**不在这里展开（2026-09-13 用户指出：
        # 二级先于一级，此处提到 context 时它还没被定义）——只讲机制并指向一级。
        "抉择归属：标（配 Actor）的角色撞上深抉择时，"
        "把这一拍写到抉择点为止，在 actor_questions 里上缴（字段写法见「一级 · 输出格式」）——"
        "决策回来后再改稿补完；"
        "标（导演代笔）的角色与普通对话由你直接写出来。"
        + roster_clause,
        "设定一致（位置）：在场名单是最近一次记录的快照，可能已过期——结合每人最后被目击的时刻"
        "与其人物卡合理推断他此刻在哪，找到、扑空、他挪了地方都是合理的叙事。",
        "连续性：玩家已经历过的事属于背景，直接接续当下的戏，只处理本回合的输入。",
        "不出戏：正文就是故事世界里玩家能感知到的一切。",
    ]


def writer_style_block(preset: NarrativePreset) -> list[str]:
    """三级 · 文风与剧情倾向：默认遵循，可按情境灵活（2026-09-11）。

    这一级是**偏好**不是硬边界。写明等级不是给它降格，而是让模型敢于为了
    戏的张力偏离它——此前它与「绝对不可违背」的规则混在一起，模型只能
    一律照办，结果是越写越保守。抬头只留等级名，不带解释句（同日用户判定）。

    style_sample（文风示范，2026-09-11）：紧贴 guidelines 注入，只留
    「文风示范：」标记 + 原文——贴身禁令经用户实测后要求去掉（22:47 功能
    上线时带禁令，23:08 用户要求先试无禁令版）。空 = 不注入，零成本。
    """
    lines: list[str] = []
    if preset.writer_guidelines:
        lines += ["【三级 · 文风与剧情倾向】", preset.writer_guidelines]
    if preset.style_sample:
        if not lines:
            lines.append("【三级 · 文风与剧情倾向】")
        lines += ["文风示范：", f"「{preset.style_sample}」"]
    return lines


def writer_output_format(player_name: str = "玩家") -> list[str]:
    """一级 · 输出格式：最硬的一级（2026-09-11 用户定级）。

    格式一旦出错，前端直接解析失败、玩家看不到正文也无法继续——这一级没有
    「灵活处理」的余地，是全表唯一的硬边界。抬头只留等级名，不带解释句
    （同日用户判定：抬头的自我说明是废话）。

    位置：紧跟三级预设块之后，使 二级 → 三级 → 一级 连成完整梯队，资料区
    不被切开（2026-09-11 用户调整，此前置于资料区与事件日志之间）。

    同日迁入：``actor_questions`` 的**条目结构**。此前字段名塞在二级的括号里、
    模板里只有一个空数组，模型得自己猜 ``npc_id`` 填什么——填「你」「玩家」或
    自造 id 都能通过校验，然后静默匹配不上（用户追问点）。字段结构属于格式层，
    迁到一级并给出带值的样例。

    2026-09-13 再迁入：``context`` 的**人称约定与范围**。用户指出二级先于一级，
    读到"context 的人称约定"时 context 还没被定义（前向引用）——字段写法就该
    贴着字段定义。二级只留机制（何时停笔上缴）并指向本节。

    ``player_name`` 走 ``player_display_name``：context 里提到玩家一律写其
    **姓名**（2026-09-11 用户改口径，此前是写「玩家」）；主角名未设定时退化
    为「玩家」。
    """
    ctx_clause = "「玩家」" if player_name == "玩家" else f"玩家姓名「{player_name}」"
    return [
        "【一级 · 输出格式】",
        "输出必须是 JSON 对象，字段：",
        '{"prose": "正文全文", "summary": "一句话摘要（不超过30字）",'
        ' "actor_questions": [{"npc_id": "朱明", "question": "…", "context": "…"}]}',
        "prose：本场戏正文全文——从第一句话写到本场结束（每稿都是完整全文）。",
        "summary：整场核心事件的摘要，供事件日志使用（与 prose 同样覆盖整场）。",
        "actor_questions：深抉择的提问清单，没有时给空数组 []；每项三个字段：",
        "  · npc_id：必须是在场名单方括号里逐字出现的角色名（如 朱明）。",
        "  · question：你要问他的那个抉择本身。",
        f"  · context：人称约定只约束这个字段——以该 NPC 为「你」，"
        f"提到玩家一律写{ctx_clause}，一句只用一个人称；范围限于该 NPC "
        "本人会知道的情境，不留你的判断与幕后注。",
        "时间、地点、在场者、私密情境等世界变化由引擎从你的正文结算——你只把变化写清楚"
        "（如「天黑了」）。字段以上面三个为准。",
    ]


def actor_contract(npc_name: str, player_name: str = "玩家") -> list[str]:
    """NPC Actor 的身份、隔离纪律与输出格式（物理隔离工作单）。

    人称读法必须讲明（2026-09-11）：情境文本里的人称由 writer 侧约定产生
    （以该 NPC 为「你」、提到玩家写其**姓名**），这里给出对应的读法，两边
    对齐后就不再需要机械替换。``player_name`` 走 ``player_display_name``——
    主角名未设定（占位符「你」）时退化为「玩家」。
    """
    return [
        f"你正在扮演：{npc_name}。你的决定只用下面「你知道的事」里的信息。",
        "人称读法（先把这几个词认准）：",
        f"- 「你」= 你自己（{npc_name}）；情境里用「他/她/名字」提到你，也是在说你。",
        f"- 「{player_name}」= 你的对话对象（人类玩家），是另一个人，不是你。",
        "- 归属有矛盾时以「你知道的事」为准：清单里没有的事，就不是你做的。",
        "纪律（本契约的硬边界）：",
        "- 你说的话来自「你知道的事」；超出这个范围的部分，留给知道的人去说。",
        "- 「你此刻的状态」与「在场旁人的状态」两段（若出现）与「你知道的事」同级，"
        "都能拿来作决定：前者是你自己身上的事，"
        "后者是你当场看得出来的样子。两段都没写到的，就是你不知道的。",
        f"- 只输出你自己的决定（decision / action_hint / tone）；{player_name}的抉择留给本人。",
        '只返回 JSON，格式如下：',
        '{"decision": "你的决定", "action_hint": "你会做的动作/行为", "tone": "语气"}',
    ]


def build_actor_work_order(
    world: WorldContent,
    ledger: Ledger,
    npc_id: str,
    scene_id: str,
    *,
    limits: InjectionLimits = DEFAULT_LIMITS,
) -> str:
    """Physically isolated work order for one NPC's deep choice.

    Cut per TDD §6: the actor gets world hard rules, the scene's perceptible
    area, its own card (incl. persona patch), **its own states + the present
    others' ``public`` ones** (2026-09-15) and its own memory slice only.
    No private notes, no other NPCs' secrets, no lore candidates, no goals,
    no conflicts, no writer motivation.
    """
    npc = world.npcs.get(npc_id)
    if npc is None:
        raise ValueError(f"unknown npc: {npc_id}")
    parts = [
        *actor_contract(npc.id, player_display_name(ledger)),
        *world_summary_block(world),
        "你的档案（人物卡）：",
        f"姓名：{npc.id}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}",
    ]
    if npc.personal_secrets:
        parts.append(f"你心里的事（只有你自己知道）：{npc.personal_secrets}")
    # 状态紧贴人物卡：自己身上发生的事，与"他是谁"同一段读起来最自然（与编剧侧
    # character_block 同一位置逻辑）。自己的全给、旁人只给 public（见函数注释）。
    parts += actor_state_block(ledger, npc.id, ledger.present_at(scene_id))
    # 在场名单剔掉自己；主角在名单里自带「（玩家）」标注，Actor 据此认人
    #（2026-09-13 主角入人物表后不再需要"显式补上玩家"那一步）。
    parts += scene_snapshot_block(world, ledger, scene_id, exclude=[npc_id])
    # 记忆口径与写手/QC 一致：known_set（亲历 ∪ known_by 含己 ∪ 听域内公开）。
    # 条数上限 2026-09-16 起统一走系统设置（原先挂 world.meta.memory_limit——
    # 那是世界设定，可"嫌 NPC 记性差"是跨世界的偏好，换个世界就得重配一遍）。
    mem = ledger.known_set(npc_id, scene_id, limits.known_set_limit)
    if mem:
        parts += ["你知道的事：", *mem]
    return "\n".join(parts)


def build_director_chat_system(
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
    *,
    limits: InjectionLimits = DEFAULT_LIMITS,
) -> str:
    """Work order for the director-window chat (OOC).

    Reads the same objective ledger blocks as the writer, plus the list of
    backstage actions the player may request and the requirement to end a
    discussion with a copyable input suggestion.
    """
    scene_id = scene_id or ledger.current_scene()
    present_ids = ledger.present_at(scene_id)
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    names = list(present_ids)

    parts: list[str] = [
        "你是 AIWorld 的导演，玩家正在戏外（OOC）和你讨论。你不是正文执笔者，一切建议都要玩家采纳后才生效。",
        "世界概要：",
        *world.meta.summary,
        "当前时间：" + (ledger.save.clock or "-"),
        "当前场景：" + (scene.id if scene else scene_id),
        "在场：" + ("、".join(names) or "暂无"),
    ]
    parts += event_log_block(
        world,
        ledger,
        limit=limits.event_log_limit,
        recent_full=limits.event_log_full,
        include_ids=True,
    )
    # include_ids=True：导演要靠 id 废弃目标、把子目标挂到某个大目标下
    #（2026-09-12 之前一律不吐 id，导致 set_goal 的废弃/挂父实际填不出来）。
    parts += goals_block(ledger, include_ids=True)
    parts += known_set_block(
        world, ledger, present_ids, scene_id, known_limit=limits.known_set_limit
    )
    parts += active_lore_block(ledger, present_ids)
    parts += private_notes_block(world, ledger, present_ids)
    # with_states=False：状态改由下面那份全量带 id 的清单统一给（见
    # director_states_block 的说明——两处口径不同会现场打架）。
    parts += character_block(world, ledger, present_ids, include_ids=True, with_states=False)

    state_rows = director_states_block(ledger, present_ids)
    if state_rows:
        parts += [
            "角色状态（长期事实；方括号内为状态 id，撤销时引用它）：",
            *state_rows,
        ]

    parts += [
        # action 的触发条件只在下方"输出字段"一节说一次（2026-09-12 去冗余）：
        # 此前抬头也重复了一遍"只在玩家明确要求时才填 action"。
        "可执行的幕后操作：",
        "- 覆写 override：声明某人/某物在哪或去做什么 → payload {subject, location}",
        "- 记忆注入 inject_memory：给某 NPC 私下注入一条记忆（只有他知道）→ payload {npc_id, memory}",
        "- 访问改判 access_rejudge：某事件改为公开或私密 → payload {event_id, known_by: [知情者...] 或 null}",
        "- 剧情目标 set_goal：设立 → payload {text, kind: big|small, subject?, big_goal_id?, npc_id?}；"
        "废弃 → payload {goal_id, status: abandoned}。"
        "大目标=主线章节、小目标=支线节拍；subject=归属者（留空=主角）；"
        "小目标用 big_goal_id 挂到大目标下；"
        "额度：大 ≤2 / 单大目标下子 ≤3 / 未挂靠支线 ≤2；大目标完成或废弃时其下子目标一并撤下",
        "- 退场 retire：某人永久离开舞台（死亡/远行/消失）→ payload {npc_id}；"
        "不可逆，退场者退出在场推导与主动调度",
        f"- 状态新增 state_add：加一条长期事实 → payload {{npc_id, text, until?, public?}}；"
        f"text ≤ {STATE_TEXT_MAX} 字，只写「他此刻是什么」不写成因；"
        "until 只在玩家给了明确期限时填（「三天」→ 当前时间 +3 天的 ISO 时刻）；"
        "public = 旁人看不看得见（false 就是上方清单里标着「秘」的那条），默认 false",
        "- 状态撤销 state_revoke：→ payload {state_id}（上方清单里的方括号 id，逐字照抄）；"
        "找不到 id 时退而给 {npc_id, text}（与清单逐字一致）",
        f"- 状态修订 state_revise：**改**一条已有状态的字段（写错的字 / 期限 / 是不是「秘」）"
        f"→ payload {{state_id, 要改的字段}}。**只给要改的字段，其余键不要出现**"
        f"（写了 public: false 就等于把它改成 false）；until 给空串＝清掉期限；"
        f"文字仍受 ≤ {STATE_TEXT_MAX} 字与同角色不重复的约束；已到期的条目改不了"
        "（它已经宣布结束了，要复活得撤销后重加）。只是改错别字就用它，"
        "别用「撤销 + 新增」——那会把「什么时候起就是这样的」重置成现在",
        "（人物卡 / Actor 档位走世界工作台；新角色与新场景的落卡走左栏「待落卡」窗口，不走这里。）",
        "纪律：action 是一份提案，玩家确认后才生效。"
        "state_add / state_revoke / state_revise 只在玩家明确要求时提——他在改自己的世界，"
        "不必拿「这算不算长期事实」拦他。",
        "讨论有明确结论时，末尾给一句可直接复制进正文框的输入建议。",
        "",
        "只返回 JSON，字段：",
        '{"reply": "给玩家的戏外回复", "action": null | {"type": "override|inject_memory|access_rejudge|set_goal|retire|state_add|state_revoke|state_revise", "payload": {"字段": "值"}}}',
        "action：只有玩家明确要求执行幕后操作时才填，否则 null。",
        "payload 里的 npc_id / subject 用角色中文名（与在场名单、角色资料逐字一致）。",
        "输出字段只有 reply 与 action 两个。",
    ]
    return "\n".join(parts)


def build_audit_work_order(world: WorldContent, ledger: Ledger, scene_id: str) -> str:
    """Audit work order: settle world side effects from the prose.

    The audit infers time advance, location, presence, privacy and one-shot
    vs registered scenes from the narrative, then judges goal completion
    (M14 剧情目标)、生命周期，以及**角色状态的获得与结束**（Step 2，2026-09-14）。

    时间是这块的**唯一结算方**（2026-09-16 起）：规则侧的跳时解析已整条下线，
    所以这里不再有 ``settle_hint``——没有"规则先定钟、审计改估"的重复计费问题，
    审计按常识自行估计即可（原委见 ``app/rules/route.py`` 模块注释）。
    """
    scene_id = scene_id or ledger.current_scene()
    player_name = world.player_name()
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    names = list(present_ids)
    scene_list = "、".join(s.id for s in world.scenes)
    # 地点候选册（落卡窗口 2.0，2026-09-19）：待落卡 / 已定为临时的地方也要报给
    # 审计，让它**沿用同一个中文名**而不是自造变体——否则同一个地点会在候选册里
    # 越攒越多条，玩家得一条条处理同义重复。它们还没进场景表，按状态分开标。
    location_lines: list[str] = []
    for label, names in (
        ("待落卡", ledger.pending_locations()),
        ("已定为临时", [n for n, item in ledger.save.locations.items() if item.status != "pending"]),
    ):
        if names:
            location_lines.append(
                f"{label}的地点（还没进场景表；提到它们时**沿用这些中文名**，别自造变体叫法）："
                + "、".join(names)
            )
    state_lines = [
        "- state_add：只收**长期事实**（体质 / 伤残 / 病症 / 能力 / 身份处境）。"
        "自检：**过几天、几十轮回头看，这条还成立吗？**戏散就没了 → 不写。",
        "  **一律不收**：① 消息 / 情报 / 别人的事（\"听说她妈住院了\"）——那是**事件**，"
        "长线追踪走**剧情目标**；② 一次性的当下（心情 / 手上有泥）；"
        "③ 该角色自己的隐秘与前史（\"他爸欠债\"）——那是**人物卡**。",
        "  只填正文**已经写成事实**的；不写意图、不写别人的评价。"
        "**拿不准 → 空数组**（状态常驻后续每轮的提示词，宁缺勿滥）。",
        f"  同一角色本回合最多 {STATE_ADD_PER_CHAR} 条；text ≤ {STATE_TEXT_MAX} 字，**只写"
        "「他此刻是什么」，不写「他怎么变成这样的」**——成因（含括号补注）留在产生它的"
        "那场戏里，不复述（写「左腿瘸了」，不写「沐浴龙血」）。"
        "until：能说清终点才给（\"病倒三天\" → 当前时间 +3 天的 ISO 时刻），说不出的留空"
        "——**说不出终点，多半它就不该是状态**。"
        "public = 旁人**看不看得见**：可见 → 写可观察的表现（\"左腿瘸了\"）；"
        "不可见（默认）→ 写不外露的事实（\"其实色盲\"）。",
        "- state_remove：正文写出某个**已有状态结束**（伤好 / 毒解 / 失能）时，"
        "给出「角色状态」清单里的方括号 id；拿不准 → 空数组（宁可留着）。",
    ]
    return "\n".join(
        [
            "你是 AIWorld 的世界审计：从**已采纳**的正文里结算世界副作用，并判定目标 / 退场 / 状态。",
            "只返回 JSON：",
            '{"location": "场景id", "scene_name": "", "register_scene": false,'
            f' "participants": ["{player_name}", "朱明"], "featured": ["朱明"],'
            ' "private": false, "delta_minutes": 0,'
            ' "npc_moves": [{"npc_id": "朱明", "location": "朱明家"}],'
            ' "clock_to": "",'
            ' "completed_goal_ids": ["达成目标id"], "lifecycle": [{"npc_id": "朱明", "status": "retired"}],'
            ' "state_add": [{"npc_id": "刘星", "text": "普通刀剑伤不了他", "public": true, "until": ""}],'
            ' "state_remove": ["st_3f2a91c04d7e"]}',
            "判定规则：",
            *state_lines,
            "- location：玩家此刻所在之处；正文明确写他移动（离开 / 去别处 / 回家）才更新，否则保持原场景。",
            "- participants（与 location 联动，二选一）。"
            "**口径 = 玩家本人 ∪ 在场的、有名字的角色**——没名字的布景人物"
            "（酒保 / 伙计 / 摊主 / 路人）**一律不进名单**，戏份多少都一样：",
            "  · **没移动**：以下方在场名单为**默认基线**原样继承；只有正文明确写某人离开（走掉 / 告辞）"
            "才移除，明确写新人到场且需记入史实才加；代词指代不清时保守保留。",
            "  · **移动了**：留在原地的人不进名单；正文里在**新场景出现且有名字**的角色"
            "（同行 / 被找到 / 搭话）都计入。",
            "- featured：本轮**与玩家实际有往来、且有名字**的角色——玩家问他话、他答话、"
            "有名字的第三方向玩家转述都算。只是\"在场景里杵着\"不算；**没名字的不收**"
            "（没名字就无从落卡）。与 participants 的区别：在场是**位置**（可继承），"
            "出场是**这一轮真的碰上了**（不可继承，必须由本轮正文产生）。多数回合为空。",
            "- npc_moves：正文明确写出某 NPC **离开去了别处**并写明去向 → 给 "
            "{\"npc_id\": \"中文名\", \"location\": \"去向（新地点给中文名）\"}；"
            "只说\"走了\"没说去哪 → 不给（位置不变）。只记 NPC"
            "（玩家移动由 location 覆盖），多数回合为空。",
            "- 场景已在场景表 → 用其中文名；进入新地点 → location 与 scene_name 都给同一个中文名。"
            "register_scene 是**建议落卡**（不立刻生效）：玩家要去 / 回访 / 会复用 → true"
            "（该地点进候选册，等玩家在落卡窗口拍板）；剧情顺笔的一次性舞台（如今晚的草地）"
            "→ false（不进任何表，显示名仍可用）。",
            "- private：判据是\"外人会不会知道这事发生过\"，不单看地点。私下交底 / 咬耳朵 / "
            "无人看见的交易 / 隐蔽处行事 → true；当众冲突、公开对话 → false。",
            "- clock_to（**优先**）：只要**玩家输入或正文给出了任何明确时刻或日界**"
            "（\"明天八点去学校\"\"第二天早上\"\"晚上十点见\"\"后天\"），就落这个时刻，"
            "格式 YYYY-MM-DDTHH:MM:SS（日期默认跟当前钟走，跨天才改；只说了日界没说钟点时"
            "按常识取一个合理钟点）——**此时 delta_minutes 必须给 0**，不要退回估时长。",
            "- delta_minutes：**仅当上面没有任何明确时刻**时才用——自行估计这场戏"
            "实际经过多久（分钟），以玩家经历了什么为准；没推出任何时间就 0。",
            "- 时间**只向前**：宁可给 0 也不要倒退，也不要因为剧情提到过去就回拨时钟。",
            "- completed_goal_ids：正文已达到目标所述才填。**推进不算完成**"
            "（NPC 提及 / 推进目标只是推进）——审计只判终点，推进是编剧的事。"
            "大目标是章节：其下「子目标 x/y」只作参考证据、不是判据——正文给出关键了结"
            "即可判完成（玩家绕道达成也算），只推了推、冲突没解决 → 不判。",
            "- lifecycle：正文语义上的退场（死亡 / 永久离开）→ retired。",
            "当前时间：" + (ledger.save.clock or "-"),
            "当前场景：" + (scene.id if scene else scene_id),
            "在场：" + ("、".join(names) or "暂无"),
            # 角色状态（2026-09-14）：不注入的话审计会判出与状态自相矛盾的
            # 结论（玩家免疫普通武器，正文却写他被砍成重伤）。带 id——移除状态
            # 要靠 id 精确定位（Step 2）。
            *states_block(ledger, present_ids, include_ids=True),
            "已注册场景（location 用这些中文名，新地点也照此给中文名）：" + (scene_list or "（无）"),
            *location_lines,
            "活动目标：",
            *audit_goals_lines(ledger),
            *world.meta.summary,
        ]
    )


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def build_work_order(
    viewer: str,
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
    preset: NarrativePreset | None = None,
    limits: InjectionLimits = DEFAULT_LIMITS,
) -> str:
    """Final work order for one agent.

    viewer="writer": merged director+storyteller (omniscient).
    viewer="actor_<npc_id>": physically isolated NPC deep-choice view.

    ``limits`` = 注入上限（系统设置）。生产路径由 TurnRunner 传
    ``settings.limits()``；不传时退回 ``DEFAULT_LIMITS``（探针/单测用）。
    """
    if viewer == "writer":
        scene_id = scene_id or ledger.current_scene()
        preset = preset or world.presets
        present_ids = ledger.present_at(scene_id)

        parts: list[str] = []
        # A 身份与任务
        parts += writer_identity()
        # ── 三级写作纪律：二级 → 三级 → 一级 连成完整梯队（2026-09-11 用户调整）
        # 二级 · 情节合理性（信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏）
        # roster = 在场 ∩ 配 Actor：本轮谁可以被上缴深抉择（零 LLM 的配置量）。
        # 编剧据此决定该不该为某个角色的抉择停笔，引擎事后也按同一份名单裁决。
        # 主角恒不在名单里：他的抉择只能由玩家给（2026-09-13 主角入人物表后显式排除，
        # 不再依赖"主角卡没勾 Actor"这一约定）。
        roster = [
            pid
            for pid in present_ids
            if pid != world.player_name()
            and (npc := world.npcs.get(pid)) is not None
            and npc.has_actor
        ]
        parts += writer_story_rules(roster)
        # 三级 · 文风与剧情倾向（预设；默认遵循，可灵活）
        parts += writer_style_block(preset)
        # 一级 · 输出格式（全表唯一的硬边界；context 的人称约定随字段定义在此，
        # 2026-09-13 由二级迁入——避免二级先引用了尚未定义的 context）
        parts += writer_output_format(player_display_name(ledger))
        # ── 资料区：以下与事件日志同性质，都是写作取材，一律不标规则等级 ──
        # 世界概要（常驻，永不裁剪）
        parts += world_summary_block(world)
        # 世界书命中（本场相关背景：常驻 + 归属在场者 + 关键词触发）
        parts += active_lore_block(ledger, present_ids)
        # 玩家资料 + 在场 NPC（含 id 标注，编剧须用规范 id）
        parts += character_block(world, ledger, present_ids, include_ids=True)
        # 幕后注（机密，与二级「信息边界」呼应双保险）
        parts += private_notes_block(world, ledger, present_ids)
        # 场景快照（此刻环境：当前时间/在场/场景/可感知）
        parts += scene_snapshot_block(world, ledger, scene_id)
        # 在场 NPC 已知集（机械知识边界，取代旧近况块）
        parts += known_set_block(
            world, ledger, present_ids, scene_id, known_limit=limits.known_set_limit
        )
        # 剧情目标（写作引导，贴近动笔位置）
        parts += goals_block(ledger)
        # 事件日志（更早摘要在前 → 最近原文在后，收尾紧贴玩家输入以便续写）
        # 摘要行不带玩家原话（本轮输入另有 user 消息下发）
        parts += event_log_block(
            world,
            ledger,
            limit=limits.event_log_limit,
            recent_full=limits.event_log_full,
        )
        return "\n".join(parts)

    if viewer.startswith("actor_"):
        npc_id = viewer[len("actor_"):]
        return build_actor_work_order(
            world, ledger, npc_id, scene_id or ledger.current_scene(), limits=limits
        )

    raise ValueError(f"unknown viewer: {viewer}")