# ShowMe — Demo 计划（v2：对话模式 + 实时语音）

> 负责人：Teresa（操作、录制、剪辑、提交），Joey（技术讲解、评委问答、保证后端稳定）
> 相关：接口 `docs/API.md`，评测 `backend/eval/README.md`
> v2 改了什么：主任务换成 VS Code；加入实时语音对话（能打断）、中途提问、「任何软件」环节；数字更新为最新评测结果

## 0. 核心信息（所有材料都围绕这一句）
> **"Teaches you, doesn't do it for you."** 第一次用任何软件，直接跟 ShowMe 说你想做什么，它看着你的屏幕，把下一步要点的按钮框亮，一边说一边教。

评委看完要记住四件事：
1. **框亮 + 实时对话**：像跟真人老师说话一样，随时可以提问、打断它
2. **会检查你做对了没有**：点错了能发现并纠正（这是和「截图问 AI」最大的区别）
3. **任何软件都能用**：没调过的 10 个软件命中率 90%，不需要软件厂商做任何配置
4. **快**：带文字的按钮点完 1 秒内出下一步（OCR 预测 + Gemini 后台核对）

---

## 1. Demo 任务

| | 任务 | 软件 | 用在哪 |
|---|---|---|---|
| **主任务** | 把 GitHub 上的 `octocat/Hello-World` clone 下来并打开 | VS Code | 视频 + 现场，全程英文 |
| **「任何软件」** | 评委从下面的「安全清单」里挑一个，演示 1–2 步 | 见下 | 视频 + 现场 |
| **纠错**（可选） | 主任务中故意点错一次 | VS Code | **只有稳定才放** |

**主任务的标准步骤**（和 `backend/eval/README.md` 的评测一致，评测 6/6）：
1. 在欢迎页点 **Clone Git Repository**
2. 点顶部的仓库地址输入框
3. 粘贴 `https://github.com/octocat/Hello-World`，选 **Clone from URL**
4. 选一个文件夹，点 **Select as Repository Destination**
5. 点右下角通知里的 **Open**（如果弹出「Do you trust the authors」，点 **Yes, I trust**）
6. 完成 → ShowMe：「Done! The repo is open in VS Code.」

**「任何软件」安全清单**（都在「没见过的软件」评测里测过，挑最稳的）：
- 访达：打开「下载」文件夹
- Safari：新建标签页
- 日历：切到月视图
- Keynote：添加一页幻灯片
- Chrome 里的 Gmail：写新邮件

**每轮演示前的重置清单（30 秒）**：
- 删掉 clone 下来的 `~/Hello-World`（或你选的目标文件夹里的那份）
- VS Code：关掉当前文件夹（File → Close Folder），回到欢迎页
- 访达回到「最近使用」，日历回到周视图，Keynote 只留一页
- 剪贴板里放好 `https://github.com/octocat/Hello-World`

---

## 2. Demo 视频分镜（目标 2:30，最长不超过 3:00）

| 时间 | 画面 | 英文旁白（录音用） | 说明 |
|---|---|---|---|
| **0:00–0:15** 痛点 | 截图丢给聊天机器人 → 它回一大段文字 → 鼠标在屏幕上来回找 | "The first time you open new software, the hardest part isn't the task — it's not knowing where to click. Chatbots *tell* you. None of them *show* you." | 快剪 |
| **0:15–0:25** 引出 | 打开 ShowMe，直接对它说："Hey ShowMe, help me clone this GitHub repo in VS Code." | "This is ShowMe. Just tell it what you want to do." | 用语音，不打字 |
| **0:25–1:10** 主 demo | ShowMe 回答并框亮 **Clone Git Repository** → 点击 → 1 秒内框跳到输入框 → 粘贴 → Clone from URL → 选文件夹 → Open → 「Done!」 | （这一段**少说话**，让 ShowMe 自己讲）最后补一句："I've never cloned a repo before. That took under a minute." | **「哇」的瞬间**。点完到出框的速度是亮点，**这里不要加速** |
| **1:10–1:30** 对话 + 打断 | ShowMe 正在说话时直接插话："Wait — what does Clone from URL mean?" → 它马上停下，看着屏幕回答 → 接着教 | "You can talk to it like a person — interrupt it, ask about anything on screen." | 展示实时语音和打断 |
| **1:30–1:40** 纠错（可选） | 故意点错 → 说 "Oops, I clicked the wrong thing" → 框回到正确位置 | "And it checks every step — if you go wrong, it gets you back on track." | **只有稳定才放**，不稳定就删，时间往前挪 |
| **1:40–1:55** 任何软件 | 快速切换：访达打开下载 → 日历切月视图，1.5 倍速 | "No setup, no integration. Any app on your Mac — 90% accuracy on apps we never tuned for." | |
| **1:55–2:20** 原理 | 架构图（第 5 节） | "Under the hood: Gemini's spatial understanding finds the exact element on screen. ElevenLabs Agents handle the real-time voice conversation. To keep it fast, we predict the next button and find it instantly with on-device OCR, while Gemini double-checks in the background. And voice fingerprinting means only *you* can interrupt it — not the people around you." | Joey 录这一段 |
| **2:20–2:30** 收尾 | ShowMe 标志 + 一句话 | "ShowMe. Teaches you — doesn't do it for you." | |

