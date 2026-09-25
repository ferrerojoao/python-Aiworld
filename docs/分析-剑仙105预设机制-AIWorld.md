# 分析：剑仙105 预设的机制与可吸收项

> 来源：`C:\Users\soony\Downloads\105.json`（4.69 MB，SillyTavern 预设，`name = 剑仙105`）
> 方法：按 `prompt-style-tuning` 的纪律——**解析文件结构，不读界面文字、不凭名字断言**；先看实际发送内容，再看扩展开关。
> 结论一句话：**它的效果不来自"写了更多更好的规则"，而来自换掉了杠杆——把文风从"规则"改成"分布 + 微范文"，并用确定性代码做全部测量与选择。**

---

## §1 它是什么

| 项 | 值 |
|---|---|
| 顶层 | `name=剑仙105`；`prompts[]` 只有 12 条，最长的一条正文 520 字符 |
| 真正的内容 | 99% 在 `extensions`，**3.34 MB 是 3 个 tavern_helper 脚本** |
| 脚本体积 | `s0=1.62MB / 15828 行` · `s1=1.46MB`（内置 673 条世界书）· `s2=50KB` |
| 版本跨度 | 分区注释从 `JX16` 排到 `JX105`；`jx_native_layer_manifest` 有 **350 个键**的版本台账，11 份 `jxNN_release` 发布记录 |
| 采样参数 | `temperature=1` · `frequency_penalty=0` · `presence_penalty=0` · `top_p=0`(关) · `top_k=500` · `repetition_penalty=1` · `reasoning_effort=high` |
| 台账里的自述 | `generation_parameters_changed: false` 反复出现 ⇒ **采样层被作者显式排除**，不是它的杠杆 |
| 外部依赖 | 两份**物理世界书**：`Core Source`（38 条）+ `Style Distribution Source`（37 条），脚本自动安装 |
| 5 个"流派"(flow) | `DM` · `DM_FP_ACTOR` · `DIRECTOR` · `CHAT` · `NARRATOR`，同一机制按流派给不同 profile |
| 额外模型调用 | 全文 `extra_model_calls: 0` —— 所有测量/选择/门控都是**确定性代码** |

---

## §2 为什么效果出色：它换掉了杠杆

按 `prompt-style-tuning` 的杠杆表对照，它的每一步都踩在高杠杆格上，而且**主动删掉了低杠杆格里的东西**：

| 杠杆位 | 它的做法 | 判断 |
|---|---|---|
| 权重层 | 没碰（模型自带） | — |
| **上下文范文** | 独立文风世界书 37 条（含 `kind:"exemplar"`），延迟绑定 + 微范文 | ✅ **主战场** |
| **段/文风的定义** | 文风 = 一组**分布参数**（register / 句律 / 对白密度 / 比喻密度 / 收尾压力…） | ✅ |
| 采样参数 | 全默认，且台账里写明没改 | 显式排除 |
| 当场自查 | 有，但**只做全篇分布比对，不做逐句清单** | ✅ |
| 文字规则与禁令 | 🔴 **主动删空**：`NO_PHRASE_BLACKLIST｜NO_PER_SENTENCE_STYLE_CHECKLIST` | 反向操作 |

**一句话**：别人在"禁令"这一格反复加字，它把这一格删了，把资源搬到"范文 + 分布"那两格。

---

## §3 逐机制拆解

### 3.1 文风 = 分布参数，不是规则（`JX-STYLE/1.1`）

文风 profile 是一份**参数化的分布**，不是一段散文要求：

```
register=MODERN_COLLOQUIAL   narrative_distance=CLOSE   sentence_rhythm=VARIED
dialogue_density={target:MEDIUM, range:[0.20,0.60]}
dialogue_rendering=CONTEXTUAL   action_dialogue_coupling=MEDIUM
authorial_explanation=LOW   figurative_density=LOW_MEDIUM
emotional_explicitness=MEDIUM   closure_pressure=LOW
lint_targets={corrective_per_k:[0,2], quantified_time_per_k:[0,1.5],
              metaphor_per_k:[0,3], authorial_explain_per_k:[0,1.5], emdash_per_k:[0,3]}
```

