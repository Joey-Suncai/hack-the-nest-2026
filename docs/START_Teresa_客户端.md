# Teresa 开工指南（B：Mac 客户端 + 路演）

> 详细任务（文件、函数签名、测试、验证命令）在 `docs/superpowers/plans/2026-11-14-showme.md` 的**第三部分 C0–C9**。这页只是你的路线图。
> 接口以 `docs/API.md` 为准；demo 和提交见 `pitch/demo-plan.md`。

## 1. 第一次准备（10 分钟）
```bash
git clone https://github.com/Joey-Suncai/hack-the-nest-2026.git   # 已经 clone 过就 git switch main && git pull
cd hack-the-nest-2026
cp .env.example .env          # 客户端不需要填 key；后端在你电脑上跑 mock 时也不需要
brew install uv               # 没装过 uv 的话
```
系统设置 → 隐私与安全性 → **屏幕录制**：给你用的终端（Terminal / iTerm / VS Code）打开权限，然后**重启终端**。

## 2. 开工提示词（在 Claude Code 里贴这段）
```
读 CLAUDE.md、docs/API.md、docs/START_Teresa_客户端.md 和 docs/superpowers/plans/2026-11-14-showme.md。
我是 B（Teresa），负责 client/。从 Task C0 开始，按计划逐个任务做，只修改 client/、Makefile、.claude/launch.json，以及删除 frontend/。
每个任务做完先跑它的测试 / 手动验证，告诉我结果，再提交。C0 的技术小实验做完后先停下来，把三项验证结果告诉我。
```

## 3. 你的任务和时间
| 时间 | 任务 | 完成标准 |
|---|---|---|
| 17:00–17:45 | **C0** 建 `client/` + 技术小实验 | 三项结果：高亮层置顶？能点穿？截图里没有高亮层？→ **17:45 站会汇报** |
| 17:45–19:00 | **C1** 坐标换算、**C2** 截屏与编码 | `uv run pytest` 通过 |
| 19:00–21:00 | **C3** 高亮层、**C4** 面板 + 调后端、**C5** 画面变化检测、**C6** 控制器 | **M1（21:00）**：`make mock` 下框出现 → 画面变化 → 跳到下一步 → 第 4 次「完成！」 |
| 21:00–01:00 | **C7** 语音、**C8** OCR 吸附；选定 demo 任务发给 Joey | **M2（01:00）**：连 Joey 的真实后端跑通 Numbers 主任务 |
| 01:30–04:00 | 打磨面板 / 高亮样式（`frontend-design`、`unslop` skill）、写 PPT 大纲、做演示数据 `pitch/demo-data.numbers` | Joey 在睡觉，有问题先记下来 |
| 04:00–06:30 | **睡觉** | 睡前 push，群里说进度 |
| 06:30–07:30 | **C9** 和 Joey 彩排，两个任务各跑 3 次 | **M3** |
| 08:00 之后 | 录 demo 视频 → 剪辑 → 上传 → Devpost 草稿（10:00）→ 最终提交（11:30） | 见 `pitch/demo-plan.md` 第 6 节 |

## 4. 没有后端时怎么开发
- 在你自己电脑上跑 mock 后端：`make backend` 前加 `GEMINI_MOCK=1`，或者直接 `make mock`（C0 改完 Makefile 后，它会同时起 mock 后端和客户端）
- mock 的 `/api/next-step` 依次返回 3 个框（屏幕中央附近），第 4 次返回 `done`；`/api/speak` 返回 204（不播声音）
- 连 Joey 电脑上的真实后端：`SHOWME_API=http://<Joey 的局域网 IP>:8000 make client`

## 5. 一定要注意
- **不要**给高亮层设置「不被截屏」（`NSWindowSharingNone`），否则 demo 视频录不到高亮框。截屏时用「先隐藏再截」
- 坐标一律按**逻辑尺寸**换算（Retina 截图像素是 2 倍）
- 任何错误都显示在面板上，**客户端不能崩**
- C0 有任何一项不行 → 直接走「面板模式」降级（计划第一部分的降级方案 2），不要硬磨
- 每 1–2 小时合进 main：`git fetch && git rebase origin/main` → `make mock` 能跑 → `gh pr create --fill && gh pr merge --squash --delete-branch`
- 分支名用 `teresa/xxx`
