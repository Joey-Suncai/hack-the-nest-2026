#!/bin/bash
# Zoom 评测截图：每一步弹窗告诉你要摆出什么画面，点「截图」后 3 秒自动截全屏。
# 用法：bash eval/zoom_shoot.sh ~/Desktop/zoom_shots
# 每张截图都是「点击之前」的画面。Zoom 用英文界面。截完按 eval/README.md 标注。
set -u
OUT="${1:?用法：bash eval/zoom_shoot.sh <输出目录>}"
mkdir -p "$OUT"

# 格式：文件名|弹窗说明
CASES=(
  "zoom_new_1|【开新会议 1/3】打开 Zoom 主窗口，停在 Home 标签页，不要在会议里。\n要点的是：New Meeting（橙色按钮）。"
  "zoom_new_2|【开新会议 2/3】点 New Meeting，等弹出音频提示框。\n要点的是：Join with Computer Audio。\n（如果你的电脑没弹这个框，点「跳过」。）"
  "zoom_new_3|【开新会议 3/3 · 完成】已经在会议里，鼠标放在会议窗口上，让底部工具栏显示出来。\n这是「开新会议」完成的状态。"
  "zoom_share_1|【共享屏幕 1/4】在会议里，鼠标放在会议窗口上，底部工具栏可见。\n要点的是：底部工具栏的绿色 Share 按钮。"
  "zoom_share_2|【共享屏幕 2/4】点 Share，共享选择窗口打开，还没选任何东西。\n要点的是：某个窗口缩略图或 Desktop 1。"
  "zoom_share_3|【共享屏幕 3/4】在共享选择窗口里选中一个窗口（有蓝框），还没点共享。\n要点的是：右下角蓝色 Share 按钮。"
  "zoom_share_4|【共享屏幕 4/4 · 完成】正在共享屏幕，屏幕顶部有绿色「You are screen sharing」条。\n这是「共享屏幕」完成的状态。"
  "zoom_share_hidden|【工具栏隐藏】在会议里，鼠标移开会议窗口，等底部工具栏自动隐藏（看不到 Share）。\n期望模型让用户先移动鼠标。截图前别动鼠标。"
)

for item in "${CASES[@]}"; do
  name="${item%%|*}"
  text="${item#*|}"
  text="${text//\"/\\\"}"
  choice=$(osascript -e "button returned of (display dialog \"$text\n\n摆好后点「截图」，3 秒后自动截全屏（文件：$name.png）\" with title \"Zoom 截图\" buttons {\"跳过\", \"全部停止\", \"截图\"} default button \"截图\")" 2>/dev/null)
  case "$choice" in
    "截图")
      sleep 3
      screencapture -x "$OUT/$name.png"
      afplay /System/Library/Sounds/Tink.aiff
      echo "已截图 $OUT/$name.png"
      ;;
    "全部停止"|"")
      echo "已停止"
      break
      ;;
    *)
      echo "跳过 $name"
      ;;
  esac
done
