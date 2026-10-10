#!/system/bin/sh
# ============================================================================
#  mode.sh — 在两种驱动之间切换
#
#  为什么需要切换：
#    rtl8xxxu 和 8188eu 是【两个不同的驱动，但抢同一块网卡】。
#    同时加载会导致互相抢设备，谁都工作不正常。
#    所以同一时间只能加载一个。
#
#    rtl8xxxu  — 内核主线驱动，稳定，但【不支持 monitor/注入】
#    8188eu    — aircrack-ng 版，【支持 monitor + 帧注入】，用于抓包/监听
#
#  用法（root）：
#    sh mode.sh status    查看当前用哪个
#    sh mode.sh monitor   切到 8188eu（监听模式用这个）
#    sh mode.sh normal    切回 rtl8xxxu（日常上网用这个）
# ============================================================================

MODDIR=${0%/*}
MDIR="$MODDIR/system/lib/modules"
IDIR="$MODDIR/independent"
LOG=/data/local/tmp/wifi_usb_drivers.log
FWDIR="$MODDIR/firmware"

log() { echo "$@" ; echo "$@" >> "$LOG" 2>/dev/null ; }

unload() {
    for m in rtl8xxxu rtl8187 mt7601u 8188eu; do
        if grep -q "^$m " /proc/modules 2>/dev/null; then
            rmmod "$m" 2>/dev/null && log "  已卸载 $m" || log "  !! 卸载 $m 失败（可能仍被占用）"
        fi
    done
}

load_common() {
    for m in eeprom_93cx6; do
        f="$MDIR/$m.ko"
        [ -f "$f" ] || continue
        grep -q "^$m " /proc/modules 2>/dev/null || insmod "$f" 2>/dev/null
    done
}

ensure_fw() {
    # 8188eu 请求的固件名与 rtl8xxxu 不同，需要单独提供
    if [ -f "$FWDIR/rtl8188eufw.bin" ]; then
        for base in /lib/firmware /vendor/firmware; do
            [ -d "$base" ] || continue
            mkdir -p "$base/rtlwifi" 2>/dev/null
            cp -f "$FWDIR/rtl8188eufw.bin" "$base/rtlwifi/" 2>/dev/null
        done
    fi
    P=/sys/module/firmware_class/parameters/path
    if [ -w "$P" ]; then
        OLD="$(cat "$P" 2>/dev/null)"
        case ",$OLD," in
            *",$FWDIR,"*) : ;;
            *) [ -n "$OLD" ] && echo -n "$FWDIR,$OLD" > "$P" || echo -n "$FWDIR" > "$P" ;;
        esac
    fi
}

case "${1:-status}" in
    status)
        echo "════════ 当前驱动状态 ════════"
        for m in eeprom_93cx6 rtl8xxxu rtl8187 mt7601u 8188eu; do
            if grep -q "^$m " /proc/modules 2>/dev/null; then
                printf "  [已加载] %s\n" "$m"
            else
                printf "  [ 未加载] %s\n" "$m"
            fi
        done
        echo
        echo "  当前模式:"
        if grep -q "^8188eu " /proc/modules 2>/dev/null; then
            echo "    → monitor 模式 (8188eu，支持抓包/注入)"
        elif grep -q "^rtl8xxxu " /proc/modules 2>/dev/null; then
            echo "    → 普通模式 (rtl8xxxu，不支持 monitor)"
        else
            echo "    → 未加载任何 8188 驱动"
        fi
        echo
        echo "  网卡接口:"
        for n in /sys/class/net/*; do
            IF=$(basename "$n")
            case "$IF" in lo|dummy*|sit*|tunl*|ip6*|gre*|erspan*|ifb*|rmnet*|r_rmnet*|ovnet*|p2p*|wifi-aware*|wlan0) continue ;; esac
            L=$(readlink -f "$n/device" 2>/dev/null)
            case "$L" in *usb*) echo "    $IF  (USB)" ;; esac
        done
        ;;

    monitor)
        log "════════ 切换到 monitor 模式 (8188eu) ════════"
        if [ ! -f "$IDIR/8188eu.ko" ]; then
            log "!! 找不到 $IDIR/8188eu.ko"
            log "   说明构建时 8188eu 没编出来，monitor 功能不可用。"
            exit 1
        fi
        unload
        load_common
        ensure_fw
        # 把 8188eu 依赖的 cfg80211 确保在（系统通常已加载）
        grep -q "^cfg80211 " /proc/modules 2>/dev/null || log "  提示: cfg80211 未加载"
        if insmod "$IDIR/8188eu.ko" 2>&1; then
            log "  [OK] 8188eu 已加载"
            sleep 3
            log "  网卡接口:"
            for n in /sys/class/net/*; do
                IF=$(basename "$n")
                L=$(readlink -f "$n/device" 2>/dev/null)
                case "$L" in *usb*) log "    $IF" ;; esac
            done
            log ""
            log "  下一步（切 monitor）："
            log "    iw dev <接口名> set type monitor"
            log "    或: airmon-ng start <接口名>"
        else
            log "  !! 8188eu 加载失败，看 dmesg 确认原因"
        fi
        ;;

    normal)
        log "════════ 切换回普通模式 (rtl8xxxu) ════════"
        unload
        load_common
        ensure_fw
        for m in rtl8xxxu rtl8187 mt7601u; do
            f="$MDIR/$m.ko"
            [ -f "$f" ] || continue
            if insmod "$f" 2>&1; then log "  [OK] $m"; else log "  [失败] $m"; fi
        done
        ;;

    *)
        echo "用法: sh mode.sh [status|monitor|normal]"
        echo
        echo "  status   查看当前状态"
        echo "  monitor  切换到 8188eu（支持抓包/注入，用于监听）"
        echo "  normal   切换回 rtl8xxxu（日常上网）"
        exit 1
        ;;
esac
