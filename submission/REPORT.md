# OpenAvatarChat 任务报告

**项目**：OpenAvatarChat（LiteAvatar 配置 `config/chat_with_openai_compatible_bailian_cosyvoice.yaml`）
**任务**：核实并解决三个 VAD / 打断相关问题
**基线**：upstream `c9d823c`（前端子模块 upstream `a6182af`，对应 v0.6.0）
**状态**：核心功能实测全部通过，未 push 到远程（按要求等你自己决定）

---

## 一、三个原始问题的现状核实结论

核实结论基于阅读 upstream 代码与配置（read-verified），并在实测中确认行为。

### 问题 1：语音截断（说话开头/结尾被切掉）—— upstream 已有修复机制

当前 upstream 已通过两层机制缓解：

- **配置层（早于本任务时间）**：ASR/VAD 配置里的 `end_delay` 与 `buffer_look_back`（=5000ms）保留句尾静音缓冲并回看句首音频，避免端点检测把头尾切掉。这一机制自 **v0.3.0** 起即存在 —— 诚实说明：**这部分修复早于本任务发布的时间**，不是为本任务新增的。
- **v0.6.0 新增**：`POST_END` 阶段的重连逻辑，进一步降低端点抖动导致的丢字。

**结论**：问题 1 在当前 upstream 上已被机制化处理，无需我再改。

### 问题 2：噪音误触发 —— upstream 已有机制，但实现方式需如实说明

upstream（v0.6.0，commit `475e71f`）的应对是 **`volume_threshold`（音量/能量门限）+ AGC（自动增益）+ energy gate（能量门控）** 的组合。

**需要如实指出**：这套方案走的是「**能量门限 + 自动增益**」路线，**而不是**单纯「提高 VAD 阈值」或「加降噪模型」。也就是说它通过能量门控滤掉低能量噪声、用 AGC 归一化输入电平，而非靠更激进的阈值或独立降噪算法。这是一个工程取舍，效果在安静/一般噪音环境下可用，但不等于专门的降噪。

**结论**：问题 2 在当前 upstream 上已有缓解机制，无需我再改；但其本质是能量门控而非降噪，汇报时不应含糊。

### 问题 3：语音打断（说话打断机器人）—— 真实缺口

这是三个问题里**唯一真正的缺口**：

- upstream **只有一个手动「打断」按钮**，且该按钮在语音模式下并不显示；
- 服务端 VAD 是**单工（simplex）**的——机器人播放期间，服务端麦克风输入被静音，因此**无法靠服务端检测到用户插话**。

也就是说，upstream 根本没有「用户开口说话即打断机器人」的能力。这正是本任务需要补的功能。

---

## 二、我实际做的工作

### 1. 真正的语音打断（barge-in）

在**前端**实现「用户开口即打断」，复用后端已有的取消链，不新增后端信号：

- **前端麦克风 VAD**（`bargeInDetector.ts`）：基于能量的检测（RMS → dBFS），带
  - 最小持续时长（min-duration，避免瞬时杂音触发）；
  - 起停迟滞（start/stop hysteresis）；
  - 仅在机器人正在说话时「武装」（armed-only-while-replying）；
  - 触发后冷却期（cooldown，避免连续误触）。
- **复用后端取消机制**：检测到插话即发送已有的 `Interrupt` data-channel 消息 → 后端 `INTERRUPT` 信号 → `interrupt_handler` 取消 `CLIENT_PLAYBACK`。无需改后端打断逻辑。
- **防自我打断（关键）**：通过 `getUserMedia` 请求 `echoCancellation` / `noiseSuppression` / `autoGainControl`，使机器人自己的播放声不被麦克风采到而误判为「用户插话」。

### 2. 个性化（根据参与者基本信息定制）

把参与者信息注入 LLM 的 system prompt，分两条路线（共用同一套合并逻辑）：

- **Option B（config 驱动，后端）**：`participant_info.py` 提供 `build_personalized_system_prompt()`，纯标准库实现。
- **Option A（运行时 per-session）**：前端表单 → data-channel 消息 `SetParticipantInfo` → 后端存入会话 `shared_states` → LLM handler 每轮按需重新合并（运行时覆盖 config）。

**防注入处理**（重要，体现在合并逻辑里）：
- **追加而非覆盖**：参与者信息以清晰分隔的区块**追加**到 base prompt，不覆盖原有指令；
- **空字段省略**：没填的字段不进 prompt；
- **把值当数据而非指令**：中和区块标记符、折叠换行、限制长度，抵抗用户在字段里塞入 prompt-injection；
- **回归安全**：无任何字段时原样返回 base prompt，不影响默认行为。

---

## 三、实测中发现并修复的两个真实 Bug

这两个都是在联调/实测阶段暴露出来的真实问题，不是纸面分析。

### Bug 1：个性化消息撞上后端 0.5s 开场丢弃窗口（时序问题）

- **现象**：填了参与者信息，但机器人完全没体现出来。
- **定位**：前端在 data-channel `open` 事件里**立刻**发送 `SetParticipantInfo`，而后端 `rtc_stream` 有一个约 **0.5s 的 stream start-delay 丢弃窗口**（`stream_start_delay`），会丢掉这段时间内收到的消息。于是个性化消息在到达 handler 之前就被丢了，从未生效。
- **修复（双保险）**：
  - **Fix A（前端，`6d9f0cb`）**：把发送推迟 1.5s，确保越过该丢弃窗口；
  - **Fix B（后端，`0f9737d`）**：把控制类消息（`SetParticipantInfo`）从丢弃逻辑里**豁免**，作为后端兜底；其他消息保持原行为。

### Bug 2：打断时在途音视频队列未清空，导致 1–2s 拖尾