**为什么有效**：规则说"不要写成说明文"，模型不知道自己的产出算不算；参数说 `authorial_explanation=LOW` + `authorial_explain_per_k ∈ [0,1.5]`，**是一个可被检验的量**。

关键的一条边界声明：

```
style_scope=LEXICON,SENTENCE_RHYTHM,DIALOGUE_DENSITY,NARRATIVE_DISTANCE,
            DESCRIPTION_TEXTURE,FIGURATIVE_DENSITY,SURFACE_EXPLICITNESS,CLOSURE_FEEL
style_not_scope=PERSONALITY,KNOWLEDGE_STATE,RELATION_FACTS,PLOT_OWNERSHIP,
                PLAYER_AGENCY,POV,HARD_FACTS,SCENE_OBLIGATIONS
style_content_duty=NONE   ← active style cannot create a requirement to demonstrate
                            emotion, personality, autonomy, conflict, symbolism or a literary device
```

🔑 **`style_content_duty=NONE` 是整套设计的承重墙**：文风被迫只负责"表面怎么落"，**被明确禁止产生"内容义务"**。八股的根因——"这段好像还没写透，得补一个文学动作"——在权限层就被掐掉了。

### 3.2 范文独立成资产 + 延迟绑定

- 文风条目存在**独立的物理世界书**里（`Style Distribution Source`，37 条），与"世界设定"完全分家。条目按 `kind` 分三类：`profile` / `variant` / **`exemplar`**。
- 选择是**确定性**的（`jxStyleSelectExemplars`）：用 `(flow + 上一轮正文尾部800字 + 行为类别 + 事件气质)` 做 FNV 哈希当种子，在候选池里抽样，**上限 2 条**，并优先命中与当前"行为类别"（回嘴/自嘲/保面子/试探/沉默…）匹配的那条。
- 注入位置与标签：

```
authority=FINAL_SURFACE_REALIZATION_HIGH｜placement=AFTER_STRUCTURE_AND_CREATIVE_SOURCE_BEFORE_PROSE
【JX50_LATE_STYLE_MICRO_EXEMPLARS】
S1|surface_example_only|do_not_copy_content
<范文正文>
```

🔑 三个要点：**① 只给 2 条**（不堆）；**② 标 `surface_example_only|do_not_copy_content`**（防抄内容，只取表面）；**③ 位置在"结构之后、正文之前"**（近因区 + 避开被读成"资料"）。

> 这正是本项目 `style_sample` 该长的样子 —— 而它现在是**空的**（见 §6）。

### 3.3 历史降级为"事实源"，不是"写法范本"

这是最值得直接搬的一段。它把历史继承拆成两条互不干扰的通道：

```
history_fact_authority=PRESERVE_OCCURRED_FACTS
history_relation_and_knowledge_authority=PRESERVE_CONTINUITY
history_generic_narrative_style_authority=CONDITIONAL_REFERENCE
history_output_length_authority=SINGLE_TURN_NONE__STABLE_MULTI_TURN_RANGE_WEAK_REFERENCE
anti_contagion=一次明显算力下降或机器泄漏造成的低质量表面，只保留其已发生事实/关系/知识，
               不作为后续措辞、节奏和篇幅老师。
quality_policy=DO_NOT_SCORE_SUBJECTIVE_LITERARY_QUALITY｜only_objective_anomaly_quarantine=TRUE
```

配套的 A/B 补丁（`JX76`）只用 8 句自然语言，是全文性价比最高的一段：

> 把历史当作已经发生的经历，而不是这一轮的写作范本。
> 继承事实、关系、共同经历、角色记忆、角色自己的稳定说话方式，以及仍在继续的动作、情绪和因果。
> 写这一轮时，从当前玩家输入、当前场景、人物此刻的状态和当前启用的文风重新决定怎样表达。
> 历史回复里的句式、段落节奏、叙述习惯、动作模板、比喻、措辞、收尾方式和单轮篇幅，即使以前出现过或连续出现过，也不会自动成为这一轮的写法规则。
> 角色自己的声音可以延续；叙述表达根据当前这一刻重新生成。
> 历史告诉你发生过什么；当前这一刻决定你现在怎么写。
> **记住经历，不复制写法。**

