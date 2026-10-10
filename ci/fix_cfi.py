#!/usr/bin/env python3
"""
fix_cfi.py — 修复 rtl8188eus 与内核 CFI 的兼容问题

崩溃现场（console-ramoops-0）：
    CFI failure at tasklet_action_common+0x244/0x4a8
        (target: usb_recv_tasklet+0x0/0xf8 [8188eu]; expected type: 0xaecee44b)
    Internal error: Oops - CFI: 00000000f2008228 [#1] PREEMPT SMP
    Kernel panic - not syncing: Oops - CFI: Fatal exception in interrupt

根因：
    内核 include/linux/interrupt.h 声明的 tasklet 回调类型是
        void (*func)(unsigned long)
    而该驱动把 void (void *) 的函数用强制类型转换塞进去：

        tasklet_init(&precvpriv->recv_tasklet,
                     (void(*)(unsigned long))usb_recv_tasklet,   ← 强转
                     (unsigned long)padapter);

    普通内核编译能过（强转骗过编译器），但本内核开启了
        CONFIG_CFI_CLANG=y
    CFI 会在【运行时】按函数指针的实际类型校验，强转骗不过它
    → 类型不匹配 → Oops → PANIC_ON_OOPS=y 放大成整机 panic

修法：
    把这三个回调的签名改成与内核声明一致：void (*)(unsigned long)
    同时去掉强转，让编译器直接做类型检查（这样以后不会退化）

    涉及三处（全仓 grep 确认无遗漏）：
      hal/hal_hci/hal_usb.c:28          usb_recv_tasklet
      hal/rtl8188e/usb/rtl8188eu_xmit.c rtl8188eu_xmit_tasklet
      core/mesh/rtw_mesh.c:2683         mpath_tx_tasklet_hdl

用法：
    python3 fix_cfi.py <rtl8188eus 源码目录>
"""
import os
import re
import sys

# (文件, 函数名) —— 需要把 void *priv 改成 unsigned long
TARGETS = [
    ('os_dep/linux/usb_ops_linux.c', 'usb_recv_tasklet'),
    # 注意：定义在 usb_ops_linux.c，不在 rtl8188eu_xmit.c（后者只是调用 tasklet_init）
    ('hal/rtl8188e/usb/usb_ops_linux.c', 'rtl8188eu_xmit_tasklet'),
    ('core/mesh/rtw_mesh.c', 'mpath_tx_tasklet_hdl'),
]

# 需要去掉强转的位置
CASTS = [
    ('hal/hal_hci/hal_usb.c',
     r'\(void\(\*\)\(unsigned long\)\)\s*usb_recv_tasklet',
     'usb_recv_tasklet'),
    ('hal/rtl8188e/usb/rtl8188eu_xmit.c',
     r'\(void\(\*\)\(unsigned long\)\)\s*rtl8188eu_xmit_tasklet',
     'rtl8188eu_xmit_tasklet'),
    ('core/mesh/rtw_mesh.c',
     r'\(void\(\*\)\(unsigned long\)\)\s*mpath_tx_tasklet_hdl',
     'mpath_tx_tasklet_hdl'),
]


def fix_signature(root, rel, fn):
    """把 `void fn(void *priv)` 改成 `void fn(unsigned long priv)`"""
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return 'skip(文件不存在)'
    src = open(path, encoding='utf-8', errors='replace').read()

    # 匹配: void <fn>(void *<name>)    也兼容 void* / 换行
    pat = re.compile(
        r'(void\s+' + re.escape(fn) + r'\s*\(\s*)void\s*\*\s*([A-Za-z_]\w*)\s*\)')
    new, n = pat.subn(r'\1unsigned long \2)', src)
    if n == 0:
        if 'unsigned long' in src and fn in src:
            return 'already'
        return 'notfound'
    open(path, 'w', encoding='utf-8').write(new)
    return 'fixed x%d' % n


def fix_declaration(root, rel, fn):
    """头文件里的原型也要一起改"""
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None
    src = open(path, encoding='utf-8', errors='replace').read()
    pat = re.compile(
        r'(void\s+' + re.escape(fn) + r'\s*\(\s*)void\s*\*\s*([A-Za-z_]\w*)\s*\)')
    new, n = pat.subn(r'\1unsigned long \2)', src)
    if n:
        open(path, 'w', encoding='utf-8').write(new)
        return 'decl fixed x%d' % n
    return None


def drop_cast(root, rel, pat, fn):
    """去掉强制类型转换"""
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return 'skip(文件不存在)'
    src = open(path, encoding='utf-8', errors='replace').read()
    new, n = re.subn(pat, fn, src)
    if n == 0:
        return 'already' if fn in src else 'notfound'
    open(path, 'w', encoding='utf-8').write(new)
    return 'cast removed x%d' % n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = sys.argv[1]
    if not os.path.isdir(root):
        print('!! 目录不存在: %s' % root)
        return 1

    print('=== 1. 修正函数签名（void* → unsigned long）===')
    for rel, fn in TARGETS:
        print('  %-42s %-22s %s' % (rel, fn, fix_signature(root, rel, fn)))

    print()
    print('=== 2. 修正头文件里的原型 ===')
    for rel, fn in (('include/usb_ops_linux.h', 'usb_recv_tasklet'),
                    ('include/rtl8188e_xmit.h', 'rtl8188eu_xmit_tasklet')):
        r = fix_declaration(root, rel, fn)
        print('  %-42s %-22s %s' % (rel, fn, r or '(无需改)'))

    print()
    print('=== 3. 去掉强制类型转换 ===')
    for rel, pat, fn in CASTS:
        print('  %-42s %-22s %s' % (rel, fn, drop_cast(root, rel, pat, fn)))

    print()
    print('=== 4. 复查：还有没有残留强转 ===')
    leftovers = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(('.c', '.h')):
                continue
            p = os.path.join(dirpath, f)
            try:
                txt = open(p, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            for m in re.finditer(r'\(void\s*\(\s*\*\s*\)\s*\(\s*unsigned long\s*\)\s*\)',
                                 txt):
                leftovers.append(os.path.relpath(p, root))
    if leftovers:
        print('  ⚠️ 仍有残留:')
        for l in sorted(set(leftovers)):
            print('     ', l)
    else:
        print('  ✅ 无残留')

    print()
    print('=== 5. 确认改动结果 ===')
    for rel, fn in TARGETS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding='utf-8', errors='replace').read()
        for m in re.finditer(r'void\s+' + re.escape(fn) + r'\s*\([^)]*\)', txt):
            print('  %-42s %s' % (rel, m.group(0)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
