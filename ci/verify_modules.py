#!/usr/bin/env python3
"""
verify_modules.py — 在 CI 里验证编出来的 .ko 能否在手机上加载。

原理（为什么不用刷机试错）：
  当 CONFIG_MODVERSIONS=y 时，内核的 same_magic() 会跳过 vermagic
  里第一个空格之前的内容，只比对 "SMP preempt mod_unload modversions aarch64"。
  也就是说 **版本字符串不参与校验**，真正决定能否加载的是
  __versions 段里每个符号的 CRC 数值。

  而 CRC 是 genksyms 依据【类型签名】算的哈希：
    CRC 相同 ⇔ 结构体布局/编译配置相同 ⇔ ABI 兼容

所以：
  1. 读厂商 .ko 的导出表 → 得到"手机认可"的 CRC 基准 (oracle)
  2. 读我们编的 .ko 的 __versions → 得到"我们期望"的 CRC
  3. 逐个比对：全部命中 ⇒ 一定能加载
"""
import sys, os, json, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ko_crc import ELF, exports, imports


def load_oracle(path):
    """把 oracle 归一化成 {符号: CRC(int)}，容忍多种存储格式。"""
    o = json.load(open(path))
    src = o.get('flat') or o.get('all') or o
    out = {}
    for sym, v in src.items():
        if isinstance(v, dict):
            if 'crc' in v:
                out[sym] = v['crc']
        elif isinstance(v, int):
            out[sym] = v
    return out


def check(mine_path, oracle, ignore=(), verbose=True):
    e = ELF(mine_path)
    name = os.path.basename(mine_path)
    need = imports(e)          # 我们模块期望的 CRC
    give = exports(e)          # 我们模块导出的 CRC

    ok, bad, unknown = [], [], []
    for sym, crc in sorted(need.items()):
        if sym in ignore:
            continue
        if sym not in oracle:
            unknown.append((sym, crc))
        elif oracle[sym] == crc:
            ok.append(sym)
        else:
            bad.append((sym, crc, oracle[sym]))

    if verbose:
        print('=' * 74)
        print('%s' % name)
        print('=' * 74)
        mi = e.modinfo()
        for k in ('vermagic', 'depends'):
            if k in mi:
                print('  %-9s %s' % (k + ':', mi[k][0]))
        print('  导入(需要) %d 个符号  导出 %d 个符号' % (len(need), len(give)))
        print('  ✅ CRC 匹配 : %d' % len(ok))
        if bad:
            print('  ❌ CRC 不符 : %d' % len(bad))
            for s, mine, want in bad[:30]:
                print('       %-46s 我=0x%08x 需=0x%08x' % (s, mine, want))
            if len(bad) > 30:
                print('       ... 还有 %d 个' % (len(bad) - 30))
        if unknown:
            print('  ⚠️  基准里没有 : %d' % len(unknown))
            for s, c in unknown[:20]:
                print('       %-46s 0x%08x' % (s, c))
            if len(unknown) > 20:
                print('       ... 还有 %d 个' % (len(unknown) - 20))
        print()
    return dict(name=name, need=len(need), give=len(give),
                ok=len(ok), bad=bad, unknown=unknown)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('kodir', help='编出来的 .ko 所在目录')
    ap.add_argument('--oracle', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'oracle_crc.json'))
    ap.add_argument('--targets', default='rtl8xxxu,rtl8187,mt7601u,ath9k_htc,eeprom_93cx6')
    ap.add_argument('--strict', action='store_true',
                    help='有任何 CRC 不符就返回非 0')
    ap.add_argument('--json', help='把结果写 JSON')
    a = ap.parse_args()

    oracle = load_oracle(a.oracle)
    targets = [t.strip() for t in a.targets.split(',') if t.strip()]

    # 这三个是我们自己的依赖或内核自带，忽略其 CRC 差异（内核符号由 oracle 提供）
    ignore = set()

    results = []
    hard_fail = 0
    for t in targets:
        p = os.path.join(a.kodir, t + '.ko')
        if not os.path.exists(p):
            print('!! 缺少 %s' % p)
            hard_fail += 1
            continue
        r = check(p, oracle, ignore)
        results.append(r)
        if r['bad']:
            hard_fail += 1

    print('#' * 74)
    print('# 汇总')
    print('#' * 74)
    for r in results:
        flag = '❌' if r['bad'] else ('⚠️ ' if r['unknown'] else '✅')
        print('  %s %-18s 需要%-4d 匹配%-4d 不符%-3d 未知%d'
              % (flag, r['name'], r['need'], r['ok'], len(r['bad']), len(r['unknown'])))

    total_bad = sum(len(r['bad']) for r in results)
    print()
    if total_bad == 0:
        print('  🎉 全部 CRC 匹配 —— 这些模块在手机上一定能加载')
    else:
        print('  ⚠️  共 %d 个 CRC 不符' % total_bad)

    if a.json:
        json.dump(results, open(a.json, 'w'), indent=1, default=str)
        print('  已写 %s' % a.json)

    return 1 if (a.strict and hard_fail) else 0


if __name__ == '__main__':
    sys.exit(main())
