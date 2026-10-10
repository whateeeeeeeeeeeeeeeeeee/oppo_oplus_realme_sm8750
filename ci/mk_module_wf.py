#!/usr/bin/env python3
"""
mk_module_wf.py — 从【已验证可开机】的 fastbuild_6.6.89.yml 生成纯模块版工作流。

设计原则：
  * 复用原工作流的全部步骤一字不改（配置/补丁/工具链），保证内核符号 CRC 与手机一致
  * 只做 4 处手术：
      1. 开头加 checkout（要用 ci/ 校验脚本）
      2. 替换 wifi 配置步骤 → 纯 =m，不内建
      3. 构建目标 Image → Image + modules
      4. 打包 AnyKernel3 → 换成 KernelSU 模块 zip
  * 模块脚本以【真实文件】放在 module_scripts/，不用 heredoc 拼接

用法: python3 mk_module_wf.py [源yml] [输出yml]
"""
import sys
import yaml

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/wf.yml'
DST = sys.argv[2] if len(sys.argv) > 2 else '/tmp/wf_module.yml'

txt = open(SRC, encoding='utf-8').read()
orig_len = len(txt)


def cut(start_marker, end_marker):
    """返回 (start_idx, end_idx)"""
    s = txt.index(start_marker)
    e = txt.index(end_marker)
    assert s < e, 'marker 顺序不对: %s 应在 %s 之前' % (start_marker, end_marker)
    return s, e


# ===========================================================================
# 1. 开头加 checkout
# ===========================================================================
CHECKOUT = '''      - name: 取出本仓库 (为了拿到 ci/ 校验脚本与 module_scripts/)
        uses: actions/checkout@v4
        with:
          fetch-depth: 1

'''
_anchor = '      - name: 安装环境依赖+初始化源码仓库及llvm-Clang18工具链'
txt = txt[:txt.index(_anchor)] + CHECKOUT + txt[txt.index(_anchor):]

# ===========================================================================
# 2. 替换 wifi 配置步骤
# ===========================================================================
WIFI_STEP = '''      - name: 启用外置USB无线网卡驱动 (模块方案·不刷内核)
        if: inputs.wifi_drivers_enable
        run: |
          set -u
          echo "=========================================================="
          echo "  外置 USB 无线网卡驱动 —— 【纯模块方案】"
          echo "=========================================================="
          echo "  只编 .ko，不产出 boot 镜像、不碰任何分区、不加载任何东西。"
          echo "  失败最坏情况 = 网卡不认，绝不可能重启。"
          echo ""
          echo "  协议栈设成 =m：手机上 vendor_dlkm 已提供 cfg80211/mac80211，"
          echo "  本工作流编出的只用于【离线 CRC 比对】，不进模块 zip。"
          cd kernel_workspace/common
          DB=./arch/arm64/configs/gki_defconfig
          y() { for k in "$@"; do echo "CONFIG_${k}=y" >> "$DB"; done; }
          m() { for k in "$@"; do echo "CONFIG_${k}=m" >> "$DB"; done; }
          n() { for k in "$@"; do echo "# CONFIG_${k} is not set" >> "$DB"; done; }

          echo ">>> [1/3] 802.11 协议栈 (=m，与厂商 vendor_dlkm 一致)"
          y  WLAN WIRELESS WIRELESS_EXT WEXT_CORE WEXT_PROC WEXT_SPY WEXT_PRIV
          m  CFG80211 MAC80211 RFKILL CRYPTO_LIB_ARC4
          y  CFG80211_CRDA_SUPPORT CFG80211_DEFAULT_PS
          y  LEDS_CLASS LEDS_TRIGGERS RFKILL_LEDS DEBUG_FS EXPERT
          m  EEPROM_93CX6
          # 以下四项经【实测厂商 .ko】确认均为关闭：
          #   MAC80211_LEDS    —— 若为 y 会多导出 5 个 __ieee80211_*_led_name
          #                        （厂商 mac80211.ko 恰好导出 136 个，含 LED 则 141）
          #   MAC80211_MESH / MAC80211_DEBUGFS / CFG80211_WEXT / CFG80211_DEBUGFS
          #                    —— 对应函数在厂商 .ko 里根本不存在
          n  CFG80211_WEXT CFG80211_DEBUGFS CFG80211_REQUIRE_SIGNED_REGDB CFG80211_USE_KERNEL_REGDB_KEYS
          n  MAC80211_LEDS MAC80211_MESH MAC80211_DEBUGFS
          # NL80211_TESTMODE 必须【开启】(厂商是 y)。
          # 它给 struct cfg80211_ops 与 struct ieee80211_ops 各加 2 个成员，
          # 关掉会让 wiphy_new_nm / ieee80211_alloc_hw_nm 的 CRC 变化
          # —— 这两个正是驱动申请网卡的入口，对不上就完全不可用。
          y  NL80211_TESTMODE
          # 速率控制算法：rtl8xxxu 自己设 HAS_RATE_CONTROL，
          # mac80211 不会去选算法，关掉以免引入多余符号
          n  MAC80211_RC_MINSTREL MAC80211_RC_MINSTREL_HT MAC80211_RC_PID

          echo ">>> [2/3] USB 无线网卡驱动 (=m)"
          y  WLAN_VENDOR_REALTEK WLAN_VENDOR_ATH WLAN_VENDOR_RALINK WLAN_VENDOR_MEDIATEK
          m  RTL8XXXU RTL8187 MT7601U
          # 0bda:8179 在主设备表内，不受 UNTESTED 控制 → 必须关闭
          n  RTL8XXXU_UNTESTED
          # 不编 ATH9K_HTC：其 Kconfig 含
          #     select MAC80211_LEDS if LEDS_CLASS=y
          # 本内核 LEDS_CLASS=y，会强制打开 MAC80211_LEDS，
          # 使自编 mac80211 多出 5 个符号而与厂商的 136 个不一致。
          # 目标网卡是 RTL8188EU，用不到它。
          n  ATH9K_HTC ATH9K_HTC_DEBUGFS

          echo ">>> [3/3] 配置已写入，稍后以内核 out/.config 实测值为准"
'''

