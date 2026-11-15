# 按任务的端到端评测（bench）

`backend/eval/` 测的是单张截图；这里测**整个任务**：一个任务 = 一串按顺序的截图（每张都是「这一步点击之前」的画面），按顺序回放给真实的 `steps.next_step`，像客户端一样把前面几步的指令当 `history`、上一步的 `expect` 带下去。

任务都在 `tasks.json`（英文目标、`lang: en`，系统界面是中文，测的是「看不懂界面也能跟着做」）：

| id | 软件 | 目标 | 步数 |
|---|---|---|---|
| `simple_text_size` | 系统设置 | The words on my screen are too small. Make the text bigger. | 4 |
| `simple_gmail` | Chrome 里的 Gmail | Write an email to my daughter saying I'll call her tonight. | 5 |
| `complex_zoom_invite` | Zoom → Gmail | Schedule a Zoom meeting with my family tomorrow at 3 pm, then email them the invitation. | 10 |
| `vscode_clone_demo` | VS Code | Clone the GitHub repo …/Hello-World（用 eval 里现成的 6 张截图） | 6 |

## 1. 截图

```bash
bash backend/bench/shoot.sh ~/Desktop/bench_raw                     # 全部
TASK=simple_gmail bash backend/bench/shoot.sh ~/Desktop/bench_raw   # 只拍一个任务
bash backend/bench/shoot.sh ~/Desktop/bench_raw simple_gmail__03     # 只重拍某几张
```

每一步弹窗写着：任务、第几步、画面应该是什么样、要点的是什么。摆好画面点「截图」，5 秒后（`DELAY=8` 可改）自动截全屏，听到「叮」就好了。**不要真的点那个按钮之外的东西，也不要真的发邮件。**文件名 `<任务id>__<两位步号>.png`。

注意：
- `simple_text_size` 第 4 步截完后，把「显示器」改回「默认」，否则后面截图的逻辑尺寸会变
- `complex_zoom_invite` 里「日历」要选「其他日历」，保存后才会弹出带「复制到剪贴板」的窗口
- 第 7 步是故意「走错软件」：Chrome 最大化挡住 Zoom，期望模型说先切回 Zoom（unsure）
- 邮箱都用假的：daughter.test@example.com / family.test@example.com

## 2. 标注

对每张图：打开原图，Cmd+Shift+4 把十字准星移到要点的元素左上角、右下角，记下两组坐标，按 Esc。然后（步号从 1 开始）：

```bash
cd backend
uv run python bench/label.py ~/Desktop/bench_raw simple_gmail 1 --pt 20 140 130 180
uv run python bench/label.py ~/Desktop/bench_raw simple_text_size 4 --done      # 完成画面
uv run python bench/label.py ~/Desktop/bench_raw complex_zoom_invite 7 --unsure # 走错软件
```

脚本把图缩到屏幕逻辑尺寸（如 1470×956）存成 `bench/shots/<名字>.jpg`，把 `file` 和 `target_px` 写进 `tasks.json`，并打开一张画了红框的预览图核对（`--no-open` 不打开）。

## 3. 运行

```bash
cd backend
uv run python bench/run.py                                # 全部任务跑一轮
uv run python bench/run.py --task simple_gmail --runs 3   # 一个任务跑 3 轮
uv run python bench/run.py --runs 3 --json /tmp/bench.json  # 存原始结果
```

需要真实 Gemini（`GEMINI_MOCK=1` 时会直接退出）。没拍 / 没标的步骤会显示「not shot yet / not labeled yet」并跳过，所以什么都没拍也能跑（只有 `vscode_clone_demo` 会真跑）。

## 4. 怎么看结果

每步一行：
- ✅ / ❌：命中。要框的步 = 模型框的中心落在标注框里；`done` / `unsure` 步 = 模型返回的 status 对；`accept_unsure` 的步返回 unsure 也算对（比如第一步软件还没打开）
- `IoU`：框的重合度（参考，不算命中）
- 耗时：这一步从调用到返回的秒数
- `label` / `guess`：模型说的目标元素和它对下一步的预测；`🔁 LOOP` = 和上一步的 target_label 一样（原地打转）
- `→「…」`：念给用户听的指令和单词数（越短越好，`⚠️ 含中文` = 英文指令里混了中文，语音读不了）

每个任务一行：
- **任务成功**：所有步都命中才算成功（这是最重要的数）；有步骤没拍时显示「不完整」，不计入成功率
- 步命中率、总耗时 / 平均每步耗时、loop 次数
- next_guess：给出几次、能核对几次（下一步也跑了）、猜中几次（猜的 label 和下一步模型返回的 target_label 互为子串，区分大小写，因为客户端要拿它做 OCR 精确匹配）

注意回放是「真实串联」的：上一步没命中时，下一步的 history 里那一条会是 `ok=false`，用来看模型能不能从错误里恢复；但截图本身是固定的（按正确路径拍的），所以一步错不代表后面一定错。

`--runs N` 时汇总里给每个任务成功 k/N、步命中率平均（min / max），以及每轮任务成功率的平均和最差 / 最好。