🔑 **`SINGLE_TURN_NONE`** 这一条是对"模型抄自己上一轮长度"的精准拆解：**单独一轮没有继承权**，只有"多轮稳定形成的区间"才算**很弱的**参考，且本轮长度由当前流派/玩家要求/剧情义务决定。

**它的"隔离"是代码判的、不是模型自省的**：取最近 8 条 assistant 正文，算前 7 条字数的中位数；若最新一条 ≤ `max(260, 中位数×0.28)` 判 `OBVIOUS_LENGTH_COLLAPSE`，或正文里漏出机器标签判 `MACHINE_SURFACE_LEAK` ⇒ 把这一条标 `QUARANTINE`，改取"最近一条无异常的"当文风参照。**明确拒绝给文学质量打分**（`DO_NOT_SCORE_SUBJECTIVE_LITERARY_QUALITY`），只认客观结构异常。

### 3.4 去八股 = 撤销一个从没被写出来的"文学完成配额"（`DEBAGU` 模块）

模块名直译"机器去八股"，它的 policy 只有几句，但每句都在拆一个具体病灶：

```
policy=REALIZATION-SELECTION/1.0｜goal=Reduce unnecessary literary completion work
       without imposing a replacement style.
already_carried=对白、动作或已成立事实已承载该 beat 时，继续下一个承载场景的动作/交换/后果/
                观察/事实，而不是把同一个意思再翻译一遍。
no_completion_quota=No literary device is a completion requirement. A beat does not need an
                 extra expressive cue merely to look finished, emotional, deep, atmospheric, or literary.
multi_function=一句话自然承担多个叙事功能时，保留这种压缩；不要为了满足本守卫把好文字压平。
semantic_delta=新措辞应当带来场景推进/信息/人物或关系后果/感知/必要解读。
grounding=不要为了把一段解读写得生动或权威，就现编证据、动作、记忆、因果、数字。
effort_routing=普通过渡不需要文学完成；重要或真正模糊的时刻才多花推理。
              不要把这笔预算花在"已经清楚"的 beat 的装饰上。
lint_scope=NO_PHRASE_BLACKLIST｜NO_PER_SENTENCE_STYLE_CHECKLIST
priority=PLAYER_EXPLICIT_WORDING_IF_LOCKED > FACT_AND_EVENT_FIDELITY > CHARACTER_VOICE >
         MODE_CONTRACT > ACTIVE_STYLE > REALIZATION_SELECTION > RHETORICAL_POLISH
```

🔑 四个可直接借用的判断：
1. **`no_completion_quota`** —— 八股的机制被命名了：模型默认认为"一段话必须带一个文学完成动作才算写完"。解法不是禁止华丽，而是**撤销这个配额本身**。
2. **`already_carried`** —— 给了可执行的判据：*"我是不是把同一个意思再翻译了一遍？"*
3. **`semantic_delta`** —— 给"新加的措辞"一个**准入条件**（必须带来五类增量之一）。
4. **`effort_routing`** —— **反对"少写"这个诱人答案**。它不说"少用修辞"，而说"把预算花在正确的地方"。这才是防"什么都不敢写"的关键。
5. **`priority` 是一条全序**（7 级，`RHETORICAL_POLISH` 排最后）。比"分三级 + 每级自我声明"更省 token，且不会等级混乱。

### 3.5 八股可数化：5 条正则 + 上一轮实测回喂

`jxStyleMetrics(text)` 是纯正则、零模型调用：

| 指标 | 正则骨架 | 对应病灶 |
|---|---|---|
| `corrective_per_k` | `不是…(而是\|只是\|是)` / `没有…(只是\|而是)` | 先否定后肯定的释义腔 |
| `quantified_time_per_k` | `(几\|半\|一…十\|十几\|几十\|\d+)\s*(秒钟?\|分钟)` | **用抽象刻度代替具体现象** |
| `metaphor_per_k` | `仿佛\|好像\|如同\|宛如\|像是\|就像` | 明喻当解释外壳 |
| `authorial_explain_per_k` | `这意味着\|也就是说\|真正…的是\|归根结底\|准确地说` | 说明文 |
| `emdash_per_k` | `——\|――` | 破折号 |
| `dialogue_ratio` | 引号内字符数 / 总字数 | 对白密度 |

