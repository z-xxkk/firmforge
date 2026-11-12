"""上板测试：烧录测试程序、读串口、解析结果（DECISIONS 第 78、81 条）。

- 烧录用 ESP-IDF 官方的 `idf.py -p <串口> flash`（烧完自动复位）；
- 读串口用 ESP-IDF Python 环境自带的 pyserial（核心库只用标准库，所以放在子进程里跑），
  读到结束标记、崩溃或超时就停；
- 串口找不到时结果记为 skipped 并写明原因，绝不判通过（CLAUDE.md 硬性规则 4）。

串口：参数指定，其次环境变量 FF_SERIAL_PORT，再其次自动找（S3 自带 USB 是 /dev/ttyACM*，经典 ESP32 的 USB 转串口芯片是 /dev/ttyUSB*）。
⚠ 还没有在真实板子上跑过（2026-11-01 板子未进入下载模式），接上板子后要实测。
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

from .chip_detect import ChipInfo, detect_chip
from .esp_idf import IdfEnv, IdfProject
from .harness import END, BoardTestResult, parse_board_output

FLASH_TIMEOUT_S = 180
RUN_TIMEOUT_S = 60

# 在 ESP-IDF 的 Python 环境里运行：读串口直到结束标记、崩溃或超时，原样写到标准输出
READER = r"""
import re, sys, time, serial
port, timeout, end = sys.argv[1], float(sys.argv[2]), sys.argv[3]
crash = ("Guru Meditation", "abort() was called", "Backtrace:")
buf = ""
deadline = time.time() + timeout
s = serial.Serial()
s.port, s.baudrate, s.timeout = port, 115200, 0.2
s.dtr = False                 # 打开前先释放 DTR/RTS：芯片自带 USB 上它们控制下载模式（GPIO9）和复位
s.rts = False
with s:
    s.rts = True              # 硬复位一次，从头跑测试程序（DTR 保持释放，不进下载模式）
    time.sleep(0.1)
    s.rts = False
    while time.time() < deadline:
        chunk = s.read(4096).decode("utf-8", "replace")
        if chunk:
            buf += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()
            done = re.search(r"\d+ Tests \d+ Failures \d+ Ignored\s*\r?\n(OK|FAIL)", buf)
            if end in buf or done or any(c in buf for c in crash):
                time.sleep(0.5)                       # 崩溃时多读一会儿，把回溯信息读完
                sys.stdout.write(s.read(8192).decode("utf-8", "replace"))
                break
"""


def find_port(explicit: str | None = None, env: dict[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    if explicit:
        return explicit
    if env.get("FF_SERIAL_PORT"):
        return env["FF_SERIAL_PORT"]
    # 先找乐鑫自带 USB（ESP32-S3），再找经典 ESP32 开发板上的 USB 转串口芯片（CP210x、CH340/CH9102、FTDI）
    for pattern in ("/dev/serial/by-id/usb-Espressif*", "/dev/ttyACM*", "/dev/serial/by-id/*CP210*",
                    "/dev/serial/by-id/*1a86*", "/dev/serial/by-id/*CH34*", "/dev/serial/by-id/*FTDI*", "/dev/ttyUSB*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


class BoardRunner:
    def __init__(self, idf: IdfEnv, port: str | None = None, *, run_timeout: float = RUN_TIMEOUT_S):
        self.idf, self.port, self.run_timeout = idf, find_port(port), run_timeout
        self._chip: ChipInfo | None = None

    def chip(self) -> ChipInfo | None:
        """识别板子上的芯片（只识别一次）；没有串口返回 None。"""
        if self.port is None or not (self.port.startswith("COM") or Path(self.port).exists()):
            return None
        if self._chip is None:
            self._chip = detect_chip(self.idf.python, self.port)
        return self._chip

    def run(self, project: IdfProject, log_path: Path | None = None) -> BoardTestResult:
        """project 必须已经编译成功。"""
        if self.port is None or not (self.port.startswith("COM") or Path(self.port).exists()):
            return BoardTestResult(skipped=f"找不到板子的串口（{self.port or '没有乐鑫 USB 设备'}），请插上板子或设置 FF_SERIAL_PORT")
        try:
            fl = self.idf.idf(["-p", self.port, "flash"], project.root, FLASH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return BoardTestResult(skipped=f"烧录超过 {FLASH_TIMEOUT_S} 秒")
        if fl.returncode != 0:
            tail = (fl.stdout + fl.stderr)[-600:]
            hint = "；板子可能需要手动进入下载模式（按住 BOOT 再按一下 RST）" if "Write timeout" in tail or \
                "Failed to connect" in tail else ""
            return BoardTestResult(skipped=f"烧录失败{hint}：{tail.strip()[-300:]}")
        try:
            rd = subprocess.run([self.idf.python, "-c", READER, self.port, str(self.run_timeout), END],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=self.run_timeout + 30, env=self.idf.env)
            out = rd.stdout
            if rd.returncode != 0:
                res = parse_board_output(out)
                finished = res.started and not res.hang
                # 读串口的程序自己出错退出（例如读到一半板子被拔掉）：测试没跑完不是代码的错，记 skipped；
                # 已经读到结束标记或崩溃信息的照常判定（崩溃后芯片重启可能让串口断开）
                if not finished and not res.crash:
                    if log_path is not None:
                        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(log_path).write_text(out, encoding="utf-8")
                    why = rd.stderr.strip().splitlines()[-1:] or ["没有输出"]
                    return BoardTestResult(skipped=f"读串口中途出错，测试没跑完（板子可能被拔掉或串口被占用）：{why[0][-300:]}")
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(out, encoding="utf-8")
        return parse_board_output(out)