s, e = cut('      - name: 启用外置USB无线网卡驱动',
           '      - name: 启用ADIOS IO调度器')
txt = txt[:s] + WIFI_STEP + '\n' + txt[e:]

# ===========================================================================
# 3. 构建目标：Image → Image + modules
# ===========================================================================
OLD_MAKE = ('          make -j$(nproc --all) LLVM=1 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- '
            'CC="$(pwd)/cc-wrapper" LD="$(pwd)/ld-wrapper" HOSTLD=ld.lld O=out '
            'KCFLAGS+=-O2 KCFLAGS+=-Wno-error Image\n')
assert OLD_MAKE in txt, '找不到构建 Image 的命令'

MODULES_BUILD = '''
          echo "=========================================================="
          echo "  编译内核模块"
          echo "=========================================================="
          make -j$(nproc --all) LLVM=1 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \\
               CC="$(pwd)/cc-wrapper" LD="$(pwd)/ld-wrapper" HOSTLD=ld.lld O=out \\
               KCFLAGS+=-O2 KCFLAGS+=-Wno-error modules

          echo ">>> 收集模块"
          STAGE="$WORKDIR/kernel_workspace/wifi_stage"
          rm -rf "$STAGE"; mkdir -p "$STAGE"
          for w in cfg80211 mac80211 libarc4 rfkill rtl8xxxu rtl8187 eeprom_93cx6 mt7601u; do
            f=$(find out -name "$w.ko" 2>/dev/null | head -1)
            if [ -n "$f" ]; then
              cp "$f" "$STAGE/"; echo "  [有] $w.ko  ($(stat -c%s "$f") 字节)"
            else
              echo "  [无] $w.ko"
            fi
          done

          echo ">>> 关键配置实测值（写入≠采纳）"
          for k in CFG80211 MAC80211 NL80211_TESTMODE MAC80211_LEDS MAC80211_MESH \\
                   MAC80211_DEBUGFS CFG80211_WEXT RTL8XXXU RTL8XXXU_UNTESTED ATH9K_HTC; do
            printf "    %-24s " "$k"
            grep -E "^CONFIG_${k}=" out/.config || echo "(未设置)"
          done
          ls -la "$STAGE"
'''
txt = txt.replace(OLD_MAKE, OLD_MAKE + MODULES_BUILD, 1)