然后 **闭环**（`jxStyleCalibration`）：拿**上一轮自己的正文**量这组数，与 profile 的 `lint_targets` 区间比，生成：

```
【DISTRIBUTED_LINT_CALIBRATION】
corrective_per_k=HIGH|value=3.4|target=0..2|adjust=LOWER
【DISTRIBUTED_LINT_CALIBRATION_END】
```

🔑 **这是全文最可移植的一块**：把"八股"从形容词变成**可数量 + 有区间的目标 + LOWER/RAISE 指令**，而且成本是零（一条正则 + 一次比对）。它同时满足两条纪律：**测量用代码（确定性强），只把结论用一行话喂回去（不占注意力）**。

注意它**没有**退回到"逐句检查清单"——`lint_scope` 明写 `NO_PER_SENTENCE_STYLE_CHECKLIST`。**全篇分布可量，逐句不许量**（逐句 = 逐段切语域 = 把句子带碎）。

### 3.6 单一表达清理出口（纪律，不是机制）

版本台账里反复出现：

```
existing_debagu_module=UNCHANGED_SOLE_EXPRESSION_CLEANUP
No extra global anti-bagu doctrine is added here: the existing JX semantic-density/去八股
module remains the sole dedicated expression-cleanup module.
```

🔑 前后跨了十几个版本，**表达清理永远只有一个模块**。新增别的层（历史防火墙、语料引力、文风绑定）时都明确写"我不新增反八股教条"。这是对"禁令条数膨胀 → 模型什么都不敢写"的工程化防御。

### 3.7 语料引力：对比式 priming（目标域 + 排除域）

`REFERENCE-ATTRACTOR/2.0-CONTRASTIVE`，默认关：

```
authority=HOW_ONLY｜story_authority=NONE｜character_authority=NONE｜player_agency=NONE
target_domain=经典文学中的关系叙事语言分布
suppress_domain=<可选，负向>
执行：在内部先区分目标语料域与排除语料域的高层语言特征，只把差异投射到当前正文的
      词汇、句法、节奏、意象组织、情绪密度和对白温度；不要输出这一步分析。
transfer=LANGUAGE_DISTRIBUTION_ONLY｜LEXICAL_ASSOCIATION|SYNTAX|RHYTHM|IMAGERY_SELECTION|
         EMOTIONAL_COMPRESSION|DIALOGUE_TEMPERATURE
do_not_transfer=PLOT|CHARACTERS|RELATIONSHIP_FACTS|SPECIFIC_LINES|QUOTES|SONG_STRUCTURE
```

三个预设带 `cues`（高层分布提示词，不是原文），例如：

| 预设 | cues |
|---|---|
| 文学经典 | 具体动作与物件承载心理｜社会位置进入语气｜克制解释｜叙述留白｜避免模板化情绪总结 |
| 影视剧本 | 口语节奏｜潜台词｜停顿与回避｜动作承载信息｜场景中的关系位置｜少解释多现场 |
| 港台金曲 | 都市日常意象｜情绪压缩｜口语与诗性并存｜含蓄关系表达｜节律回环｜具体物承载情绪 |

🔑 **"语料域 + 高层提示词"是"贴范文"的低成本替代**：绕开版权与复制问题，还能带一个**负向排除域**。它甚至自己声明局限：`source_provenance=NOT_VERIFIABLE_FROM_MODEL_PARAMETERS` / "模型无法证明某句话来自哪条训练语料，因此排除域是负向 steering，不是数据来源过滤器"。

### 3.8 玩家输入：四轴标签 + 代理权矩阵（`JX47`）

输入不是一整块文字，是**带型别的槽位**。每个槽位挂 4 个正交标签：

`authority`（HARD_/SOFT_/CONTEXT_/OPEN_SPACE）× `epistemic`（谁知道）× `temporal`（何时）× `realization`（怎么落地）

以 `DM` 流派为例：

| 槽位 | authority | epistemic | realization |
|---|---|---|---|
| 我现在做什么 | HARD_SEMANTIC | PLAYER_SELF_ACTION | **WORLD_OUTCOME_OPEN** |
| 我想说什么 | HARD_SURFACE | PLAYER_SELF_SPEECH | NO_REPLAY |
| 我想达到什么 | SOFT_TARGET | PLAYER_GOAL | OUTCOME_OPEN |
| 我接受的风险 | SOFT_PERMISSION | PLAYER_PERMISSION | MAY_USE_NOT_QUOTA |
| 我的行动边界 | HARD_BOUNDARY | PLAYER_AUTHORITY | DENY_OUTSIDE |

