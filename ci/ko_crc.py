#!/usr/bin/env python3
"""
ko_crc.py — 内核模块 CRC 工具（自包含，无外部依赖）

子命令
  exports  <a.ko>               打印导出的符号 + CRC
  imports  <a.ko>               打印导入(__versions)的符号 + CRC
  map      <a.ko> <out.json>    导出 → {符号: CRC} 写文件
  cmpmap   <mine.json> <theirs.json>
                                比较两份导出 CRC 表，输出匹配率
  patch    <a.ko> <crcmap.json> [out.ko]
                                按 crcmap 改写模块 __versions 里的 CRC

为什么这样能判断兼容：
  modversions 加载时只比对 __versions 里的 CRC 数值。
  CRC 是 genksyms 对【类型签名】算出来的哈希，
  所以两张导出表的 CRC 完全一致 ⇒ 结构体布局/配置完全一致 ⇒ ABI 安全。
"""
import struct, sys, os, json, shutil

SHT_SYMTAB = 2
MODVERSION_ENTSIZE = 64          # unsigned long crc; char name[56]


class ELF:
    def __init__(self, path):
        self.path = path
        self.d = open(path, 'rb').read()
        if self.d[:4] != b'\x7fELF' or self.d[4] != 2:
            raise ValueError('不是 64 位 ELF: ' + path)
        self.shoff = struct.unpack_from('<Q', self.d, 0x28)[0]
        self.shentsize = struct.unpack_from('<H', self.d, 0x3a)[0]
        self.shnum = struct.unpack_from('<H', self.d, 0x3c)[0]
        self.shstrndx = struct.unpack_from('<H', self.d, 0x3e)[0]
        self.secs = []
        for i in range(self.shnum):
            f = struct.unpack_from('<IIQQQQIIQQ', self.d, self.shoff + i * self.shentsize)
            self.secs.append(dict(i=i, name_off=f[0], type=f[1], addr=f[3],
                                  offset=f[4], size=f[5], link=f[6]))
        base = self.secs[self.shstrndx]['offset']
        for s in self.secs:
            e = self.d.index(b'\0', base + s['name_off'])
            s['name'] = self.d[base + s['name_off']:e].decode('utf-8', 'replace')

    def syms(self):
        out = []
        for s in self.secs:
            if s['type'] != SHT_SYMTAB:
                continue
            strbase = self.secs[s['link']]['offset']
            for i in range(s['size'] // 24):
                nmoff, info, other, shndx, val, size = struct.unpack_from(
                    '<IBBHQQ', self.d, s['offset'] + i * 24)
                if nmoff == 0:
                    continue
                e = self.d.index(b'\0', strbase + nmoff)
                out.append((self.d[strbase + nmoff:e].decode('utf-8', 'replace'),
                            shndx, val, size))
        return out

    def u32(self, shndx, val):
        if shndx == 0 or shndx >= len(self.secs):
            return None
        s = self.secs[shndx]
        p = s['offset'] + val
        return struct.unpack_from('<I', self.d, p)[0] if p + 4 <= len(self.d) else None

    def modinfo(self):
        info = {}
        for s in self.secs:
            if s['name'] in ('.modinfo', '__modinfo'):
                for f in self.d[s['offset']:s['offset'] + s['size']].split(b'\0'):
                    if b'=' in f:
                        k, _, v = f.decode('utf-8', 'replace').partition('=')
                        info.setdefault(k, []).append(v)
        return info


def exports(e):
    """导出符号 → CRC"""
    crcs = {}
    for n, shndx, val, size in e.syms():
        if n.startswith('__crc_'):
            s = n[6:]
            if shndx == 0xfff1:                       # SHN_ABS
                crcs[s] = val & 0xffffffff
            else:
                v = e.u32(shndx, val)
                if v is not None:
                    crcs[s] = v
    out = {}
    for n, shndx, val, size in e.syms():
        if n.startswith('__ksymtab_gpl_'):
            out[n[14:]] = crcs.get(n[14:])
        elif n.startswith('__ksymtab_') and not n.startswith('__ksymtab_strings'):
            s = n[10:]
            if s.startswith('gpl_') and s[4:] in out:
                continue
            out[s] = crcs.get(s)
    return {k: v for k, v in out.items() if v is not None}


def imports(e):
    """__versions 段：模块导入的符号 → 期望 CRC"""
    out = {}
    for s in e.secs:
        if 'version' not in s['name'] or s['size'] == 0:
            continue
        blob = e.d[s['offset']:s['offset'] + s['size']]
        for i in range(s['size'] // MODVERSION_ENTSIZE):
            off = i * MODVERSION_ENTSIZE
            crc = struct.unpack_from('<Q', blob, off)[0] & 0xffffffff
            nm = blob[off + 8:off + 64].split(b'\0')[0].decode('utf-8', 'replace')
            if nm:
                out[nm] = crc
    return out


def cmd_map(a, b):
    e = ELF(a)
    m = exports(e)
    json.dump(m, open(b, 'w'), indent=1, sort_keys=True)
    mi = e.modinfo()
    print('%s: 导出 %d 个符号 → %s' % (os.path.basename(a), len(m), b))
    if 'vermagic' in mi:
        print('   vermagic: %s' % mi['vermagic'][0])


def cmd_cmpmap(mine, theirs):
    A = json.load(open(mine))
    B = json.load(open(theirs))
    ka, kb = set(A), set(B)
    common = ka & kb
    same = [s for s in common if A[s] == B[s]]
    diff = [s for s in common if A[s] != B[s]]
    print('  我的导出: %d   对方导出: %d   共有: %d' % (len(ka), len(kb), len(common)))
    print('  ✅ CRC 一致 : %d' % len(same))
    print('  ❌ CRC 不同 : %d' % len(diff))
    if ka - kb:
        print('  (我多出 %d 个)' % len(ka - kb))
    if kb - ka:
        print('  (我缺少 %d 个) 例: %s' % (len(kb - ka), sorted(kb - ka)[:6]))
    for s in diff[:25]:
        print('     %-46s 我=0x%08x 对方=0x%08x' % (s, A[s], B[s]))
    if len(diff) > 25:
        print('     ... 还有 %d 个' % (len(diff) - 25))
    return len(diff)


def cmd_patch(ko, crcmap, outko=None):
    """
    就地改写模块 __versions 里的 CRC。
    注意：仅在确认结构体布局一致时才安全 —— 否则会 ABI 错配导致崩溃。
    """
    want = json.load(open(crcmap))
    data = bytearray(open(ko, 'rb').read())
    e = ELF(ko)
    changed = []
    for s in e.secs:
        if 'version' not in s['name'] or s['size'] == 0:
            continue
        for i in range(s['size'] // MODVERSION_ENTSIZE):
            off = s['offset'] + i * MODVERSION_ENTSIZE
            nm = bytes(data[off + 8:off + 64]).split(b'\0')[0].decode('utf-8', 'replace')
            if nm in want:
                old = struct.unpack_from('<Q', data, off)[0] & 0xffffffff
                if old != want[nm]:
                    struct.pack_into('<Q', data, off, want[nm])
                    changed.append((nm, old, want[nm]))
    dst = outko or ko
    if dst != ko:
        shutil.copyfile(ko, dst)
    open(dst, 'wb').write(bytes(data))
    print('  改写 %d 个 CRC → %s' % (len(changed), dst))
    for nm, o, n in changed[:15]:
        print('     %-46s 0x%08x → 0x%08x' % (nm, o, n))
    if len(changed) > 15:
        print('     ... 还有 %d 个' % (len(changed) - 15))
    return len(changed)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    c = sys.argv[1]
    if c == 'exports':
        e = ELF(sys.argv[2])
        m = exports(e)
        for k in sorted(m):
            print('  %-52s 0x%08x' % (k, m[k]))
        print('共 %d 个' % len(m))
    elif c == 'imports':
        e = ELF(sys.argv[2])
        m = imports(e)
        for k in sorted(m):
            print('  %-52s 0x%08x' % (k, m[k]))
        print('共 %d 个' % len(m))
    elif c == 'map':
        cmd_map(sys.argv[2], sys.argv[3])
    elif c == 'cmpmap':
        sys.exit(1 if cmd_cmpmap(sys.argv[2], sys.argv[3]) else 0)
    elif c == 'patch':
        cmd_patch(sys.argv[2], sys.argv[3],
                  sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        print(__doc__)
        sys.exit(1)
