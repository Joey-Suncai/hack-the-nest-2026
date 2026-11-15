#!/bin/bash
# 按任务截图（读 bench/tasks.json）。每一步弹窗告诉你要摆出什么画面，点「截图」后几秒自动截全屏。
# 用法：bash backend/bench/shoot.sh ~/Desktop/bench_raw                         全部拍
#       TASK=simple_gmail bash backend/bench/shoot.sh ~/Desktop/bench_raw       只拍一个任务
#       bash backend/bench/shoot.sh ~/Desktop/bench_raw simple_gmail__03         只重拍指定的几张
#       DELAY=8 bash backend/bench/shoot.sh ...                                 点「截图」后等几秒（默认 5）
# 每张截图都是「点击之前」的画面，文件名 <任务id>__<两位步号>.png。vscode_clone_demo 已有截图，跳过。
# 截完按 backend/bench/README.md 用 label.py 标注。
set -u
OUT="${1:?用法：bash backend/bench/shoot.sh <输出目录> [文件名...]}"
shift
ONLY=" $* "
DELAY="${DELAY:-5}"
TASK="${TASK:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"

# 每行：文件名|弹窗说明（已转义成 AppleScript 字符串，\n 是换行）
list_cases() {
  TASK="$TASK" python3 - "$HERE/tasks.json" <<'PY'
import json, os, sys
only = os.environ.get("TASK", "")
for t in json.load(open(sys.argv[1])):
    if t.get("no_shoot") or (only and t["id"] != only):
        continue
    n = len(t["steps"])
    for i, s in enumerate(t["steps"], 1):
        text = (f"任务：{t['goal']}\n（{t['app']}）\n\n【{i}/{n}】{s['state']}\n\n要点的是：{s['target_hint']}")
        text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        print(f"{t['id']}__{i:02d}|{text}")
PY
}

while IFS= read -r item <&3; do
  name="${item%%|*}"
  text="${item#*|}"
  if [ "$ONLY" != "  " ] && [[ "$ONLY" != *" $name "* ]]; then continue; fi
  choice=$(osascript -e "button returned of (display dialog \"$text\n\n摆好后点「截图」，$DELAY 秒后自动截全屏（文件：$name.png）\" with title \"ShowMe 任务截图\" buttons {\"跳过\", \"全部停止\", \"截图\"} default button \"截图\")" 2>/dev/null)
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
done 3< <(list_cases)