`CHAT` 流派里最值钱的一格：

| 槽位 | authority | epistemic | realization |
|---|---|---|---|
| 想说什么 | HARD_SURFACE | **NPC_HEARD_EXACT** | NO_REPLAY_REWRITE_PROXY |
| 我的语气／用意／潜台词（角色不知道） | MODEL_ONLY | **NPC_UNKNOWN** | NO_DIRECT_NPC_KNOWLEDGE |
| 我预测角色会怎样（不锁定结果） | SOFT_OPTION | USER_BELIEF | NOT_FACT_NOT_NPC_DECISION |

再配一张**代理权矩阵** `JX47_AGENCY_MATRIX`，按流派 × 8 个通道（speech/action/inner/decision/goal/relationship/timeskip/plot）声明谁能定：

```
DM:   speech/action/inner/decision/goal/relationship = LOCKED_PLAYER
      timeskip = REQUIRES_PLAYER_OR_ESTABLISHED_CAUSE
      plot     = WORLD_RESPONDS_NOT_PLAYER_PROXY
CHAT: relationship = NPC_MAY_MOVE_OWN_POSITION_ONLY
      plot         = NPC_RESPONSE_ONLY
```

🔑 这是"玩家输入是否必须完全执行 / 编剧有没有否决权"的**矩阵化答案**：不是一刀切"输入即事实"，而是**逐通道声明**。`WORLD_OUTCOME_OPEN` 和 `NOT_FACT_NOT_NPC_DECISION` 是关键发明——**"我做了什么"是硬事实，但"结果如何"默认开放**。

### 3.9 此刻的确定性派生 + 玩家锁（`JX103`，零调用）

从模型自己输出的 `<Status_block>` 里**正则**推出 11 个字段（时间/地点/天气/在场/正在发生/身份/关系/近期余波/知识边界/现实约束/未完成），渲染成一个**可手改、每格可锁 🔒** 的面板，只往请求里塞**一行软事实**：

```
本轮开始时的当前事实参考（不是剧情边界；可按当前Flow与玩家输入自然变化）：
时间=…；地点=…；在场=…；…
meta: authority=CONTEXT_SUPPORT｜epistemic=CURRENT_SCENE_CACHE｜temporal=TURN_START
      realization=REFERENCE_ONLY_NO_SCENE_FREEZE   ← 不冻结场景
```

其中"在场"是算出来的：`离开|不在场|外出|异地|远程` → 否；地点关键词（寝室/教室/医院…）命中 → 是。"知识边界"是扫 `行动/内心` 分句里有没有 `知道|得知|听到|看见|目睹|发现|察觉|确认|意识到|记得`。

🔑 **玩家锁 + 不冻结 + 零调用**，是一条完整的"此刻"实现路径，正对本项目 `方案-位置面板` 的空白。

### 3.10 知域防火墙（`JX100`）

```
author_context_rule=模型可见的角色卡/世界书/Status/数据库召回/历史旁白/他人内心/隐藏设定
                    属于 AUTHOR_CONTEXT；AUTHOR_CONTEXT 不自动等于任意角色的 CHARACTER_KNOWLEDGE。
access_gate=角色使用一个非公开具体事实前，必须存在可追溯访问路径：
            DIRECT亲历｜OBSERVED亲眼亲耳｜TOLD被明确告知｜INFERABLE由其已知事实合理推出。无路径则 UNKNOWN。
inferable_boundary=INFERABLE 只允许推出证据真正支持的粒度；猜疑保持猜疑，不升级成 DIRECT。
scene_disclosure=当众披露后，实际在场且能听到者从该披露点起获得 TOLD。
failure_mode=路径不足时优先表现为不知道/询问/试探/误解/模糊怀疑/等待别人说明；
             禁止用准确引用来填补信息缺口。
reasoning_cost=FAST_GATE_ONLY｜不要为每个角色生成可见知识清单。
```

