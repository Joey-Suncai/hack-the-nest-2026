# ShowMe — Demo 计划

> 负责人：Teresa（录制、剪辑、提交），Joey（技术部分讲解、保证 demo 任务稳定）
> 相关：开发计划 `docs/superpowers/plans/2026-11-14-showme.md`（A6 / C9 任务对应这里的准备工作）

## 0. 核心信息（所有材料都围绕这一句）
> **"Teaches you, doesn't do it for you."** 第一次用任何软件，ShowMe 看着你的屏幕，把下一步要点的按钮直接框亮。

评委看完要记住三件事：
1. **框亮 + 语音**：不是一段文字说明，而是直接在屏幕上指给你看
2. **会检查你做对了没有**：点错了能纠正（这是和「截图问 AI」最大的区别）
3. **任何软件都能用**：不需要软件厂商做任何配置

---

## 1. Demo 任务（只用这两个，提前反复测试）

| | 任务 | 软件 | 步骤数 | 为什么选它 |
|---|---|---|---|---|
| **主任务** | 把一列数据做成柱状图 | Numbers | 4–5 步 | 和数据科学背景呼应；步骤有菜单、有弹窗，能体现「一步步」 |
| **副任务** | 打开深色模式 | 系统设置 | 2–3 步 | 每台 Mac 都有，最稳；证明「换个软件也行」 |
| 备选（只在评委主动要求时用） | 添加一页带标题的幻灯片 | Keynote | 3 步 | 提前测过的第三个任务 |

**主任务的标准步骤**（A6 调 prompt、C9 彩排都按这个来）：
1. 选中数据区域 A1:B6（如果演示数据已经选好，就跳过）
2. 点击上方工具栏的「图表」按钮
3. 在弹出菜单里选「二维」
4. 点击第一个柱状图
5. 完成 → ShowMe 说「完成！你的柱状图已经做好了」

**演示数据**：提前做好一份 `pitch/demo-data.numbers`，例如「2020–2025 年 EMU 黑客松参赛人数」这类一眼能看懂的数据（6 行 2 列）。

---

## 2. Demo 视频分镜（目标 2:30，最长不超过 3:00）

| 时间 | 画面 | 英文旁白（录音用） | 说明 |
|---|---|---|---|
| **0:00–0:20** 痛点 | 截图丢给聊天机器人 → 它回一大段文字「Click Insert, then Chart, then...」→ 鼠标在屏幕上来回找 | "The first time you open new software, the hardest part isn't the task — it's not knowing where to click next. Screenshots, tutorials, chatbots: they all *tell* you. None of them *show* you." | 用快剪，节奏要快 |
| **0:20–0:30** 引出 | ShowMe 面板出现在屏幕右上角 | "This is ShowMe. Tell it what you want to do, and it watches your screen and points at the next step." | |
| **0:30–1:20** 主 demo | Numbers：在面板输入 "Turn this data into a bar chart" → 点 Start → 「图表」按钮发光 + 听到语音 → 点击 → 框跳到「二维」→ 点击 → 框跳到柱状图 → 点击 → 「Done!」 | （这一段**少说话**，让 ShowMe 的 ElevenLabs 语音自己讲）最后补一句："Four steps. I'd never used Numbers before." | **「哇」的瞬间**。Gemini 等待时间可以加速，但不要剪掉 |
| **1:20–1:40** 纠错 | 故意点错一个按钮 → ShowMe：「Looks like that opened the wrong menu — close it first」→ 框回到正确位置 | "It doesn't just point. After every click, it checks whether you actually got there — and corrects you if you didn't." | **只有稳定才放**；不稳定就删掉这一段，其他时间往前挪 |
| **1:40–2:00** 任何软件 | 系统设置 → 深色模式，1.5 倍速播放 | "No setup, no integration from the app maker. Any app on your Mac." | |
| **2:00–2:25** 原理 | 一张架构图（见第 5 节） | "Under the hood: Gemini's spatial understanding finds the exact element on screen. We snap the box to on-screen text with macOS OCR, detect when your screen changes, and ask Gemini to verify each step. ElevenLabs reads every instruction out loud." | Joey 录这一段 |
| **2:25–2:30** 收尾 | ShowMe 标志 + 一句话 | "ShowMe. Teaches you — doesn't do it for you." | |

### 录制要求
- **工具**：Cmd+Shift+5 录整个屏幕（或者 QuickTime），1080p 以上；旁白另外录（安静的地方，用耳机麦克风），剪辑时合成
- **高亮层必须录得进去**：客户端**不要**给高亮层设置「不被截屏」（`NSWindowSharingNone`）。计划里只在截图那一下隐藏高亮层，录屏能正常录到
- **截图时高亮层闪一下**：录出来能看到；如果太明显就剪掉那几帧
- **加速要诚实**：Gemini 思考的 2–3 秒可以 2 倍速，画面角落标「2×」。评委问「是实时的吗？」就说「是，视频里为了节奏加速了」
- **录之前清理屏幕**：关掉通知（勿扰模式）、隐藏桌面图标、用干净的 Dock、字体调大一点方便看
- **ShowMe 的语音用英文**（`lang = en`），评委听得懂；路演时可以提一句「也支持中文」，并现场切换演示一步
- **上传**：YouTube 设为「不公开」（Unlisted），链接填进 Devpost

