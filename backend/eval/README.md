# 定位评测（eval）

`eval_boxes.py` 逐张截图调用真实 Gemini，统计命中率（预测框中心落在正确框里）、平均 IoU、耗时，并打印每一步的 instruction。

```bash
cd backend
uv run python eval/eval_boxes.py              # 全部 case，各用自己的 lang
uv run python eval/eval_boxes.py --lang en    # 全部强制英文（用 goal_en / history_en）
uv run python eval/eval_boxes.py zoom         # 只跑文件名含 zoom 的
```

`labels.json` 里每个 case 三种期望：`target_px`（要框中）、`"expect_status": "done"`（已完成）、`"expect_status": "unsure"`（要用户先做点什么，比如 Zoom 工具栏隐藏了）。

## Zoom demo 截图流程

Demo 教两件事：**开新会议**、**共享屏幕**。Zoom 用中文界面也可以（评测用英文指令：测「看不懂界面也能跟着做」，汇总行会统计英文指令里混入中文的条数），屏幕上只放 Zoom。

### 1. 截图

```bash
cd backend
bash eval/zoom_shoot.sh ~/Desktop/zoom_shots
```

每一步会弹窗说明要摆出什么画面（都是「点击之前」的状态），摆好点「截图」，3 秒后自动截全屏，听到「叮」就好。没出现的画面点「跳过」。

### 2. 标注

先用 Cmd+Shift+4，把十字准星移到要点的按钮的左上角、右下角，记下旁边的两组坐标（逻辑坐标），按 Esc 取消。然后：

```bash
S=~/Desktop/zoom_shots
uv run python eval/add_case.py $S/zoom_new_1.png  --name zoom_new_1  --app Zoom --lang en --goal "Start a new meeting" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/zoom_new_2.png  --name zoom_new_2  --app Zoom --lang en --goal "Start a new meeting" --history "Click New Meeting" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/zoom_new_3.png  --name zoom_new_3  --app Zoom --lang en --goal "Start a new meeting" --history "Click New Meeting" --history "Click Join with Computer Audio" --done

uv run python eval/add_case.py $S/zoom_share_1.png --name zoom_share_1 --app Zoom --lang en --goal "Share my screen" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/zoom_share_2.png --name zoom_share_2 --app Zoom --lang en --goal "Share my screen" --history "Click Share in the bottom toolbar" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/zoom_share_3.png --name zoom_share_3 --app Zoom --lang en --goal "Share my screen" --history "Click Share in the bottom toolbar" --history "Select the window to share" --pt X1 Y1 X2 Y2
uv run python eval/add_case.py $S/zoom_share_4.png --name zoom_share_4 --app Zoom --lang en --goal "Share my screen" --history "Click Share in the bottom toolbar" --history "Select the window to share" --history "Click the blue Share button" --done
uv run python eval/add_case.py $S/zoom_share_hidden.png --name zoom_share_hidden --app Zoom --lang en --goal "Share my screen" --unsure
```

- 每次会打开一张画了红框的预览图，确认框对了再继续。
- 如果 zoom_new_2 被跳过了，zoom_new_3 的 `--history` 去掉 "Click Join with Computer Audio"。
- `--done`：任务已完成的画面；`--unsure`：期望模型让用户先移动鼠标。

### 3. 跑评测

```bash
uv run python eval/eval_boxes.py zoom
```

目标：命中率 ≥ 90%，平均每步 ≤ 4s。没命中的 case 看打印出来的 instruction / label，调 `prompts/next_step.txt`。
