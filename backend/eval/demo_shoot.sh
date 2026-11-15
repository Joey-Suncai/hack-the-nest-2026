#!/bin/bash
# Demo 评测截图（VS Code：从 GitHub clone 一个 repo）。每一步弹窗告诉你要摆出什么画面，点「截图」后 3 秒自动截全屏。
# 用法：bash eval/demo_shoot.sh ~/Desktop/demo_shots              全部拍
#       bash eval/demo_shoot.sh ~/Desktop/demo_shots vscode_clone_4   只重拍指定的几张
#       DELAY=8 bash eval/demo_shoot.sh ...                          点「截图」后等几秒（默认 5）
#       SET=unseen bash eval/demo_shoot.sh ~/Desktop/unseen_shots     「没见过的软件」评测（每个软件 1 张）
# 每张截图都是「点击之前」的画面。练习用的 repo：https://github.com/octocat/Hello-World
# 截完按 eval/README.md 标注。
set -u
OUT="${1:?用法：bash eval/demo_shoot.sh <输出目录> [文件名...]}"
shift
ONLY=" $* "
DELAY="${DELAY:-5}"
mkdir -p "$OUT"

# 格式：文件名|弹窗说明
CASES_VSCODE=(
  "vscode_clone_1|【1/6】打开一个新的 VS Code 窗口（File > New Window），不打开任何文件夹，停在 Welcome 页。\n要点的是：Welcome 页里的「Clone Git Repository...」。"
  "vscode_clone_2|【2/6】点了 Clone Git Repository，顶部弹出输入框「Provide repository URL or pick a repository source」，框里还是空的。\n要点的是：这个输入框（下一步要粘贴 URL）。"
  "vscode_clone_3|【3/6】在输入框里粘贴 https://github.com/octocat/Hello-World ，下面出现「Clone from URL ...」选项，还没按回车。\n要点的是：「Clone from URL」那一项。"
  "vscode_clone_4|【4/6】弹出了选择文件夹的窗口（比如选桌面），还没点确认。\n要点的是：右下角蓝色「Select as Repository Destination」按钮。"
  "vscode_clone_5|【5/6】clone 完成，右下角弹出通知「Would you like to open the cloned repository?」。\n要点的是：通知里的「Open」。\n（通知一会儿会消失，要快点截。）"
  "vscode_clone_6|【6/6 · 完成】仓库已经打开，左边 Explorer 里能看到 README 文件。\n（如果先弹出「Do you trust the authors」对话框，先点「跳过」，下一张会截它。）"
  "vscode_clone_trust|【可选】弹出「Do you trust the authors of the files in this folder?」对话框。\n要点的是：「Yes, I trust the authors」。没弹就点「跳过」。"
)

# 「没见过的软件」：从没用来调过 prompt，每个软件 1 张，都是「点击之前」的画面
CASES_UNSEEN=(
  "unseen_finder|【访达 Finder】打开一个访达窗口（任意文件夹），左边侧边栏能看到「下载」。\n任务：打开「下载」文件夹。"
  "unseen_safari|【Safari】打开 Safari，任意一个网页，只开一个标签页。\n任务：新建一个标签页。"
  "unseen_gmail|【Gmail（Safari 或 Chrome）】打开 Gmail 收件箱（mail.google.com），页面加载完。\n任务：写一封新邮件。"
  "unseen_keynote|【Keynote】新建一个演示文稿（任意主题），停在编辑界面，只有 1 页。\n任务：添加一张新幻灯片。"
  "unseen_notes|【备忘录 Notes】打开备忘录，停在笔记列表界面。\n任务：新建一条备忘录。"
  "unseen_calendar|【日历 Calendar】打开日历，切到「周」或「日」视图（不要是「月」）。\n任务：切换到月视图。"
  "unseen_preview|【预览 Preview】用预览打开任意一张图片（比如桌面上的截图），工具栏可见。\n任务：把图片向左旋转。"
  "unseen_excel|【Excel】新建空白工作簿，在 A1 输入几个字并选中 A1，停在「开始」选项卡。\n任务：把 A1 的文字加粗。"
  "unseen_spotify|【Spotify】打开 Spotify 主页（Home）。\n任务：搜索一首歌。"
  "unseen_maps|【地图 Maps】打开地图，没有在搜索。\n任务：搜索附近的咖啡店。"
)
if [ "${SET:-vscode}" = "unseen" ]; then CASES=("${CASES_UNSEEN[@]}"); else CASES=("${CASES_VSCODE[@]}"); fi

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
