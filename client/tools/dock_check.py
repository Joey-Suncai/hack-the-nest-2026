"""打印 Dock 里每个图标的标题和位置，以及几个名字的匹配结果。用法：cd client && uv run python -m tools.dock_check"""

from showme.ax_snap import dock_items, match_dock_item

items = dock_items()
print(f"{len(items)} Dock items:")
for t, r in items:
    print(f"  {t!r:30} {r}")
for label in ["Google Chrome", "Chrome", "Finder", "System Settings"]:
    print(f"{label!r:20} → {match_dock_item(label, 'Click it in the Dock.', None, items)}")