🔑 两点比"设定边界"高明：① **四种访问路径**是可判定的（不是"应该不知道"）；② **`failure_mode` 给了"不知道"一个可演的样子**，所以边界不会变成硬拒。

---

## §4 它试过并**推翻**的（负面清单，同样值钱）

| 被撤销的东西 | 台账证据 | 教训 |
|---|---|---|
| **`restrained tell` / 白描偏置 / 少修辞偏置** | `JX54`：`restrainedTell:false, whiteSketchBias:false, showTellDirective:false`；"54撤销52加入的…" 且"**不把『少写』作为默认答案**" | 治八股的直觉答案（"少修辞/白描"）是**错的**——会把文字压平 |
| **句式黑名单 + 逐句风格清单** | `NO_PHRASE_BLACKLIST｜NO_PER_SENTENCE_STYLE_CHECKLIST` | 逐句审计 = 逐段切语域 → 把句子带碎 |
| **常驻也要求有触发词**（反向） | `no_completion_quota` | 给"完成的错觉"设配额 = 逼出凑数 |
| **所有非空槽位硬锁** | `all_nonempty_slots_hard_lock_retired: true` | 输入槽位全硬锁会僵死 |
| **英文语义中转** | `english_semantic_pivot: REMOVED_AFTER_FAILED_AB` | 试过、A/B 失败、撤掉 |
| **把历史当文风老师** | `history_generic_narrative_style_authority=CONDITIONAL_REFERENCE` + `JX76` | 历史会传染八股 |
| **给文学质量打分** | `DO_NOT_SCORE_SUBJECTIVE_LITERARY_QUALITY` | 只隔离客观异常（长度塌陷/机器泄漏），别做主观评分 |

⚠️ 台账里还有大量 `NOT_IMPORTED`（从外部版本**明确拒绝引入**的层，如 `WORLD_CHARACTER_SETTINGS`、`CREATIVE_NARRATIVE_RULES`、`ECHO_BEACON`）。**它把"不引入什么"也写进台账**——这是它跨 50 多个版本没烂掉的原因之一。

---

## §5 可吸收清单（按落点与成本排）

| # | 可吸收项 | 落点 | 成本 | 依赖 |
|---|---|---|---|---|
| 1 | **填 `style_sample`**：2 段玩家认可的范文，标 `surface_example_only` | **AIWorld** `data/presets.json` | 极低（只改数据） | 无 |
| 2 | **历史写法切分**：7 句自然语言（"记住经历，不复制写法"）进 writer 三级块 | **AIWorld** 预设 | 极低 | 无 |
| 3 | **撤销"文学完成配额"**：`already_carried` + `no_completion_quota` + `semantic_delta` + `effort_routing` 四句 | **AIWorld** 预设 | 低 | 无 |
| 4 | **八股正则化**：5 条指标 + 每千字区间（含本项目特有的"程度刻度""实物化比喻"两条） | **AIWorld**：`writer.py` 后处理 + 预设回喂 | 中 | 需要一条"上一轮正文"可取 |
| 5 | **单一表达清理出口**纪律：现有 457 字里已有 3 条重叠禁令 ⇒ 合并不新增 | AIWorld 预设 | 低 | 无 |
| 6 | **属性化玩家输入槽位**（四轴标签 + 结果开放） | **aiworld-alive**（新特性） | 中大 | 需改输入模型 |
| 7 | **代理权矩阵**（流派 × 通道） | **aiworld-alive** | 中 | 需先有"流派/模式"概念 |
| 8 | **此刻的确定性派生 + 字段锁** | AIWorld 有 `方案-位置面板` 空白，但属新特性 ⇒ **aiworld-alive** | 中 | 需模型输出结构化的状态块 |
| 9 | **知域四路径门**（DIRECT/OBSERVED/TOLD/INFERABLE + 失败模式） | **aiworld-alive** | 中 | 需先有"知识"表示 |
| 10 | 语料引力（对比式 priming） | 预设层即可试（不改代码） | 低 | 无 |
| 11 | 文风参数化（把散文要求改成参数 + 区间） | AIWorld 边际改良 / aiworld-alive 正解 | 中 | 需要 lint 指标先行 |