### 录制要求
- **先确认能录到系统声音**：ShowMe 的语音是从电脑放出来的。录之前先试录 10 秒，确认视频里听得到 ShowMe 的声音；系统自带的 Cmd+Shift+5 录不到就改用 OBS（能录 macOS 系统声音）
- **高亮层必须录得进去**：客户端**不要**给高亮层设置「不被截屏」（`NSWindowSharingNone`）
- **加速要诚实**：只在 Gemini 核对的空档加速，画面角落标「2×」。主 demo 里「点完到出框」那一段**不加速**，这是卖点。评委问「是实时的吗？」就说「是」
- **录之前清理屏幕**：勿扰模式、隐藏桌面图标、干净的 Dock、VS Code 字体调大
- **声纹**：在录制的地方先录一次声纹，环境和录制时一致
- **ShowMe 全程英文**（界面和声音都是 English），评委听得懂；现场可以提一句「也支持中文」
- **上传**：YouTube 设为「不公开」（Unlisted），链接填进 Devpost

---

## 3. 现场演示（3 分钟，评委来桌前时用）

| 时间 | 谁 | 做什么 |
|---|---|---|
| 0:00–0:25 | Teresa | 讲痛点，可以问评委「你上次第一次用一个软件是什么时候？」 |
| 0:25–1:35 | Teresa 操作 | 用语音实时跑 VS Code 主任务，中途插话问一个问题 |
| 1:35–2:10 | Teresa 操作 | 请评委从「安全清单」里挑一个软件，演示 1–2 步 |
| 2:10–3:00 | Joey | 讲原理（指着架构图）+ 回答问题，报评测数字 |

**规则：**
- 评委只从「安全清单」里挑。评委想试清单外的软件，可以试，但先说一句「这个我们没测过，我们试试看」
- 每轮演示前按第 1 节的重置清单恢复
- 演示用的电脑：电源插好、关掉通知、提前开好「屏幕录制」和「麦克风」权限、后端和客户端已经在跑、声纹已经在现场录好
- **现场很吵时**：直接打开面板上的「按住说话」，按住空格再说话，不要硬撑

**卡住的应急预案**（按顺序）：
1. Gemini 慢：说「它在看屏幕」，等 3 秒
2. 框错了：直接说 "That's not right"，让它重新看；还不行就点「重试」
3. 被杂音误打断：打开「按住说话」
4. 实时语音连不上：客户端会自动退回打字模式，直接打字继续
5. 还是不行：用另一台设备播放 demo 视频

### 准备好回答的问题（Joey）
| 问题 | 回答要点 |
|---|---|
| 和直接截图问 ChatGPT / Gemini 有什么区别？ | ① 直接在屏幕上框出来，不用自己找 ② 每一步都检查你做对了没有 ③ 实时语音对话，不用一次次截图、粘贴 |
| 和 computer-use agent（AI 替你操作）有什么区别？ | 我们教你做，你自己掌握控制权；付款、删除这类操作更安全，你也真的学会了 |
| 和 WalkMe / Whatfix 这类产品引导工具有什么区别？ | 它们要软件厂商一个个手动配置；我们任何软件都能用，现场实时生成 |
| 框得准吗？ | demo 任务 6/6；原来的评测集 91%；**10 个从没调过的软件 90%**（唯一失败是没看出任务已经完成，找按钮位置全对） |
| 怎么做到这么快？ | Gemini 每步约 2.2 秒（调低思考深度后从 4.4 秒降下来）；同时预测下一步的按钮文字，点完后用本地 OCR 秒出框，Gemini 在后台核对；语音开始播放约 0.25 秒 |
| 周围这么吵，怎么不被打断？ | macOS 回声消除去掉它自己的声音；VAD 检测人声；声纹确认是本人才打断。检测到人声先把音量压低，确认后再真正停下，所以听起来没有延迟 |
| 隐私？ | 只在任务进行中截图；截图只发到我们自己的后端，再到 Gemini，不存储；声纹只存在本机，不上传 |
| 下一步做什么？ | Windows 版；手机版（帮爸妈远程学用手机）；记住学过的流程，下次直接用 |

