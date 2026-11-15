# 定位评测（eval）

`eval_boxes.py` 逐张截图调用真实 Gemini，统计命中率（预测框中心落在正确框里）、平均 IoU、耗时，并打印每一步的 instruction。

```bash
cd backend
uv run python eval/eval_boxes.py              # 全部 case，各用自己的 lang
uv run python eval/eval_boxes.py --lang en    # 全部强制英文（用 goal_en / history_en）
uv run python eval/eval_boxes.py vscode       # 只跑文件名含 vscode 的
```

`labels.json` 里每个 case 三种期望：`target_px`（要框中）、`"expect_status": "done"`（已完成）、`"expect_status": "unsure"`（要用户先做点什么，比如软件没打开）。

## Demo 截图流程（VS Code：从 GitHub clone 一个 repo）

Demo 教的是：在 VS Code 里把 `https://github.com/octocat/Hello-World` clone 下来并打开。评测用英文指令（`--lang en`），汇总行会统计英文指令里混入中文的条数。

### 1. 截图

```bash
cd backend
bash eval/demo_shoot.sh ~/Desktop/demo_shots
```

每一步会弹窗说明要摆出什么画面（都是「点击之前」的状态），摆好点「截图」，3 秒后自动截全屏，听到「叮」就好。没出现的画面点「跳过」。

### 2. 标注

先用 Cmd+Shift+4，把十字准星移到要点的元素的左上角、右下角，记下旁边的两组坐标（逻辑坐标），按 Esc 取消。然后：

```bash
S=~/Desktop/demo_shots
uv run python eval/add_case.py $S/vscode_clone_1.png --name vscode_clone_1 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_2.png --name vscode_clone_2 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_3.png --name vscode_clone_3 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --history "Click the repository URL box at the top" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_4.png --name vscode_clone_4 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --history "Click the repository URL box at the top" --history "Paste the URL and choose Clone from URL" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_5.png --name vscode_clone_5 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --history "Click the repository URL box at the top" --history "Paste the URL and choose Clone from URL" --history "Choose a folder and click Select as Repository Destination" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_trust.png --name vscode_clone_trust --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --history "Click the repository URL box at the top" --history "Paste the URL and choose Clone from URL" --history "Choose a folder and click Select as Repository Destination" --history "Click Open in the notification" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/vscode_clone_6.png --name vscode_clone_6 --app "VS Code" --lang en --goal "Clone the GitHub repo https://github.com/octocat/Hello-World" --history "Click Clone Git Repository on the Welcome page" --history "Click the repository URL box at the top" --history "Paste the URL and choose Clone from URL" --history "Choose a folder and click Select as Repository Destination" --history "Click Open in the notification" --done
```

- 每次会打开一张画了红框的预览图，确认框对了再继续。
- 没弹「Do you trust the authors」就不加 `vscode_clone_trust`。
- `--done`：任务已完成的画面；`--unsure`：期望模型让用户先做点别的。

### 3. 跑评测

```bash
uv run python eval/eval_boxes.py vscode
```

目标：命中率 ≥ 90%，平均每步 ≤ 4s。没命中的 case 看打印出来的 instruction / label，调 `prompts/next_step.txt`。

## 「没见过的软件」评测（分组 unseen）

目的：证明 ShowMe 能教**任何**软件，不只是调过 prompt 的那几个。**不要针对这组调 prompt**，只看数字。

```bash
cd backend
SET=unseen bash eval/demo_shoot.sh ~/Desktop/unseen_shots     # 10 个软件，每个 1 张
# 标注：加 --group unseen --lang en
uv run python eval/add_case.py ~/Desktop/unseen_shots/unseen_finder.png --name unseen_finder --group unseen \
    --app Finder --lang en --goal "Open the Downloads folder" --pt X1 Y1 X2 Y2
uv run python eval/eval_boxes.py --group unseen     # 只跑这一组
uv run python eval/eval_boxes.py                    # 全部，最后按组分别给命中率
```