# ===========================================================================
# 4. 打包：AnyKernel3 → KernelSU 模块
# ===========================================================================
PACK_STEP = '''      - name: 验证 CRC 兼容性 (核心闸门·失败则整体失败)
        run: |
          set -u
          for f in ci/verify_modules.py ci/cmp_exports.py ci/ko_crc.py ci/oracle_crc.json; do
            if [ ! -f "$f" ]; then
              echo "!! 缺少 $f —— checkout 未生效，校验无法进行"
              echo "!! 这不是通过，而是校验被跳过。"
              exit 1
            fi
          done
          python3 ci/verify_modules.py kernel_workspace/wifi_stage \\
            --oracle ci/oracle_crc.json \\
            --targets rtl8xxxu,rtl8187,mt7601u,eeprom_93cx6 2>&1 | tee verify.txt

          echo
          echo "########## 自编协议栈导出表 vs 厂商（配置是否对齐）##########"
          python3 ci/cmp_exports.py kernel_workspace/wifi_stage ci/oracle_exports.json 2>&1 | tee -a verify.txt

          echo
          echo "########## vermagic ##########"
          for f in kernel_workspace/wifi_stage/*.ko; do
            [ -f "$f" ] || continue
            printf "  %-22s " "$(basename $f)"
            strings "$f" | grep -m1 '^vermagic=' || echo "(无)"
          done | tee -a verify.txt

      - name: 组装 KernelSU 模块 zip
        run: |
          set -e
          cd kernel_workspace
          M=wifi_module
          rm -rf "$M"
          mkdir -p "$M/system/lib/modules" "$M/firmware/rtlwifi"

          # 只打包手机上没有的四个模块。
          # 不打包 cfg80211/mac80211/rfkill/libarc4：
          #   手机 vendor_dlkm 已有厂商版本且在运行，
          #   重复提供只会带来约 20MB 冗余与潜在重复符号冲突风险。
          for x in eeprom_93cx6 rtl8xxxu rtl8187 mt7601u; do
            if [ -f "wifi_stage/$x.ko" ]; then
              cp "wifi_stage/$x.ko" "$M/system/lib/modules/"
            else
              echo "!! 缺少 $x.ko"; exit 1
            fi
          done

          # RTL8188EU 固件（驱动向内核请求 rtlwifi/rtl8188eufw.bin）
          curl -sSL --retry 3 -o /tmp/fw.bin \\
            https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain/rtlwifi/rtl8188eufw.bin
          if [ -s /tmp/fw.bin ]; then
            cp /tmp/fw.bin "$M/firmware/rtlwifi/rtl8188eufw.bin"
            echo "固件 OK: $(stat -c%s /tmp/fw.bin) 字节"
          else
            echo "!! 固件下载失败"; exit 1
          fi

          # 脚本以真实文件复制，便于逐行审查
          SD="$(pwd)/../module_scripts"
          [ -d "$SD" ] || SD="$GITHUB_WORKSPACE/module_scripts"
          [ -d "$SD" ] || { echo "!! 找不到 module_scripts"; exit 1; }
          for s in post-fs-data.sh service.sh customize.sh; do
            cp "$SD/$s" "$M/$s" || { echo "!! 缺少 $s"; exit 1; }
            echo "  [脚本] $s"
          done
          chmod 755 "$M/post-fs-data.sh" "$M/service.sh" "$M/customize.sh"

          STAMP="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M)"
          CODE="$(TZ=Asia/Shanghai date +%Y%m%d%H%M)"
          {
            echo "id=wifi_usb_drivers"
            echo "name=外置USB无线网卡驱动"
            echo "version=6.6.89-$STAMP"
            echo "versionCode=$CODE"
            echo "author=DSH"
            echo "description=为 OnePlus 13 (内核 6.6.89) 提供外置 USB 无线网卡驱动(RTL8188EU/8187/MT7601U)。纯模块加载，不修改内核、不写入任何分区；失败最坏情况仅网卡不认。"
          } > "$M/module.prop"

          ZIP="wifi_usb_drivers_6.6.89.zip"
          rm -f "$ZIP"
          ( cd "$M" && zip -r "../$ZIP" . -x '.*' >/dev/null )
          echo "=== $ZIP 内容 ==="
          unzip -l "$ZIP"
          echo "zipname=$ZIP" >> $GITHUB_OUTPUT
        id: pack

'''
s, e = cut('      - name: 克隆 AnyKernel3 并打包',
           '      - name: 上传 Ccache 调试日志')
