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
#    sh mode.sh status      查看当前用哪个
#    sh mode.sh monitor     切到 8188eu（监听模式用这个）
#    sh mode.sh monitor-on  切到 8188eu 并直接设成 monitor（省一步）
#    sh mode.sh normal      切回 rtl8xxxu（日常上网用这个）
#
#  关于接口名（重要）：
#    接口名存在【网络命名空间】里，不在进程里。手机和 Droidspaces 容器
#    共用同一个 netns，所以名字只有一份，谁改两边都看到。
#    内核先给默认名（wlan1），随后容器里的 systemd-udevd 异步改名成
#    wlu1u3（wlu = wlan-over-USB + USB 总线/端口号）。
#    本脚本会等名字【连续两次一致】再输出，避免打印一个马上要变的名字。
# ============================================================================

MODDIR=${0%/*}
MDIR="$MODDIR/system/lib/modules"
IDIR="$MODDIR/independent"
LOG=/data/local/tmp/wifi_usb_drivers.log
FWDIR="$MODDIR/system/etc/firmware/rtlwifi"

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
    # 固件现在随模块放在 system/etc/firmware/rtlwifi/，
    # 由 KernelSU overlay 暴露成 /etc/firmware/rtlwifi/，ueventd 直接能找。
    #
    # 【不要】再改 firmware_class.path：
    #   那是全局内核参数，指向 /data/adb/... 会被 SELinux 拒绝(-13)，
    #   并且会连累设备上其它驱动（实测有线网卡 r8152 也被拦）。
    #
    # 这里只做一件事：确认固件在 overlay 后可见；不可见才补一份到 /lib/firmware。
    for p in /etc/firmware/rtlwifi/rtl8188eufw.bin \
             /system/etc/firmware/rtlwifi/rtl8188eufw.bin; do
        [ -f "$p" ] && return 0
    done

    if [ -f "$FWDIR/rtl8188eufw.bin" ]; then
        mkdir -p /lib/firmware/rtlwifi 2>/dev/null
        cp -f "$FWDIR/rtl8188eufw.bin" /lib/firmware/rtlwifi/ 2>/dev/null && \
            chmod 644 /lib/firmware/rtlwifi/rtl8188eufw.bin 2>/dev/null && \
            log "  [固件] 已补到 /lib/firmware/rtlwifi/"
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

            # 等接口出现，并且【等 udev 改名结束】。
            #
            # 为什么不能只 sleep 固定秒数：
            #   内核先建接口，起默认名（wlan1），然后异步发 uevent；
            #   容器的 systemd-udevd 收到后才改名成 wlu1u3。
            #   而接口名存在【网络命名空间】里（不是进程里），
            #   手机和容器共用同一个 netns，所以改一次两边都看到。
            #   如果我们在 udev 改名前就 ls，打印的是 wlan1；
            #   改完之后再 ls 就是 wlu1u3 —— 这就是同一命令
            #   不同时候输出不同名字的原因（竞态，不是 bug 也不是随机）。
            #
            # 做法（两段式，避免过早接受默认名）：
            #   内核默认名:  wlan1 / eth0        ← udev 可能马上改掉，不可信
            #   udev 预测名: wlu1u3 / enu1u4     ← 以 wl/en + 类型字母开头，是终态
            #   1) 一旦看到 udev 风格的名字 → 立刻采用（终态）
            #   2) 否则一直等到超时，再用最后看到的默认名兜底
            IF=""
            i=0
            while [ $i -lt 24 ]; do
                NEW=""
                for n in /sys/class/net/*; do
                    _i=$(basename "$n")
                    case "$_i" in
                        lo|dummy*|sit*|tunl*|ip6*|gre*|erspan*|ifb*|rmnet*|r_rmnet*|ovnet*|p2p*|wifi-aware*|wlan0) continue ;;
                    esac
                    _l=$(readlink -f "$n/device" 2>/dev/null)
                    case "$_l" in *usb*) NEW="${NEW:+$NEW }$_i" ;; esac
                done

                [ -n "$NEW" ] && IF="$NEW"

                # 终态判定：udev 的可预测命名规则
                #   wl<type><bus><port>  如 wlu1u3   (wlan over USB)
                #   wlx<mac>             如 wlx488a... (无稳定槽位时按 MAC)
                #   en<type><bus><port>  如 enu1u4   (ethernet over USB)
                #   enx<mac>             如 enx6c1f... (同上)
                # 这些不是内核默认名，说明 udev 已经处理完，不会再变。
                _final=1
                for _i in $NEW; do
                    case "$_i" in
                        wlu*|wlx*|enu*|enx*) : ;;
                        *) _final=0 ;;
                    esac
                done
                if [ -n "$NEW" ] && [ "$_final" = 1 ]; then
                    break
                fi

                sleep 0.5
                i=$((i + 1))
            done

            log "  网卡接口:"
            for _i in $IF; do log "    $_i"; done
            [ -n "$IF" ] || log "    (未发现 USB 网卡，检查是否插好)"

            log ""
            log "  下一步（切 monitor）："
            log "    iw dev <接口名> set type monitor"
            log "    或: airmon-ng start <接口名>"
            log "  或直接执行: sh mode.sh monitor-on <接口名>"
        else
            log "  !! 8188eu 加载失败，看 dmesg 确认原因"
        fi
        ;;

    monitor-on)
        # 直接切到 monitor，省得再手敲 iw。
        # 用法: sh mode.sh monitor-on [接口名]   （不给就自动找 USB 无线接口）
        TGT="$2"
        if [ -z "$TGT" ]; then
            for n in /sys/class/net/*; do
                _i=$(basename "$n")
                case "$_i" in
                    lo|dummy*|sit*|tunl*|ip6*|gre*|erspan*|ifb*|rmnet*|r_rmnet*|ovnet*|p2p*|wifi-aware*|wlan0) continue ;;
                esac
                _l=$(readlink -f "$n/device" 2>/dev/null)
                case "$_l" in *usb*) TGT="$_i"; break ;; esac
            done
        fi
        if [ -z "$TGT" ]; then
            log "!! 找不到 USB 无线接口，先执行: sh mode.sh monitor"
            exit 1
        fi
        log "════════ 把 $TGT 切到 monitor ════════"
        ip link set "$TGT" down 2>&1 || log "  !! down 失败"
        if iw dev "$TGT" set type monitor 2>&1; then
            log "  [OK] 已切到 monitor"
        else
            log "  !! 切换失败（该驱动不支持？或接口名不对）"
            ip link set "$TGT" up 2>/dev/null
            exit 1
        fi
        ip link set "$TGT" up 2>&1 || log "  !! up 失败"
        log "  当前类型: $(iw dev "$TGT" info 2>/dev/null | awk '/type/{print $2}')"
        log "  用法: airmon-ng start $TGT   或   airodump-ng $TGT"
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
        echo "用法: sh mode.sh [status|monitor|monitor-on|normal]"
        echo
        echo "  status      查看当前状态"
        echo "  monitor     切换到 8188eu（支持抓包/注入，用于监听）"
        echo "  monitor-on  切到 8188eu 并直接设成 monitor 模式"
        echo "  normal      切换回 rtl8xxxu（日常上网）"
        exit 1
        ;;
esac
