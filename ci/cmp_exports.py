#!/usr/bin/env python3
"""
cmp_exports.py — 比对【自编模块】与【厂商模块】的导出符号 CRC。

用途：CI 里判断我们编出的 mac80211/cfg80211 是否与手机上的厂商版本 ABI 等价。

为什么这是关键判据：
  内核加载模块时只比对 __versions 里的 CRC 数值。
  CRC = genksyms 对类型签名算的哈希 ⇒
    导出表 CRC 完全一致 ⇔ 结构体布局与编译配置完全一致 ⇔ ABI 兼容

用法:
  python3 ci/cmp_exports.py <stage目录> [oracle_exports.json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ko_crc import ELF, exports


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else 'stage'
    opath = sys.argv[2] if len(sys.argv) > 2 else 'ci/oracle_exports.json'
    oracle = json.load(open(opath))

    overall = 0
    for mod in ('cfg80211', 'mac80211'):
        p = os.path.join(stage, mod + '.ko')
        if not os.path.exists(p):
            print('%s: 未产出（跳过）' % mod)
            continue
        mine = exports(ELF(p))
        ref = oracle.get(mod + '.ko', {})
        common = set(mine) & set(ref)
        same = [s for s in common if mine[s] == ref[s]]
        diff = sorted(s for s in common if mine[s] != ref[s])
        missing = sorted(set(ref) - set(mine))
        extra = sorted(set(mine) - set(ref))

        print('=' * 72)
        print('%s' % mod)
        print('  我导出 %d   厂商导出 %d   共有 %d' % (len(mine), len(ref), len(common)))
        print('  CRC 一致 : %d' % len(same))
        print('  CRC 不符 : %d' % len(diff))
        if missing:
            print('  我缺少   : %d  例: %s' % (len(missing), missing[:6]))
        if extra:
            print('  我多出   : %d  例: %s' % (len(extra), extra[:6]))
        for s in diff[:15]:
            print('     %-44s 我=0x%08x 厂商=0x%08x' % (s, mine[s], ref[s]))
        if len(diff) > 15:
            print('     ... 还有 %d 个' % (len(diff) - 15))
        overall += len(diff)

    print()
    if overall == 0:
        print('结论: 导出表 CRC 完全一致 → 与手机 ABI 等价')
    else:
        print('结论: 有 %d 处 CRC 不同 → 结构体布局与厂商不一致' % overall)
    return 0


if __name__ == '__main__':
    sys.exit(main())
