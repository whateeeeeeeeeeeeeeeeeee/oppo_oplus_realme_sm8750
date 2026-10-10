#!/usr/bin/env python3
"""
fix_cfi_xmit.py — 修复 rtl8188eus 剩余的全部 ndo_start_xmit CFI 类型不匹配

崩溃现场（console-ramoops-0 (最新)，第 2 次 panic）：
    CFI failure at dev_hard_start_xmit+0x?d/0x2d? 
        (target: rtw_xmit_entry+0x0/0x3c [8188eu]; expected type: 0x44e57e43)
    Internal error: Oops - CFI: 00000000f2008228 [#1] PREEMPT SMP

【与第 1 次 panic 的区别 —— 这次不是同一个 bug】
    第 1 次: tasklet_action_common -> usb_recv_tasklet,  expected 0xaecee44b
    第 2 次: dev_hard_start_xmit   -> rtw_xmit_entry,     expected 0x44e57e43
    v2 的 tasklet 修复【生效了】（typeid 已由 0xa488ebfc 变为 0xaecee44b），
    驱动因此走得更远：一路 probe 成功、注册 netdev、开始发包，
    直到发包路径才撞上这个新的类型不匹配。

根因：
    内核 include/linux/netdevice.h 的 net_device_ops 里
        netdev_tx_t (*ndo_start_xmit)(struct sk_buff *skb, struct net_device *dev);
    即回调必须返回 netdev_tx_t（enum，36 位有符号 → 实际 4 字节）。

    而驱动声明为返回 int：
        int rtw_xmit_entry(_pkt *pkt, _nic_hdl pnetdev)

    int 与 enum netdev_tx 在 KCFI 眼里是【不同类型】：
        int                     -> 0xab1f26f7   ← 驱动实际编出来的
        netdev_tx_t             -> 0x44e57e43   ← 内核期望的
    所以每次发包（dev_hard_start_xmit）都在 CFI 检查处 Oops → PANIC_ON_OOPS → 重启。

    注意：C 语言里 enum 与 int 隐式兼容，clang 不会报错，也没有警告 —— 
    即使加了 -Werror 也照样过。这正是它隐蔽的原因。

修法：
    把 3 个 xmit 回调的返回类型由 int 改成 netdev_tx_t，并让返回语句
    显式返回 NETDEV_TX_OK（0），与内核语义一致。

    涉及 4 处注册点 / 3 个函数：
      os_dep/linux/xmit_linux.c        rtw_xmit_entry           (rtw_netdev_ops, rtw_netdev_vir_if_ops)
      os_dep/linux/ioctl_cfg80211.c    rtw_cfg80211_monitor_if_xmit_entry (rtw_cfg80211_monitor_if_ops)
      os_dep/linux/mlme_linux.c        mgnt_xmit_entry          (rtl871x_mgnt_netdev_ops)
      include/xmit_osdep.h             原型同步

用法：
    python3 fix_cfi_xmit.py <rtl8188eus 源码目录>
"""
import os
import re
import sys

MARK = "KCFI_XMIT_FIX"

# (相对路径, 旧函数头正则, 新函数头, 需要插入 typedef 的锚点)
EDITS = [
    # --- 1) rtw_xmit_entry: 返回值改 netdev_tx_t ---
    (
        "os_dep/linux/xmit_linux.c",
        r"int rtw_xmit_entry\(_pkt \*pkt, _nic_hdl pnetdev\)\n\{",
        "netdev_tx_t rtw_xmit_entry(_pkt *pkt, _nic_hdl pnetdev)\n{\n"
        "\t/* %s: 内核 net_device_ops.ndo_start_xmit 返回 netdev_tx_t(enum)，\n"
        "\t * 不是 int。KCFI 把 enum 与 int 视为不同类型，返回 int 会在\n"
        "\t * 每次发包时触发 CFI failure 并 panic。 */\n" % MARK,
    ),
    # --- 2) monitor_if_xmit_entry ---
    (
        "os_dep/linux/ioctl_cfg80211.c",
        r"static int rtw_cfg80211_monitor_if_xmit_entry\(struct sk_buff \*skb, struct net_device \*ndev\)\n\{",
        "static netdev_tx_t rtw_cfg80211_monitor_if_xmit_entry(struct sk_buff *skb, struct net_device *ndev)\n"
        "{\n\t/* %s: 同 rtw_xmit_entry，必须返回 netdev_tx_t */\n" % MARK,
    ),
    # --- 3) mgnt_xmit_entry ---
    (
        "os_dep/linux/mlme_linux.c",
        r"static int mgnt_xmit_entry\(struct sk_buff \*skb, struct net_device \*pnetdev\)\n\{",
        "static netdev_tx_t mgnt_xmit_entry(struct sk_buff *skb, struct net_device *pnetdev)\n"
        "{\n\t/* %s: 同 rtw_xmit_entry，必须返回 netdev_tx_t */\n" % MARK,
    ),
    # --- 4) 头文件原型（必须只改 PLATFORM_LINUX 分支！
    #         FREEBSD 分支的 rtw_xmit_entry 返回 int，改错会变成冲突声明。
    #         用 "extern int _rtw_xmit_entry(...)" 紧邻的那一行做唯一锚点）---
    (
        "include/xmit_osdep.h",
        r"(extern int _rtw_xmit_entry\(_pkt \*pkt, _nic_hdl pnetdev\);\n)"
        r"extern int rtw_xmit_entry\(_pkt \*pkt, _nic_hdl pnetdev\);",
        "\\1/* %s: 必须与内核 net_device_ops.ndo_start_xmit 的返回类型一致\n"
        " * （仅 Linux 分支；FreeBSD 分支保持 int 不动）*/\n"
        "extern netdev_tx_t rtw_xmit_entry(_pkt *pkt, _nic_hdl pnetdev);" % MARK,
    ),
]