---

## 3. 现场演示（3 分钟，评委来桌前时用）

| 时间 | 谁 | 做什么 |
|---|---|---|
| 0:00–0:30 | Teresa | 讲痛点（同视频 0:00–0:20 的旁白），可以问评委「你上次第一次用一个软件是什么时候？」 |
| 0:30–1:45 | Teresa 操作 | **实时**跑 Numbers 主任务（数据文件提前打开好） |
| 1:45–2:15 | Teresa 操作 | 实时跑系统设置深色模式；时间够就切中文语音演示一步 |
| 2:15–3:00 | Joey | 讲原理（指着电脑上的架构图）+ 回答问题 |

**规则：**
- **不让评委随便挑软件**。评委主动要求时，说「我们来试一个」，用备选的 Keynote 任务
- 每轮演示前：系统设置恢复浅色模式、Numbers 数据恢复原样（准备一个「重置」清单，30 秒搞定）
- **卡住的应急预案**（按顺序）：
  1. Gemini 慢：说「它在看屏幕」，等 5 秒
  2. 框错了：点「重试」
  3. 还是不行：切到 `make mock`，或者直接用另一台设备播放 demo 视频
- 演示用的电脑：电源插好、关掉通知、提前开好「屏幕录制」权限、`make backend` 和 `make client` 已经在跑

### 准备好回答的问题（Joey）
| 问题 | 回答要点 |
|---|---|
| 和直接截图问 ChatGPT / Gemini 有什么区别？ | ① 直接在屏幕上框出来，不用你自己找 ② 每一步都检查你做对了没有 ③ 不用一次次截图、粘贴 |
| 和 computer-use agent（AI 替你操作）有什么区别？ | 我们教你做，你自己掌握控制权；付款、删除这类操作更安全，你也真的学会了 |
| 和 WalkMe / Whatfix 这类产品引导工具有什么区别？ | 它们要软件厂商一个个手动配置；我们任何软件都能用，现场实时生成 |
| 框得准吗？ | Gemini 空间定位 + 两步放大 + 本地 OCR 吸附；说出我们评测集上的命中率（A2 / A6 的数据） |
| 隐私？ | 只在任务进行中截图；截图只发到我们自己的后端，再到 Gemini；不存储 |
| 下一步做什么？ | Windows 版；手机版（帮爸妈远程学用手机）；记住学过的流程，下次直接用 |

---

## 4. Devpost 要准备的材料

| 材料 | 负责人 | 截止 |
|---|---|---|
| demo 视频（YouTube 不公开链接） | Teresa | 09:30 |
| 3–5 张截图：面板、高亮框、完成画面、架构图 | Teresa | 09:30 |
| 项目描述（Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned / What's next） | Teresa 写，Joey 补技术部分 | 10:00 草稿 |
| 「Built with」标签：gemini-api, elevenlabs, python, pyqt6, fastapi, macos-vision | Joey | 10:00 |
| GitHub 链接 + 干净的 README | Joey | 10:00 |
| 赛道：AI/ML、Best use of ElevenLabs、Computer Science Department；勾 MLH Best Use of Gemini API | Teresa | 提交时 |

---

## 5. 架构图（Joey 用 Keynote 或 Excalidraw 画一张）

```
  ┌──────────── Mac 客户端 ────────────┐        ┌────── 后端 (FastAPI) ──────┐
  │ 浮动面板：软件 + 目标               │        │                            │
  │ 截屏（先隐藏高亮层）  ───────────────┼──────▶ │ /api/next-step             │
  │                                    │        │   Gemini：找到下一步元素框   │
  │ OCR 吸附（macOS Vision）◀───────────┼─────── │   + 判断上一步是否完成       │
  │ 透明高亮层：发光框 + 提示            │        │                            │
  │ 画面变化检测 → 下一步               │        │ /api/speak → ElevenLabs    │
  │ 播放语音    ◀───────────────────────┼─────── │                            │
  └────────────────────────────────────┘        └────────────────────────────┘
```

---

## 6. 时间表（周日）

| 时间 | 事项 | 谁 |
|---|---|---|
| 06:30–07:30 | 彩排：两个任务各跑 3 次；记录失败的步骤交给 Joey 修 | 两人 |
| **08:00** | **功能冻结** | |
| 08:00–08:45 | 录屏（每段录 2–3 遍，挑最好的） | Teresa 操作，Joey 盯着屏幕检查 |
| 08:00–08:45 | 画架构图、写 README 技术部分 | Joey |
| 08:45–09:15 | 录旁白 + 剪辑 | Teresa（原理那段 Joey 录） |
| 09:15–09:30 | 上传 YouTube，截图 | Teresa |
| **10:00 前** | **交 Devpost 草稿** | Teresa |
| 10:00–11:15 | 两人各自通读一遍描述、看一遍视频、修改；彩排现场演示 | 两人 |
| **11:30 前** | **交最终版**（留 30 分钟余量） | Teresa |
