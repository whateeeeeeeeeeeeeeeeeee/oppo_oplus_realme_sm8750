#!/usr/bin/env python3
"""
btf_members.py — 从 .ko 的 BTF 里提取指定结构体的成员表（名字+偏移+类型大小）。

BTF 是编译器写出的真实布局，不受 LTO 影响，所以能用来定位
「为什么 genksyms 算出的 CRC 不同」——结构体成员变了，CRC 就会变。

用法:
  python3 btf_members.py <.ko> <结构体名> [...]
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ko_crc import ELF as KELF


class BTF:
    def __init__(self, blob):
        (self.magic, self.ver, self.flags, self.hdr_len,
         self.type_off, self.type_len,
         self.str_off, self.str_len) = struct.unpack_from('<HBBIIIII', blob, 0)
        assert self.magic == 0xEB9F, 'bad BTF magic 0x%04x' % self.magic
        self.blob = blob
        base = self.hdr_len
        self.strs = blob[base + self.str_off: base + self.str_off + self.str_len]
        self.types = [None]                       # 1-based
        pos = base + self.type_off
        end = pos + self.type_len
        while pos < end:
            no, info, st = struct.unpack_from('<III', blob, pos)
            k = (info >> 24) & 0x1f
            v = info & 0xffff
            kf = (info >> 31) & 1
            pos += 12
            n = self._extra(k, v, kf)
            self.types.append(dict(name=self.s(no), kind=k, vlen=v,
                                   size=st, extra=blob[pos:pos + n], kflag=kf))
            pos += n

    def _extra(self, k, v, kf):
        if k == 1: return 4
        if k == 3: return 12
        if k in (4, 5): return v * 12 + (4 if kf else 0)
        if k == 6: return v * 8 + (4 if kf else 0)
        if k == 13: return v * 8
        if k == 14: return 4
        if k == 15: return v * 12
        if k == 17: return 4
        if k == 19: return v * 12
        return 0

    def s(self, off):
        if off == 0 or off >= len(self.strs):
            return ''
        i = self.strs.find(b'\0', off)
        return self.strs[off:i].decode('utf-8', 'replace') if i >= 0 else ''

    def find(self, name, kinds=(4, 5)):
        for i, t in enumerate(self.types):
            if t and t['kind'] in kinds and t['name'] == name:
                return i, t
        return None, None
    __getitem__ = lambda self, i: self.types[i]


def load(path):
    e = KELF(path)
    for s in e.secs:
        if s['name'] == '.BTF':
            return BTF(e.d[s['offset']:s['offset'] + s['size']])
    return None


def members(btf, name):
    i, t = btf.find(name)
    if not t:
        return None, None
    out = []
    for k in range(t['vlen']):
        no, ty, off = struct.unpack_from('<III', t['extra'], k * 12)
        out.append((btf.s(no), ty, off & 0xffffff, off >> 24))
    return t['size'], out


def describe(btf, ty, depth=0):
    """把类型 id 变成可读字符串"""
    if ty == 0 or ty >= len(btf.types):
        return 'void'
    t = btf.types[ty]
    if t is None:
        return '?'
    k = t['kind']
    if k == 1:
        return t['name'] or 'int'
    if k == 2:
        return describe(btf, t['size'], depth + 1) + ' *'
    if k in (4, 5, 7):
        return ('struct ' if k != 5 else 'union ') + (t['name'] or '?')
    if k == 6:
        return 'enum ' + (t['name'] or '?')
    if k == 8:
        return t['name'] or 'typedef'
    if k in (9, 10, 11):
        return describe(btf, t['size'], depth + 1)
    if k == 3:
        return 'array'
    return t['name'] or ('kind%d' % k)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    path = sys.argv[1]
    btf = load(path)
    if btf is None:
        print('无 .BTF')
        return 1
    print('%s  (%d 类型)' % (os.path.basename(path), len(btf.types) - 1))
    for nm in sys.argv[2:]:
        size, mem = members(btf, nm)
        print('\n=== %s ===' % nm)
        if size is None:
            print('   未找到')
            continue
        print('   size = %d 字节, 成员 %d 个' % (size, len(mem)))
        for i, (mn, ty, bitoff, bits) in enumerate(mem):
            print('   [%3d] +%-5d %-34s %-42s%s'
                  % (i, bitoff // 8, mn or '(anon)', describe(btf, ty),
                     (' :%d' % bits) if bits else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
