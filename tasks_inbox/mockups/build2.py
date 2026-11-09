"""把示意图片段拼成独立 HTML 并截图。用法：python3 build.py chat designer ..."""
import pathlib, subprocess, sys
here = pathlib.Path(__file__).parent
css = (here / "base.css").read_text(encoding="utf-8")
rail = (here / "rail2.html").read_text(encoding="utf-8")
TITLES = {"board_run": ("对话（上板）", "chat"), "hardware": ("硬件", "hw"), "eval": ("评测", "eval"), "chat": ("对话", "chat"), "designer": ("工作流设计器", "flow"), "settings": ("设置", "set"),
          "settings_tools": ("设置", "set"), "team": ("Agent 团队", "team")}
for name in sys.argv[1:]:
    title, on = TITLES[name]
    r = rail
    for k in ("chat", "hw", "eval", "flow", "team", "set"):
        r = r.replace("{" + k + "}", "on" if k == on else "")
    src = (here / f"{name}.src.html").read_text(encoding="utf-8").replace("{rail}", r)
    html = (f'<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><title>FirmForge {title}</title>'
            f"<style>{css}</style></head><body>{src}</body></html>\n")
    out = here.parent / f"gui_{name}.html"
    out.write_text(html, encoding="utf-8")
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1440,900",
                    f"--screenshot={here.parent / f'gui_{name}.png'}", out.resolve().as_uri()],
                   check=True, capture_output=True)
    print("ok", name)