# 返回语句改写：仅在目标函数体内
RET_EDITS = [
    ("os_dep/linux/xmit_linux.c", "rtw_xmit_entry", "return ret;", "return (netdev_tx_t)ret;"),
    ("os_dep/linux/ioctl_cfg80211.c", "rtw_cfg80211_monitor_if_xmit_entry", "return ret;", "return (netdev_tx_t)ret;"),
    ("os_dep/linux/mlme_linux.c", "mgnt_xmit_entry",
     "return rtw_hal_hostap_mgnt_xmit_entry(padapter, skb);",
     "return (netdev_tx_t)rtw_hal_hostap_mgnt_xmit_entry(padapter, skb);"),
]


def read(p):
    with open(p, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(s)


def ensure_typedef(txt, path):
    """确保 netdev_tx_t 可见（通过内核头文件即可，这里只做检查）"""
    return txt


def func_body(txt, fname):
    """返回函数体（花括号配对），用于限定替换范围"""
    m = re.search(r"\b%s\s*\(" % re.escape(fname), txt)
    if not m:
        return None
    i = txt.find("{", m.end())
    if i < 0:
        return None
    depth = 0
    k = i
    while k < len(txt):
        if txt[k] == "{":
            depth += 1
        elif txt[k] == "}":
            depth -= 1
            if depth == 0:
                return (i, k + 1)
        k += 1
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    if not os.path.isdir(root):
        print("!! 目录不存在: %s" % root)
        return 2

    changed = []

    # 1) 函数签名
    for rel, pat, rep in EDITS:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            print("!! 缺少文件: %s" % rel)
            return 1
        t = read(p)
        # re.sub 的替换串里 \\1 是反向引用，不能转义反斜杠
        new, n = re.subn(pat, rep, t, count=1)
        if n == 0:
            if MARK in t:
                print("   = 已是修复态，跳过 %s" % rel)
                continue
            print("!! 未匹配: %s  /  %s" % (rel, pat))
            return 1
        write(p, new)
        changed.append(rel)
        print("   + %s" % rel)

    # 2) 返回语句（限定在函数体内）
    for rel, fname, old, new in RET_EDITS:
        p = os.path.join(root, rel)
        t = read(p)
        b = func_body(t, fname)
        if not b:
            print("!! 找不到函数 %s in %s" % (fname, rel))
            return 1
        a, z = b
        body = t[a:z]
        if old not in body:
            print("   = %s: 无 '%s'，跳过" % (fname, old))
            continue
        if new.strip() in body and old not in body:
            continue
        body2 = body.replace(old, new, 1)
        write(p, t[:a] + body2 + t[z:])
        changed.append(rel)
        print("   + %s: %s -> %s" % (fname, old, new))

    # 3) 自检：确认真正注册为 ndo_start_xmit 的那 3 个函数已改完
    #    （只查这 3 个 —— _rtw_xmit_entry / rtw_os_xmit_resource_alloc 不是回调，
    #      它们返回 int 是正确的，不能误报）
    print()
    TARGETS = [
        ("os_dep/linux/xmit_linux.c", "rtw_xmit_entry"),
        ("os_dep/linux/ioctl_cfg80211.c", "rtw_cfg80211_monitor_if_xmit_entry"),
        ("os_dep/linux/mlme_linux.c", "mgnt_xmit_entry"),
    ]
    ok = True
    for rel, fn in TARGETS:
        t = read(os.path.join(root, rel))
        if re.search(r"\bnetdev_tx_t\s+%s\s*\(" % re.escape(fn), t):
            print("   ✅ %s -> netdev_tx_t" % fn)
        else:
            print("   ❌ %s 仍未改为 netdev_tx_t" % fn)
            ok = False

    # 头文件 Linux 分支
    h = read(os.path.join(root, "include/xmit_osdep.h"))
    lin = re.search(r"#ifdef PLATFORM_LINUX(.*?)#endif /\* PLATFORM_LINUX \*/", h, re.S)
    if lin and re.search(r"extern\s+netdev_tx_t\s+rtw_xmit_entry", lin.group(1)):
        print("   ✅ xmit_osdep.h (PLATFORM_LINUX) -> netdev_tx_t")
    else:
        print("   ❌ xmit_osdep.h Linux 分支原型未改")
        ok = False
    if re.search(r"#ifdef PLATFORM_FREEBSD(.*?)#endif /\* PLATFORM_FREEBSD \*/", h, re.S) and \
       "extern int rtw_xmit_entry" in re.search(r"#ifdef PLATFORM_FREEBSD(.*?)#endif /\* PLATFORM_FREEBSD \*/", h, re.S).group(1):
        print("   ✅ FreeBSD 分支保持 int（未误改）")
    else:
        print("   ⚠️  FreeBSD 分支状态无法确认")

    # 确认 netdev_tx_t 可见（drv_types.h -> osdep_service_linux.h -> linux/netdevice.h）
    dl = os.path.join(root, "include/osdep_service_linux.h")
    if os.path.exists(dl) and "linux/netdevice.h" in read(dl):
        print("   ✅ netdev_tx_t 经 osdep_service_linux.h 可见")
    else:
        print("   ❌ 无法确认 netdev_tx_t 可见性")
        ok = False

    print()
    print("修改文件数: %d" % len(set(changed)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
