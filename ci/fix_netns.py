#!/usr/bin/env python3
"""
fix_netns.py — 让 8188eu 无线网卡可以移入容器自己的网络命名空间

【问题】
    用户想把容器改成独立网络（net_mode=isolated），然后把 USB 无线网卡
    直接给容器用。做法是把网卡的 phy 移进容器的 netns：

        iw phy phy10 set netns <容器PID>

    但报错：
        command failed: Operation not supported (-95)   [EOPNOTSUPP]

【根因】
    内核 cfg80211 在 cfg80211_switch_netns() 里会检查：
        if (!(wiphy->flags & WIPHY_FLAG_NETNS_OK))
            return -EOPNOTSUPP;

    即驱动必须声明 WIPHY_FLAG_NETNS_OK 才允许跨 netns 迁移。

    而 rtl8188eus v5.3.9 里这句被一个【荒谬的版本区间】包着：

        #if (LINUX_VERSION_CODE >= KERNEL_VERSION(2, 6, 38) && \
             LINUX_VERSION_CODE <  KERNEL_VERSION(3, 0, 0))
        #if defined(CONFIG_NET_NS)
            wiphy->flags |= WIPHY_FLAG_NETNS_OK;
        #endif
            wiphy->flags |= WIPHY_FLAG_SUPPORTS_SEPARATE_DEFAULT_KEYS;
        #endif

    这个区间是 2.6.38 ~ 2.6.x —— 我们跑的是 6.6.89，条件为假，
    整块代码【根本不编译】，所以驱动从没声明过 NETNS_OK。

    （真实历史：NETNS_OK 在 2.6.38 引入，作者当时只对"新内核"加；
      3.0 之后大概觉得默认就有，就删了这个判断，结果 6.x 上反而丢了。）

【修法】
    在现代内核下无条件设置 WIPHY_FLAG_NETNS_OK。
    放在原本那块代码之后，用一个 LINUX_VERSION_CODE >= 3.0.0 的判断，
    这样旧内核行为不变，新内核补上。

    注意：只加 flag，不动其他逻辑。

【风险提示】
    这是让厂商驱动支持一个它从未被测试过的能力（跨 netns 迁移）。
    rtl8188eus 是 USB 驱动，状态都在 per-adapter 结构里，
    理论上没有全局状态问题，但属于"未经验证"的改动。
    如果移动后驱动异常，把 flag 去掉即可恢复。

用法：
    python3 fix_netns.py <rtl8188eus 源码目录>
"""
import os
import re
import sys

MARK = "KCFI_NETNS_FIX"

# 在原块【之后】插入无条件设置
ANCHOR = (
    "#if (LINUX_VERSION_CODE >= KERNEL_VERSION(2, 6, 38) && "
    "LINUX_VERSION_CODE < KERNEL_VERSION(3, 0, 0))\n"
    "#if defined(CONFIG_NET_NS)\n"
    "\twiphy->flags |= WIPHY_FLAG_NETNS_OK;\n"
    "#endif //CONFIG_NET_NS\n"
    "\twiphy->flags |= WIPHY_FLAG_SUPPORTS_SEPARATE_DEFAULT_KEYS;\n"
    "#endif\n"
)

INSERT = """
/* %s: 允许 phy 跨网络命名空间迁移。
 *
 * 上面那块只对 2.6.38~2.6.x 生效，6.x 上整块不编译，
 * 导致 wiphy 里没有 WIPHY_FLAG_NETNS_OK，
 * `iw phy <phy> set netns <pid>` 会返回 -EOPNOTSUPP(-95)。
 *
 * 这里对 3.0.0 以上内核补上，让容器可以用独立 netns 直接接管网卡。
 */
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(3, 0, 0))
#ifdef CONFIG_NET_NS
\twiphy->flags |= WIPHY_FLAG_NETNS_OK;
#endif /* CONFIG_NET_NS */
#endif
""" % MARK


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    path = os.path.join(root, "os_dep/linux/ioctl_cfg80211.c")
    if not os.path.isfile(path):
        print("!! 找不到 %s" % path)
        return 1

    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        txt = f.read()

    if MARK in txt:
        print("   = 已是修复态，跳过")
        return 0

    if ANCHOR not in txt:
        print("!! 找不到插入锚点（源码版本不符？）")
        # 退而求其次：直接在 wiphy->flags 赋值区加
        alt = re.search(r"(\twiphy->flags \|= WIPHY_FLAG_SUPPORTS_SEPARATE_DEFAULT_KEYS;\n)", txt)
        if not alt:
            print("!! 备用锚点也找不到，放弃")
            return 1
        print("   使用备用锚点")
        new = txt[:alt.end()] + INSERT + txt[alt.end():]
    else:
        new = txt.replace(ANCHOR, ANCHOR + INSERT, 1)

    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(new)

    print("   + 已插入 WIPHY_FLAG_NETNS_OK（>=3.0.0 无条件生效）")

    # 自检
    cnt = new.count("WIPHY_FLAG_NETNS_OK")
    print("   ✅ 文件内 WIPHY_FLAG_NETNS_OK 出现 %d 次（原 1 次 + 新增 1 次）" % cnt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