> ⚠️ 本项目已冻结（2026-09-24）：新特性一律去 `C:\AI\aiworld-alive\`。上表 1–5、10 是**改数据/改预设**（不动引擎），可视为"调参"而非"加特性"；6–9、11 属新特性，走新项目。

---

## §6 与 AIWorld 现状对账

现状（实测 `data/presets.json`）：`writer_guidelines` **457 字** · `banned_words` **6 条** · `style_sample` **0 字（空）**。

| AIWorld 现有写法 | 剑仙105 的对应物 | 差距 |
|---|---|---|
| `禁止先否定后肯定或先肯定后否定的转折句` | `corrective_per_k` 正则 + 区间 `[0,2]` + 回喂 | 只有禁令，**没有测量**，模型不知道自己犯没犯 |
| `禁止用破折号` | `emdash_per_k` 正则 + `[0,3]` | 同上 |
| `不要对人物的语言、语气和行为进行比喻、评价。不要把语言当成或比作实物` | `metaphor_per_k` 正则（`仿佛\|好像\|如同\|宛如\|像是\|就像`） | 这条**最抽象**，正是"抓不住"的典型；需要落到可数字面锚 |
| `不描写喉结、骨骼、血管` + `banned_words` 6 条 | 🔴 **它删掉了词汇黑名单** | 词汇黑名单是**无条件局部改写**，会误伤合法句；它改成了"分布压制" |
| `过短的描述句子要合并成为长句` / `五字以内对白必须删掉` | `dialogue_ratio` 区间 + 微范文 | 只给禁令，没给"长句长什么样"的样本 |
| `***忘掉前文文风，必须深度复刻《斗破苍穹》***` | 37 条文风世界书（profile + variant + **exemplar**）+ 延迟绑定 | 🔴 **最弱的一格**：给了一个**书名**，既不是分布参数也不是范文 |
| （无） | `HISTORY-STYLE-FIREWALL` + `JX76` 历史写法复位 | 缺——历史正文正被当范本抄，八股由此传染 |
| （无） | `no_completion_quota` / `effort_routing` | 缺——只禁"别这样"，没说"预算该花哪" |
| （无） | `style_content_duty=NONE` | 缺——文风没被禁止产生"内容义务" |
| （无） | 单一表达清理出口纪律 | 缺——现有 457 字里"不要比喻/不要评价/不要实物化"已重叠 |

**结论**：AIWorld 当前预设 **100% 落在低杠杆格**（全是文字规则与禁令），且**最高杠杆那格是空的**（`style_sample` 0 字）。按 skill 的判据——"只投规则不放范文 = 只下了禁令，模型只能回落默认档"——**"八股反复回来"就有一个结构性解释：没给过一条"好"的样本，只给了一堆"不许"**。

---

## §7 怎么验证（别只换措辞就宣布成功）

1. **先量再改**：拿现有存档的真实候选正文，跑 §3.5 的 5 条正则，记录基线（每千字密度 + 对白均字长 + ≤4字句占比）。
2. **一次只动一格**，同存档同输入同模型 A/B：`无范文 → 加范文` / `加历史切分` / `加配额撤销`，分开测。
3. **判据**：`semantic_delta` 类指标（八股密度）下降 **且** 总字数/句长不掉——只降八股但文字变平 = 又踩了 `JX54` 撤销过的坑。
4. **注意它自己的警告**：一条规则吃两遍（写手三级块 + 质检比对基准）⇒ 改预设会**同时**改变写手与质检的行为，A/B 时别只盯写手。
5. 改完 `data/presets.json` **必须重启服务**（加载点在 startup，`reload=False`）。

---

## §8 一句话总结

剑仙105 的效果来自五件**不靠提示词措辞**的事：
1. **范文独立成资产**（37 条世界书，含 exemplar）并**延迟绑定**到近因区；
2. **历史降级为事实源**（`SINGLE_TURN_NONE` + "记住经历，不复制写法"）；
3. **撤销"文学完成配额"**，并把推理预算**路由**到真正模糊处；
4. **八股可数化**（5 条正则 + 区间 + 上一轮回喂）；
5. **单一表达清理出口** + 明确**撤销"少写/白描"**这个诱人答案。

全部测量、选择、门控都是**确定性代码**，`extra_model_calls: 0`。
