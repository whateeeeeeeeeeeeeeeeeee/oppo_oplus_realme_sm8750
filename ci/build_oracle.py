#!/usr/bin/env python3
"""
build_oracle.py — 从一批 .ko 构建【手机内核 CRC 全表】。

原理：
  每个厂商 .ko 的 __versions 段记录了「手机内核为每个符号算出的 CRC」。
  另外它的 __ksymtab 记录了自己导出的符号及 CRC。
  把手机上所有 .ko 汇总起来，就得到一份近乎完整的手机内核 ABI 快照。

  有了它，就能在电脑上离线判断「自编模块能否在手机上加载」——
  不需要刷机去试。

用法:
  python3 build_oracle.py <ko目录> [-o oracle_full.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ko_crc import ELF, exports, imports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('kodir')
    ap.add_argument('-o', '--out', default='oracle_full.json')
    ap.add_argument('-q', '--quiet', action='store_true')
    a = ap.parse_args()

    flat = {}          # 符号 -> CRC
    origin = {}        # 符号 -> 描述
    conflict = {}      # 符号 -> [(来源, CRC)]
    kos = []

    for root, _, files in os.walk(a.kodir):
        for fn in sorted(files):
            if not fn.endswith('.ko'):
                continue
            p = os.path.join(root, fn)
            try:
                e = ELF(p)
            except Exception as ex:
                if not a.quiet:
                    print('  跳过 %s (%s)' % (fn, ex))
                continue

            nexp = nimp = 0
            # 导出：该模块提供给别人的
            for s, c in exports(e).items():
                nexp += 1
                if s in flat and flat[s] != c:
                    conflict.setdefault(s, [(origin[s], flat[s])]).append((fn, c))
                else:
                    flat[s] = c
                    origin.setdefault(s, fn + ' 导出')
            # 导入：手机内核（或其他模块）提供给它
            for s, c in imports(e).items():
                nimp += 1
                if s in flat and flat[s] != c:
                    conflict.setdefault(s, [(origin[s], flat[s])]).append((fn, c))
                else:
                    flat.setdefault(s, c)
                    origin.setdefault(s, fn + ' 导入')
            kos.append((fn, nexp, nimp))
            if not a.quiet:
                print('  %-34s 导出 %-5d 导入 %-5d' % (fn, nexp, nimp))

    print()
    print('=' * 68)
    print('汇总: %d 个模块, %d 个唯一符号' % (len(kos), len(flat)))
    print('=' * 68)
    if conflict:
        print('⚠️  %d 个符号存在 CRC 冲突（可能来自不同固件版本）:' % len(conflict))
        for s, lst in list(conflict.items())[:15]:
            print('   %-44s %s' % (s, lst[:3]))
    else:
        print('✅ 无 CRC 冲突 —— 所有模块出自同一内核')

    data = {
        'flat': flat,
        'origin': origin,
        'conflicts': {k: v for k, v in conflict.items()},
        'modules': [{'name': n, 'exports': e, 'imports': i} for n, e, i in kos],
    }
    json.dump(data, open(a.out, 'w'), indent=1, sort_keys=True)
    print('\n已写 %s (%.1f KB)' % (a.out, os.path.getsize(a.out) / 1024.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