> 「1 秒内出框」「误打断率」这两个数字在彩排时实测后填进来，没测到的不要说。

---

## 4. Devpost 要准备的材料

| 材料 | 负责人 | 截止 |
|---|---|---|
| demo 视频（YouTube 不公开链接） | Teresa | 09:30 |
| 3–5 张截图：对话面板、高亮框、完成画面、「任何软件」、架构图 | Teresa | 09:30 |
| 项目描述（Inspiration / What it does / How we built it / Challenges / Accomplishments / What we learned / What's next） | Teresa 写，Joey 补技术部分和评测数字 | 10:00 草稿 |
| 「Built with」标签：gemini-api, elevenlabs, elevenlabs-agents, python, pyqt6, fastapi, macos-vision, avfoundation, onnxruntime, silero-vad | Joey | 10:00 |
| GitHub 链接 + 干净的 README（含架构图和评测数字） | Joey | 10:00 |
| 赛道：AI/ML、Best use of ElevenLabs、Computer Science Department；勾 MLH Best Use of Gemini API | Teresa | 提交时 |

**ElevenLabs 赛道要强调的点**：实时语音对话（Agents）、能打断、ElevenLabs 语音转文字做打字的替代、流式 TTS。
**Gemini 赛道要强调的点**：空间定位（框坐标）、看截图判断上一步是否完成、对话理解、用官方文档生成步骤大纲（Google 搜索）、Agent 的大脑也是 Gemini。

---

## 5. 架构图（Joey 用 Keynote 或 Excalidraw 画一张）

```
 ┌─────────────────── Mac 客户端 ───────────────────┐        ┌──────── 后端 (FastAPI) ────────┐
 │ 麦克风 → 回声消除 → VAD → 声纹确认 ──────────────┼──────▶ │ ElevenLabs Agent（大脑：Gemini）│
 │ 扬声器 ◀─────────────── 实时语音 ◀───────────────┼─────── │   工具：show_next_step          │
 │                                                  │        │         look_at_screen         │
 │ 截屏（先隐藏高亮层）+ 当前 App 名字 ─────────────┼──────▶ │ /api/next-step                 │
 │                                                  │        │   Gemini：找下一步元素框        │
 │ 透明高亮层：发光框 ◀─────────────────────────────┼─────── │   + 判断上一步是否完成          │
 │                                                  │        │   + 预测再下一步（next_guess）  │
 │ 画面变化检测 → 本地 OCR 找 next_guess → 秒出框    │        │ /api/chat：对话、提问           │
 │ 对话面板（打字 / 语音）                           │        │ /api/guide：官方文档步骤大纲    │
 └──────────────────────────────────────────────────┘        └────────────────────────────────┘
```

---

## 6. 时间表（周日）

| 时间 | 事项 | 谁 |
|---|---|---|
| 06:30–07:30 | 彩排：主任务跑 3 次、「任何软件」清单每个跑 1 次；实测「点完到出框」耗时和误打断次数，填进第 3 节；失败的步骤记下来交给对应的人修 | 两人 |
| **08:00** | **功能冻结** | |
| 08:00–08:45 | 在录制的地方录声纹 → 录屏（每段录 2–3 遍，挑最好的） | Teresa 操作，Joey 盯着屏幕检查 |
| 08:00–08:45 | 画架构图、写 README 技术部分 | Joey |
| 08:45–09:15 | 录旁白 + 剪辑 | Teresa（原理那段 Joey 录） |
| 09:15–09:30 | 上传 YouTube，截图 | Teresa |
| **10:00 前** | **交 Devpost 草稿** | Teresa |
| 10:00–11:15 | 两人各自通读一遍描述、看一遍视频、修改；彩排现场演示 | 两人 |
| **11:30 前** | **交最终版**（留 30 分钟余量） | Teresa |
