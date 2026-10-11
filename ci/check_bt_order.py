#!/usr/bin/env python3
"""
check_bt_order.py — 校验蓝牙模块加载顺序满足依赖

【为什么需要这个】
    v6 的 bluetooth.sh 里顺序写错了：
        ORDER="bluetooth rfcomm hidp hci_uart btqca"
    两个问题：
      1. btqca 排在 hci_uart 后面 → hci_uart 找不到 qca_* 符号
      2. btbcm 根本不在列表里     → hci_uart 找不到 btbcm_* 符号

    症状很有误导性：insmod 报
        failed to load hci_uart.ko: No such file or directory
    但文件其实在。真实错误是 Unknown symbol (err -2 = -ENOENT)，
    被 insmod 误报成了"文件不存在"。

    实际 dmesg：
        hci_uart: Unknown symbol btbcm_read_pcm_int_params (err -2)
        hci_uart: Unknown symbol qca_read_soc_version (err -2)

【做法】
    从每个 .ko 的 .modinfo 里读 depends（权威数据，不靠猜），
    验证脚本里的 ORDER 是一个合法的拓扑序。

用法：
    python3 check_bt_order.py <模块目录> "bluetooth btbcm btqca rfcomm hidp hci_uart"
"""
import os
import subprocess
import sys

# 这些依赖由内核或其他模块提供，不需要在 ORDER 里
EXTERNAL = {"rfkill", "crypto", "crc32c", "libcrc32c"}


def depends_of(path):
    """读 .ko 的 depends 字段"""
    try:
        out = subprocess.run(
            ["modinfo", "-F", "depends", path],
            capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:
        return None
    return [x for x in out.split(",") if x]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    bdir, order = sys.argv[1], sys.argv[2].split()
    if not order:
        print("  !! ORDER 为空")
        return 1

    deps = {}
    missing_file = False
    for m in order:
        p = os.path.join(bdir, m + ".ko")
        if not os.path.isfile(p):
            print("    !! %s.ko 不存在" % m)
            missing_file = True
            continue
        d = depends_of(p)
        if d is None:
            print("    !! 读不到 %s.ko 的 depends" % m)
            missing_file = True
            continue
        deps[m] = d

    if missing_file:
        return 1

    # 拓扑校验
    have = set(EXTERNAL)
    bad = False
    for i, m in enumerate(order):
        before = set(order[:i])
        miss = [d for d in deps.get(m, []) if d not in have and d not in before]
        mark = "OK" if not miss else "缺 " + ",".join(miss)
        print("    %-12s depends=%-28s %s" % (m, ",".join(deps.get(m, [])) or "-", mark))
        if miss:
            bad = True
        have.add(m)

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
