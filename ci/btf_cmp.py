#!/usr/bin/env python3
"""
btf_cmp.py — 用 BTF 比对两个 .ko 的结构体布局，找出差异。

为什么用 BTF：
  符号表会被 LTO 内联/消除而失真，但 BTF 记录的是
  【编译时的真实类型布局】，不受 LTO 影响。
  而 genksyms 算 CRC 时正是一层层展开结构体成员的类型，
  所以「结构体布局哪里不同」= 「CRC 为什么不同」。

用法:
  python3 btf_cmp.py <我的.ko> <厂商.ko>
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_crc import ELF


def parse(path):
    e = ELF(path)
    blob = None
    for s in e.secs:
        if s['name'] == '.BTF':
            blob = e.d[s['offset']:s['offset'] + s['size']]
            break
    if blob is None:
        return None
    (magic, ver, flags, hdr_len, type_off, type_len,
     str_off, str_len) = struct.unpack_from('<HBBIIIII', blob, 0)
    strs = blob[hdr_len + str_off: hdr_len + str_off + str_len]

    def gs(o):
        if o == 0 or o >= len(strs):
            return ''
        i = strs.find(b'\0', o)
        return strs[o:i].decode('utf-8', 'replace') if i >= 0 else ''

    def esz(k, v, kf):
        if k == 1:
            return 4
        if k == 3:
            return 12
        if k in (4, 5):
            return v * 12 + (4 if kf else 0)
        if k == 6:
            return v * 8 + (4 if kf else 0)
        if k == 13:
            return v * 8
        if k == 14:
            return 4
        if k == 15:
            return v * 12
        if k == 17:
            return 4
        if k == 19:
            return v * 12
        return 0

    types = []
    pos = hdr_len + type_off
    end = pos + type_len
    while pos < end:
        no, info, st = struct.unpack_from('<III', blob, pos)
        k = (info >> 24) & 0x1f
        v = info & 0xffff
        kf = (info >> 31) & 1
        pos += 12
        n = esz(k, v, kf)
        types.append(dict(name=gs(no), kind=k, vlen=v, size=st,
                          extra=blob[pos:pos + n], kflag=kf))
        pos += n
    return types


def structs(types):
    """{名字: (大小, [(成员名, 类型id, 位偏移)])}"""
    out = {}
    for t in types:
        if t['kind'] in (4, 5) and t['name']:
            mem = []
            for i in range(t['vlen']):
                no, ty, off = struct.unpack_from('<III', t['extra'], i * 12)
                # 需要字符串表才能取名，这里先占位
                mem.append((no, ty, off))
            out.setdefault(t['name'], (t['size'], mem, t))
    return out


def main():
    a, b = sys.argv[1], sys.argv[2]
    ta, tb = parse(a), parse(b)
    if ta is None or tb is None:
        print('有一方没有 .BTF'); return 1
    print('%s: %d 类型' % (os.path.basename(a), len(ta)))
    print('%s: %d 类型' % (os.path.basename(b), len(tb)))
    sa, sb = structs(ta), structs(tb)

    common = sorted(set(sa) & set(sb))
    print('\n共有结构体 %d 个' % len(common))
    diff = []
    for n in common:
        if sa[n][0] != sb[n][0]:
            diff.append((n, sa[n][0], sb[n][0]))
    print('大小不同的: %d' % len(diff))
    for n, x, y in diff[:60]:
        print('   %-46s 我=%-6d 厂商=%-6d' % (n, x, y))

    only_a = sorted(set(sa) - set(sb))
    only_b = sorted(set(sb) - set(sa))
    if only_a:
        print('\n只有我有的结构体: %d 个，例:' % len(only_a), only_a[:10])
    if only_b:
        print('只有厂商有的结构体: %d 个，例:' % len(only_b), only_b[:10])
    return 0


if __name__ == '__main__':
    sys.exit(main())
