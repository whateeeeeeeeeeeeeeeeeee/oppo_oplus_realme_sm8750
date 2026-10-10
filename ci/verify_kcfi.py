#!/usr/bin/env python3
"""
verify_kcfi.py — KCFI 类型闸门

背景（两次真机 panic，都是这个原因）：
  #1  CFI failure at tasklet_action_common (target: usb_recv_tasklet
        [8188eu]; expected type: 0xaecee44b)
  #2  CFI failure at dev_hard_start_xmit  (target: rtw_xmit_entry
        [8188eu]; expected type: 0x44e57e43)

本内核 CONFIG_CFI_CLANG=y + CONFIG_PANIC_ON_OOPS=y + PANIC_TIMEOUT=-1，
任何一个函数指针类型不匹配都会：Oops → 立刻 panic → 无限重启。

原理：
  KCFI 在【每个函数入口前 4 字节】放一个 typeid（对函数原型做 hash）。
  内核做间接调用时会比对 typeid。所以只要直接读 .ko 里编出来的这 4 字节，
  就能在【编译产物层面】确认类型对不对，不需要真机试。

  期望值来源 = 内核自己的模块（vendor cfg80211.ko / mac80211.ko）
  编出来的符号 __kcfi_typeid_<某内核函数>，与内核期望完全一致。

用法：
    python3 verify_kcfi.py <8188eu.ko>
退出码：0 = 全部通过；1 = 有类型不匹配（调用方应中止构建）
"""
import re
import struct
import subprocess
import sys

# ---- 内核期望的 typeid（全部实测自 vendor 模块，非猜测）----
# ndo_start_xmit: netdev_tx_t (*)(struct sk_buff *, struct net_device *)
#   取自 vendor mac80211.ko: __kcfi_typeid_ieee80211_subif_start_xmit
EXPECT_XMIT = 0x44E57E43
# ndo_select_queue: u16 (*)(struct net_device *, struct sk_buff *, struct net_device *)
#   由 clang-18 (内核同版本) 复现
EXPECT_SELECT_QUEUE = 0x32D0349B
# tasklet: void (*)(unsigned long)   ← 已修，防回归
#   取自内核 panic 日志 "expected type"
EXPECT_TASKLET = 0xAECEE44B

# 必须返回 netdev_tx_t 的函数（注册进 net_device_ops.ndo_start_xmit）
XMIT_FUNCS = [
    "rtw_xmit_entry",
    "rtw_cfg80211_monitor_if_xmit_entry",
    "mgnt_xmit_entry",
]
SELECT_QUEUE_FUNCS = ["rtw_select_queue"]
TASKLET_FUNCS = ["usb_recv_tasklet", "rtl8188eu_xmit_tasklet", "mpath_tx_tasklet_hdl"]


def load(ko):
    return open(ko, "rb").read()


def sections(ko):
    out = {}
    txt = subprocess.run(["readelf", "-SW", ko], capture_output=True, text=True).stdout
    for ln in txt.split("\n"):
        m = re.match(r"\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", ln)
        if m:
            out[int(m.group(1))] = dict(name=m.group(2), addr=int(m.group(4), 16),
                                        off=int(m.group(5), 16), size=int(m.group(6), 16))
    return out


def symbols(ko):
    syms = {}
    for ln in subprocess.run(["nm", ko], capture_output=True, text=True).stdout.split("\n"):
        p = ln.split()
        if len(p) == 3 and p[1] in "TtWw":
            syms[p[2]] = int(p[0], 16)
    return syms


def typeid_at(data, secs, va):
    """读函数入口前 4 字节的 KCFI typeid"""
    for s in secs.values():
        if s["size"] and s["addr"] <= va - 4 < s["addr"] + s["size"]:
            b = data[s["off"] + (va - 4 - s["addr"]):][:4]
            if len(b) == 4:
                return struct.unpack("<I", b)[0]
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ko = sys.argv[1]
    data = load(ko)
    secs = sections(ko)
    syms = symbols(ko)

    print(f"  目标模块: {ko}  ({len(data)} 字节)")
    fails = []
    checks = 0

    def check(label, fname, expect):
        nonlocal checks
        va = syms.get(fname)
        if va is None:
            print(f"    {label:34s} {fname:38s} (未编入，跳过)")
            return
        got = typeid_at(data, secs, va)
        checks += 1
        ok = (got == expect)
        if not ok:
            fails.append((fname, got, expect))
        print(f"    {label:34s} {fname:38s} 0x{got:08x} "
              f"{'== 0x%08x OK' % expect if ok else '!= 0x%08x  *** 不符 ***' % expect}")

    print("  [ndo_start_xmit] 必须 netdev_tx_t — 内核期望 0x%08x" % EXPECT_XMIT)
    for f in XMIT_FUNCS:
        check("ndo_start_xmit", f, EXPECT_XMIT)

    print("  [ndo_select_queue] 必须 u16(...) — 内核期望 0x%08x" % EXPECT_SELECT_QUEUE)
    for f in SELECT_QUEUE_FUNCS:
        check("ndo_select_queue", f, EXPECT_SELECT_QUEUE)

    print("  [tasklet] 必须 void(unsigned long) — 内核期望 0x%08x" % EXPECT_TASKLET)
    for f in TASKLET_FUNCS:
        check("tasklet", f, EXPECT_TASKLET)

    print()
    if fails:
        print("  ❌ KCFI 类型闸门未通过：")
        for f, got, exp in fails:
            print(f"       {f}: 实际 0x{got:08x}  期望 0x{exp:08x}")
        print("     该模块在真机上会触发 CFI failure → Oops → panic → 无限重启。")
        return 1

    if checks == 0:
        print("  ❌ 一个都没校验到（符号全缺失？）—— 视为失败")
        return 1
    print(f"  ✅ KCFI 类型闸门通过（{checks} 项全部匹配）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