- **现象**：语音打断后，机器人的回复音视频还会继续播约 1–2 秒才停，不够「干净」。
- **定位**：打断时上游（avatar worker）的队列确实被清空了，**但已经交给 RTC delegate 的 `output_queues` 的那些帧仍在继续向客户端排放**，所以在途的这段回复继续播完。
- **修复（`8c50170`）**：在 `STREAM_CANCEL`（取消 `CLIENT_PLAYBACK`）时，调用新增的 `flush_output()` 清空 delegate 的 **AUDIO + VIDEO** `output_queues`。因为 `on_signal` 可能跑在别的线程，而排放循环（emit loop）拥有这些队列，所以用 `call_soon_threadsafe` 把清空动作调度到 emit loop 上执行，线程安全。日志会打印 `RtcClient: flushed N buffered ... frames on interrupt`。

---

## 四、实测结果

单机环境（RTX 4090 Laptop）实测，核心功能全部通过：

**语音打断（A1–A4）：**
- A1 能打断：用户开口说话能打断正在说话的机器人 ✅
- A2 立刻干净停、无拖尾：打断后音视频立即停止（Bug 2 修复后无 1–2s 残留）✅
- A3 安静时不自我打断：机器人说话、用户不出声时不会自己打断自己 ✅
- A4 噪音不误触发 / 手动按钮仍正常：一般噪音下不误触，原有手动打断按钮功能未受影响 ✅

**个性化：** 机器人能正确认出并体现参与者信息 ✅

---

## 五、已知小问题与局限

- ~~**UI 小缺陷**：个性化表单展开时「开始对话」按钮被挤出可视区域~~ —— **已修复**（commit `f772415`）：开播前布局改为 flex 纵向、视频自适应收缩，表单/视频/按钮都在可视区内；通话中布局不受影响，已实测刷新确认按钮回归。
- **测试范围有限**：单机环境、单次手动测试，未做多机/多浏览器/弱网/压力测试，也未做长时间稳定性观察。
- **问题 2 的本质**：如上所述是能量门控 + AGC，而非专门降噪；强噪声环境下的鲁棒性未专门验证。
- **前端构建产物未提交**：子模块 `dist/` 下有重新构建的产物处于未提交状态（与本次功能代码无关，属构建输出）。
- **upstream 既有类型错误未修**：前端独立 `pnpm typecheck` 有 26 个**既存于 upstream**、分布在 17 个未改动文件中的错误，属任务范围之外，未修（避免破坏提交隔离）；我改动的文件本身类型检查干净，`pnpm build` 通过。
- **未 push**：所有改动仅在本地分支，未推送远程。

---

## 附录：本次所有改动清单（分支 + commit）

> 基线：父仓库 upstream `c9d823c`；前端子模块 upstream `a6182af`（v0.6.0）。以下均为在基线之上新增的提交，均未 push。

### 父仓库（OpenAvatarChat）— 分支 `feature/participant-personalization`

| commit | 说明 |
|--------|------|
| `8c50170` | fix(barge-in)：打断时清空 RTC delegate 的 AUDIO+VIDEO 在途发送队列，消除 1–2s 拖尾（Bug 2 修复） |
| `0f9737d` | fix(personalization)：后端把控制消息（SetParticipantInfo）从 0.5s 开场丢弃窗口中豁免（Bug 1 的后端兜底，Fix B） |
| `6d4ea50` | feat(llm)：运行时 per-session 个性化——data-channel 收 SetParticipantInfo 存入 shared_states，LLM 每轮按需重合并（Option A 后端） |
| `6db7e1b` | feat(llm)：config 驱动的 system prompt 个性化合并逻辑 `participant_info.py`，含防注入处理（Option B） |

涉及文件（共 7 个，+448/−7）：
`src/handlers/client/rtc_client/client_handler_rtc.py`、`src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`、`src/handlers/llm/openai_compatible/participant_info.py`、`src/chat_engine/contexts/session_context.py`、`src/service/rtc_service/rtc_stream.py`、`config/chat_with_openai_compatible_bailian_cosyvoice_personalized.yaml`、`scripts/verify_personalization.py`

### 前端子模块（src/service/frontend_service/frontend）— 分支 `feature/participant-personalization-ui`

> 该分支叠在 barge-in 分支之上（共享 app.ts / webrtc.ts）。另有独立分支 `feature/voice-barge-in`（含 `7755a97`）。

| commit | 说明 |
|--------|------|
| `f772415` | fix(ui)：开播前改 flex 纵向布局，个性化表单展开时「开始对话」按钮仍可见（已知局限项的修复） |
| `6d9f0cb` | fix(personalization)：把 SetParticipantInfo 推迟 1.5s 发送以越过后端开场丢弃窗口（Bug 1 修复，Fix A） |
| `db1cadc` | feat(personalization)：参与者信息表单 + per-session 传输（Option A 前端） |
| `7755a97` | feat(barge-in)：前端麦克风 VAD 实现语音打断，复用后端取消链，含回声消除/最小时长/迟滞/冷却防误触 |

涉及文件（共 9 个，+481/−1，不含 dist 构建产物）：
`src/renderer/src/helpers/bargeInDetector.ts`、`src/renderer/src/store/webrtc.ts`、`src/renderer/src/store/app.ts`、`src/renderer/src/store/media.ts`、`src/renderer/src/components/ParticipantInfoForm.vue`、`src/renderer/src/interface/participant.ts`、`src/renderer/src/interface/eventType.ts`、`src/renderer/src/views/VideoChat/index.vue`、`BARGE_IN.md`

### 设计文档

`Task_2/BARGEIN_DESIGN.md`（打断方案）、`Task_2/PERSONALIZATION_DESIGN.md`（个性化方案）、`Task_2/VERIFY.md`（验证清单）。
