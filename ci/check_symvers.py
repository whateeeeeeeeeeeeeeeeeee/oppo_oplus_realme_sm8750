#!/usr/bin/env python3
"""
check_symvers.py — 用【本次编译】的 Module.symvers 校验内核 ABI 是否与手机一致。

为什么这是最强判据：
  厂商模块(cfg80211/mac80211/rfkill/libarc4)的 __versions 段记录的是
  「手机正在运行的内核」为每个符号算出的 CRC 期望值。
  它是手机内核 ABI 的【直接快照】，不需要刷机去试。

  如果本次编译产出的 Module.symvers 里这些符号的 CRC 完全一致，
  就证明这次编译的配置在 ABI 层面与手机内核等价 ——
  于是自编模块所引用的内核符号也一定能解析成功。

用法:
  python3 ci/check_symvers.py <out/Module.symvers> [oracle_crc.json]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LINE = re.compile(r'^(0x[0-9a-fA-F]+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$')


def parse_symvers(path):
    """Module.symvers: crc<TAB>symbol<TAB>module<TAB>export_type

    返回 {符号: (crc, 提供者)}；提供者 == 'vmlinux' 表示由内核本体提供。
    """
    out = {}
    with open(path, 'r', errors='replace') as f:
        for ln in f:
            m = LINE.match(ln.rstrip('\n'))
            if not m:
                continue
            crc, sym, mod, _ = m.groups()
            try:
                out[sym] = (int(crc, 16), mod)
            except ValueError:
                continue
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    sv_path = sys.argv[1]
    opath = sys.argv[2] if len(sys.argv) > 2 else 'ci/oracle_crc.json'

    if not os.path.exists(sv_path):
        print('!! 找不到 %s' % sv_path)
        return 1

    allsv = parse_symvers(sv_path)
    # 只关心内核本体导出的符号 —— 模块间导出与手机无关
    mine = {s: c for s, (c, m) in allsv.items() if m == 'vmlinux'}
    o = json.load(open(opath))
    src = o.get('flat') or o.get('all') or o
    oracle = {}
    for sym, v in src.items():
        if isinstance(v, dict) and 'crc' in v:
            oracle[sym] = v['crc']
        elif isinstance(v, int):
            oracle[sym] = v

    print('Module.symvers 总符号 %d，其中内核本体(vmlinux)导出 %d'
          % (len(allsv), len(mine)))
    print('基准(手机内核认可)符号  : %d' % len(oracle))
    print()

    # 只看【手机内核确实提供】的符号：即基准里有、我们也有
    common = set(mine) & set(oracle)
    same = sorted(s for s in common if mine[s] == oracle[s])
    diff = sorted(s for s in common if mine[s] != oracle[s])

    print('=' * 72)
    print('内核导出符号 CRC 比对（这决定自编模块能否引用内核符号）')
    print('=' * 72)
    print('  共有符号 : %d' % len(common))
    print('  ✅ 一致  : %d' % len(same))
    print('  ❌ 不符  : %d' % len(diff))
    for s in diff[:40]:
        print('     %-46s 我=0x%08x 手机=0x%08x' % (s, mine[s], oracle[s]))
    if len(diff) > 40:
        print('     ... 还有 %d 个' % (len(diff) - 40))

    # 我们缺少的（手机内核有、本次编译没导出）
    missing = sorted(set(oracle) - set(mine))
    if missing:
        print()
        print('  本次编译缺少 %d 个手机内核提供的符号（若被自编模块引用则会加载失败）' % len(missing))
        for s in missing[:25]:
            print('     %s' % s)
        if len(missing) > 25:
            print('     ... 还有 %d 个' % (len(missing) - 25))

    print()
    if not diff:
        print('结论: ✅ 内核 ABI 与手机一致 —— 自编模块引用的内核符号可正常解析')
    else:
        pct = 100.0 * len(same) / max(1, len(common))
        print('结论: ⚠️  一致率 %.2f%%（%d/%d 不符）' % (pct, len(diff), len(common)))
        print('       不符的符号若被自编模块引用，该模块会加载失败（不会重启）。')

    # 特别关注：自编驱动实际引用的内核符号
    print()
    print('提示：驱动模块自身的导入检查见 verify_modules.py')

    return 0


if __name__ == '__main__':
    sys.exit(main())
