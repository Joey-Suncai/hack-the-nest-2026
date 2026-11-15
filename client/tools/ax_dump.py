"""打印客户端会发给 /api/next-step 的 elements（Accessibility 控件）和耗时。

用法：cd client && uv run python -m tools.ax_dump "Google Chrome"
"""

import sys
import time

from showme import ax_tree

app = " ".join(sys.argv[1:]) or None
for i in range(2):  # 第二次才是常态耗时（第一次要开 AXManualAccessibility）
    t = time.perf_counter()
    els = ax_tree.collect(app)
    print(f"run {i + 1}: {(time.perf_counter() - t) * 1000:.0f} ms total")
for e in els:
    print(f"  #{e['id']:<3} {e['role']:<22} {e['box']}  {e['name']!r}")
if not els:
    print("(没有元素：没权限、App 没在运行，或窗口在别的桌面)")
