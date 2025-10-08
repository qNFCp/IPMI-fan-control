#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IPMI 风扇自动调速脚本

功能概述:
1. 定期读取所有含 "Temp" 的传感器温度值（通过 `ipmitool sensor`）。
2. 取最高温度作为当前散热决策依据。
3. 按温度区间设置固定风扇占空比（先关闭自动，再发送 RAW 命令）。
4. 支持夜间时间段对风扇速度进行上限封顶（可跨越午夜）。
5. 使用 APScheduler 定时调度。

注意：
- RAW 命令适用于常见 Supermicro 等兼容机型，其他厂商可能不同。
- 退出时可选择恢复自动模式（需取消代码中注释）。
"""

import subprocess
import time
import re
from typing import List
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

# ---------------- 基本用户配置区域 ----------------
IPMI_HOST = "192.168.1.100"      # BMC / IPMI 地址
IPMI_USER = "ADMIN"              # 用户名
IPMI_PASSWORD = "PASSWORD"       # 密码
INTERVAL_SECONDS = 30            # 轮询间隔秒
IPMITOOL_CMD = "ipmitool"        # 若已在 PATH 中可直接用 ipmitool
# --------------------------------------------------

# ----------- 温度, 风扇转速百分比策略 ---------------
# (温度下限, 风扇百分比) 规则从高到低判断
TEMP_SPEED_RULES = [
    (70, 40),
    (60, 30),
    (40, 20),
    (30, 15),
    (-273, 5),  # 最低风扇转速百分比
]
MIN_PERCENT = 0
MAX_PERCENT = 100
# --------------------------------------------------

# ---------------- 夜间限速配置 --------------------
# 是否启用夜间风扇最大速率限制
NIGHT_LIMIT_ENABLED = True
# 夜间开始时间 (24h 格式 "HH:MM")
NIGHT_START = "23:00"
# 夜间结束时间 (24h 格式 "HH:MM") 可小于开始时间表示跨日
NIGHT_END = "07:30"
# 夜间最大允许速度百分比（在温控策略结果上再做封顶）
NIGHT_MAX_PERCENT = 25
# --------------------------------------------------


def run_ipmitool(args: List[str]) -> subprocess.CompletedProcess:
    """
    统一执行 ipmitool 命令。
    返回 subprocess.CompletedProcess，失败不抛异常但可查看 returncode / stderr。
    """
    try:
        result = subprocess.run(
            [IPMITOOL_CMD, "-I", "lanplus", "-H", IPMI_HOST, "-U", IPMI_USER, "-P", IPMI_PASSWORD] + args,
            text=True,
            capture_output=True,
            timeout=15
        )
        return result
    except subprocess.TimeoutExpired as e:
        print(f"[ERROR] ipmitool 命令超时: {e}")
        return subprocess.CompletedProcess(args, 1, "", "timeout")
    except Exception as e:
        print(f"[ERROR] 执行 ipmitool 异常: {e}")
        return subprocess.CompletedProcess(args, 1, "", str(e))


def disable_auto():
    res = run_ipmitool(["raw", "0x30", "0x30", "0x01", "0x00"])
    if res.returncode != 0:
        print(f"[WARN] 关闭自动模式失败: {res.stderr.strip()}")


def enable_auto():
    res = run_ipmitool(["raw", "0x30", "0x30", "0x01", "0x01"])
    if res.returncode != 0:
        print(f"[WARN] 开启自动模式失败: {res.stderr.strip()}")


def set_speed(percent: int):
    """
    设置风扇转速百分比（原始命令模式）。
    """
    if percent < MIN_PERCENT:
        percent = MIN_PERCENT
    if percent > MAX_PERCENT:
        percent = MAX_PERCENT

    disable_auto()  # 先关闭自动模式
    hex_byte = f"0x{percent:02x}"
    res = run_ipmitool(["raw", "0x30", "0x30", "0x02", "0xff", hex_byte])
    if res.returncode != 0:
        print(f"[WARN] 设置速度 {percent}% 失败: {res.stderr.strip()}")
    else:
        print(f"[INFO] 已设置风扇速度: {percent}% ({hex_byte})")


def parse_sensor_output(output: str) -> List[float]:
    """
    从 ipmitool sensor 输出中提取带 'Temp' 的温度值。
    """
    temps = []
    lines = output.strip().splitlines()
    for line in lines:
        if "Temp" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        field = parts[1].strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)", field)
        if m:
            try:
                val = float(m.group(1))
                if -50 < val < 200:
                    temps.append(val)
            except ValueError:
                continue
    return temps


def get_temps() -> List[float]:
    res = run_ipmitool(["sensor"])
    if res.returncode != 0:
        print(f"[ERROR] 读取传感器失败: {res.stderr.strip()}")
        return []
    return parse_sensor_output(res.stdout)


def choose_speed_by_temp(temp: float) -> int:
    """
    按 TEMP_SPEED_RULES 从上到下匹配第一个 temp >= 下限 的规则。
    """
    for lower_bound, pct in TEMP_SPEED_RULES:
        if temp >= lower_bound:
            return pct
    return TEMP_SPEED_RULES[-1][1]


def parse_hhmm(s: str):
    """
    把 'HH:MM' 解析为 (hour, minute)。
    """
    try:
        hour, minute = s.split(":")
        return int(hour), int(minute)
    except Exception:
        raise ValueError(f"时间格式错误: {s}，应为 HH:MM")


def is_in_time_window(now: datetime, start_str: str, end_str: str) -> bool:
    """
    判断当前时间是否处于 [start, end) 区间。
    支持跨午夜：
      - 若 start <= end：同日区间
      - 若 start > end ：跨越午夜，例如 23:00 - 07:30
    """
    sh, sm = parse_hhmm(start_str)
    eh, em = parse_hhmm(end_str)
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em
    now_minutes = now.hour * 60 + now.minute

    if start_minutes == end_minutes:
        # 认为整个 24 小时都在区间
        return True

    if start_minutes < end_minutes:
        # 不跨午夜
        return start_minutes <= now_minutes < end_minutes
    else:
        # 跨午夜：只要 >= start 或 < end
        return now_minutes >= start_minutes or now_minutes < end_minutes


def apply_night_limit(speed: int) -> int:
    """
    若开启夜间限速，且当前处于夜间，则对风扇速度做上限封顶。
    """
    if not NIGHT_LIMIT_ENABLED:
        return speed
    now = datetime.now()
    if is_in_time_window(now, NIGHT_START, NIGHT_END):
        if speed > NIGHT_MAX_PERCENT:
            print(f"[INFO] 夜间限速生效: {speed}% -> {NIGHT_MAX_PERCENT}% (区间 {NIGHT_START}-{NIGHT_END})")
            return NIGHT_MAX_PERCENT
        else:
            print(f"[DEBUG] 夜间限速已启用，但当前速度 {speed}% 未超过上限 {NIGHT_MAX_PERCENT}%")
    return speed


def auto_config():
    temps = get_temps()
    if not temps:
        print("[WARN] 未获取到有效温度数据，保持当前风扇状态。")
        return

    current_max = max(temps)
    base_speed = choose_speed_by_temp(current_max)
    print(f"[INFO] 当前最高温度: {current_max:.1f}°C -> 策略目标风扇: {base_speed}%")

    final_speed = apply_night_limit(base_speed)
    set_speed(final_speed)


def main():
    print("[INFO] IPMI 风扇自动调速脚本启动")
    print(f"[INFO] 轮询间隔: {INTERVAL_SECONDS} 秒, 目标主机: {IPMI_HOST}")
    if NIGHT_LIMIT_ENABLED:
        print(f"[INFO] 夜间限速启用: {NIGHT_START} - {NIGHT_END}, 最大 {NIGHT_MAX_PERCENT}%")
    else:
        print("[INFO] 夜间限速未启用")
    scheduler = BlockingScheduler()
    scheduler.add_job(auto_config, "interval", seconds=INTERVAL_SECONDS, max_instances=1, coalesce=True)
    try:
        auto_config()  # 立即执行一次
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        # print("\n[INFO] 接收到退出信号，尝试恢复自动控制...")
        # enable_auto()  # 若希望退出时恢复 BIOS 自动风扇转速控制，请取消此注释
        print("[INFO] 已退出。")


if __name__ == "__main__":
    main()