txt = txt[:s] + PACK_STEP + txt[e:]

# ===========================================================================
# 5. 上传工件
# ===========================================================================
UPLOAD = '''      - name: 上传模块 zip
        uses: actions/upload-artifact@v7
        with:
          name: wifi-usb-drivers
          path: ${{ github.workspace }}/kernel_workspace/wifi_usb_drivers_*.zip
          archive: false
          if-no-files-found: error

      - name: 上传校验报告
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: crc-verify-report
          path: ${{ github.workspace }}/verify.txt
          archive: false
          if-no-files-found: warn
'''
s, e = cut('      - name: 上传 ZIP 工件', '      - name: 下载 ZIP 工件')
txt = txt[:s] + UPLOAD + txt[e:]

# 去掉发布阶段（下载工件 / 设置环境变量 / 创建发布），本工作流不发布 release
s = txt.index('      - name: 下载 ZIP 工件')
txt = txt[:s].rstrip() + '\n'

# ===========================================================================
# 6. 收尾调整
# ===========================================================================
# 移除 KPM 步骤（它 patch 的是 Image，本工作流不产出 Image）
s, e = cut('      - name: 应用KPM并修补内核', '      - name: 验证 CRC 兼容性')
txt = txt[:s] + txt[e:]

# job outputs 指向新步骤
txt = txt.replace('      ak3name: ${{ steps.create_zip.outputs.ak3name }}',
                  '      zipname: ${{ steps.pack.outputs.zipname }}', 1)

# 网卡驱动默认开启
txt = txt.replace(
    "      wifi_drivers_enable:\n"
    "        description: '是否编译外置USB无线网卡驱动(RTL8188EU/AR9271/MT7601U等，编成模块不刷内核，需配合Magisk模块加载)'\n"
    "        required: true\n"
    "        type: boolean\n"
    "        default: 'false'",
    "      wifi_drivers_enable:\n"
    "        description: '编译外置USB无线网卡驱动(只编.ko,不改内核;默认开启)'\n"
    "        required: true\n"
    "        type: boolean\n"
    "        default: 'true'", 1)

# 工作流改名
txt = txt.replace('name: 6.6.89 欧加真OKI内核快速构建',
                  'name: WIFI-MODULE 外置网卡驱动(只出ko·不刷内核)', 1)

open(DST, 'w', encoding='utf-8').write(txt)
print('原 %d → 新 %d 字节' % (orig_len, len(txt)))

# ===========================================================================
# 校验
# ===========================================================================
d = yaml.safe_load(txt)
on = d.get('on') or d.get(True)
steps = d['jobs']['build']['steps']
print('YAML OK — %d 步' % len(steps))
for i, st in enumerate(steps, 1):
    nm = st.get('name') or st.get('uses')
    flag = ''
    if any(k in str(nm) for k in ('网卡', 'CRC', '模块 zip', '取出本仓库')):
        flag = '  <<<'
    print('  %2d. %s%s' % (i, nm, flag))

print()
print('wifi_drivers_enable 默认值 =',
      on['workflow_dispatch']['inputs']['wifi_drivers_enable'].get('default'))
print('job outputs =', d['jobs']['build'].get('outputs'))

# 引用完整性检查
import re
names = set(on['workflow_dispatch']['inputs'])
used = set(re.findall(r'inputs\.([a-z_0-9]+)', txt))
missing = {u for u in used if u not in names and not any(n.startswith(u) for n in names)}
print('悬空的 inputs 引用:', missing or '无 ✅')
