#!/system/bin/sh
# ============================================================================
#  check.sh — 监听环境自检 + 问题诊断
#
#  用法（root）：sh check.sh
#  或在容器里：sudo sh check.sh
#
#  这个脚本只【读】不【写】，可以放心跑。
# ============================================================================

echo "════════════════════════════════════════════════════════"
echo "  外置网卡监听环境自检"
echo "════════════════════════════════════════════════════════"
echo "  时间: $(date)"
echo "  内核: $(uname -r)"
echo

# ---------------------------------------------------------------- 1. 硬件
echo "【1】USB 无线网卡是否在位"
echo "────────────────────────────────────────────────────────"
FOUND_HW=""
for d in /sys/bus/usb/devices/*/; do
    [ -f "$d/idVendor" ] || continue
    V=$(cat "$d/idVendor" 2>/dev/null)
    P=$(cat "$d/idProduct" 2>/dev/null)
    case "$V:$P" in
        0bda:8179|0bda:8178|0bda:8176|0bda:f179|0bda:817f|07b8:8179)
            N=$(cat "$d/product" 2>/dev/null)
            echo "  ✅ 找到无线网卡 $V:$P  $N  ($(basename $d))"
            FOUND_HW=$(basename "$d")
            ;;
    esac
done
[ -n "$FOUND_HW" ] || echo "  ❌ 没找到无线网卡 —— 检查是否插好（换个 USB 口试试）"
echo

# ---------------------------------------------------------------- 2. 驱动
echo "【2】驱动加载状态"
echo "────────────────────────────────────────────────────────"
for m in rtl8xxxu 8188eu rtl8187 mt7601u eeprom_93cx6; do
    L=$(grep "^$m " /proc/modules 2>/dev/null)
    if [ -n "$L" ]; then
        printf "  [已加载] %-14s 引用 %s\n" "$m" "$(echo "$L" | awk '{print $3}')"
    else
        printf "  [ 未加载] %-14s\n" "$m"
    fi
done
echo
echo "  ⚠️  rtl8xxxu 和 8188eu 不能同时加载（抢同一硬件）"
if grep -q "^rtl8xxxu " /proc/modules 2>/dev/null && grep -q "^8188eu " /proc/modules 2>/dev/null; then
    echo "  ❌ 检测到两个驱动同时加载！用 mode.sh 切换"
fi
echo

# ---------------------------------------------------------------- 3. 接口
echo "【3】网卡接口"
echo "────────────────────────────────────────────────────────"
USBIF=""
for n in /sys/class/net/*; do
    IF=$(basename "$n")
    L=$(readlink -f "$n/device" 2>/dev/null)
    case "$L" in
        *usb*)
            DRV=$(basename "$(readlink -f "$n/device/driver" 2>/dev/null)" 2>/dev/null)
            printf "  %-12s 驱动=%-12s\n" "$IF" "${DRV:-未绑定}"
            case "$DRV" in
                rtl8xxxu|rtl8187|mt7601u|8188eu) USBIF="$IF" ;;
            esac
            ;;
    esac
done
if [ -n "$USBIF" ]; then
    echo
    echo "  ✅ 可用接口: $USBIF"
else
    echo
    echo "  ❌ 没有可用的无线接口"
    echo
    echo "  诊断：若【1】找到了硬件但这里没接口，说明 probe 失败。"
    echo "  执行下面命令看原因："
    echo "      dmesg | grep -iE 'rtl8|8188' | tail -20"
    echo "  常见原因："
    echo "    -12 (-ENOMEM)  → 反复拔插导致资源泄漏，重新插拔或重启"
    echo "    -110 (超时)    → USB 口供电不足，换口或换线"
    echo "    固件加载失败   → 检查 /lib/firmware/rtlwifi/"
fi
echo

# ---------------------------------------------------------------- 4. 当前模式
echo "【4】当前工作模式"
echo "────────────────────────────────────────────────────────"
if grep -q "^8188eu " /proc/modules 2>/dev/null; then
    echo "  → 8188eu 模式（支持 monitor + 帧注入）✅ 可以抓包"
elif grep -q "^rtl8xxxu " /proc/modules 2>/dev/null; then
    echo "  → rtl8xxxu 模式（不支持 monitor）"
    echo "    想抓包请执行: sh mode.sh monitor"
else
    echo "  → 未加载"
fi
echo

# ---------------------------------------------------------------- 5. 固件
echo "【5】固件"
echo "────────────────────────────────────────────────────────"
echo "  firmware_class.path = '$(cat /sys/module/firmware_class/parameters/path 2>/dev/null)'"
for p in /lib/firmware/rtlwifi/rtl8188eufw.bin \
         /vendor/firmware/rtlwifi/rtl8188eufw.bin \
         /data/adb/modules/wifi_usb_drivers/firmware/rtlwifi/rtl8188eufw.bin; do
    if [ -f "$p" ]; then
        printf "  [有] %-58s %s 字节\n" "$p" "$(stat -c%s "$p" 2>/dev/null)"
    fi
done
echo

# ---------------------------------------------------------------- 6. 工具
echo "【6】监听工具（在容器里跑才有意义）"
echo "────────────────────────────────────────────────────────"
MISS=""
for c in iw ip airmon-ng airodump-ng aireplay-ng aircrack-ng tcpdump; do
    printf "  %-16s " "$c"
    if command -v $c >/dev/null 2>&1; then
        echo "✅ $(command -v $c)"
    else
        echo "❌ 缺"
        MISS="$MISS $c"
    fi
done
if [ -n "$MISS" ]; then
    echo
    echo "  缺这些。在 Arch 容器里装（用 --asdeps 避免污染，见文档）："
    echo "      sudo pacman -S --asdeps --needed iw aircrack-ng tcpdump"
fi
echo

# ---------------------------------------------------------------- 7. 权限
echo "【7】权限"
echo "────────────────────────────────────────────────────────"
echo "  当前: $(id)"
if [ "$(id -u)" = "0" ]; then
    echo "  ✅ root，可以 insmod / set type monitor"
else
    echo "  ⚠️  非 root。加载模块和切 monitor 需要 root："
    echo "      容器内: sudo <命令>"
    echo "      宿主机: su -c '<命令>'"
fi
echo

# ---------------------------------------------------------------- 8. 最近内核消息
echo "【8】最近内核消息（驱动相关）"
echo "────────────────────────────────────────────────────────"
dmesg 2>/dev/null | grep -iE 'rtl8|8188|monitor|firmware' | tail -20 \
    || echo "  （读不到 dmesg，需要 root）"
echo

echo "════════════════════════════════════════════════════════"
echo "  自检结束 —— 把上面全部内容发我"
echo "════════════════════════════════════════════════════════"
