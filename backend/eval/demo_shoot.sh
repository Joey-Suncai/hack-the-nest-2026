#!/bin/bash
# Demo 评测截图（VS Code：从 GitHub clone 一个 repo）。每一步弹窗告诉你要摆出什么画面，点「截图」后 3 秒自动截全屏。
# 用法：bash eval/demo_shoot.sh ~/Desktop/demo_shots              全部拍
#       bash eval/demo_shoot.sh ~/Desktop/demo_shots vscode_clone_4   只重拍指定的几张
#       DELAY=8 bash eval/demo_shoot.sh ...                          点「截图」后等几秒（默认 5）
# 每张截图都是「点击之前」的画面。练习用的 repo：https://github.com/octocat/Hello-World
# 截完按 eval/README.md 标注。
set -u
OUT="${1:?用法：bash eval/demo_shoot.sh <输出目录> [文件名...]}"
shift
ONLY=" $* "
DELAY="${DELAY:-5}"
mkdir -p "$OUT"

# 格式：文件名|弹窗说明
CASES=(
  "vscode_clone_1|【1/6】打开一个新的 VS Code 窗口（File > New Window），不打开任何文件夹，停在 Welcome 页。\n要点的是：Welcome 页里的「Clone Git Repository...」。"
  "vscode_clone_2|【2/6】点了 Clone Git Repository，顶部弹出输入框「Provide repository URL or pick a repository source」，框里还是空的。\n要点的是：这个输入框（下一步要粘贴 URL）。"
  "vscode_clone_3|【3/6】在输入框里粘贴 https://github.com/octocat/Hello-World ，下面出现「Clone from URL ...」选项，还没按回车。\n要点的是：「Clone from URL」那一项。"
  "vscode_clone_4|【4/6】弹出了选择文件夹的窗口（比如选桌面），还没点确认。\n要点的是：右下角蓝色「Select as Repository Destination」按钮。"
  "vscode_clone_5|【5/6】clone 完成，右下角弹出通知「Would you like to open the cloned repository?」。\n要点的是：通知里的「Open」。\n（通知一会儿会消失，要快点截。）"
  "vscode_clone_6|【6/6 · 完成】仓库已经打开，左边 Explorer 里能看到 README 文件。\n（如果先弹出「Do you trust the authors」对话框，先点「跳过」，下一张会截它。）"
  "vscode_clone_trust|【可选】弹出「Do you trust the authors of the files in this folder?」对话框。\n要点的是：「Yes, I trust the authors」。没弹就点「跳过」。"
)

for item in "${CASES[@]}"; do
  name="${item%%|*}"
  text="${item#*|}"
  if [ "$ONLY" != "  " ] && [[ "$ONLY" != *" $name "* ]]; then continue; fi
  text="${text//\"/\\\"}"
  choice=$(osascript -e "button returned of (display dialog \"$text\n\n摆好后点「截图」，$DELAY 秒后自动截全屏（文件：$name.png）\" with title \"Demo 截图\" buttons {\"跳过\", \"全部停止\", \"截图\"} default button \"截图\")" 2>/dev/null)
  case "$choice" in
    "截图")
      sleep "$DELAY"
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
