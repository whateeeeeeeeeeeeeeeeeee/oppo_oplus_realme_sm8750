#!/system/bin/sh
# ============================================================================
#  bluetooth.sh — 加载/卸载蓝牙内核模块
#
#  【为什么需要这个脚本】
#    手机内核配置里有 CONFIG_BT=m、CONFIG_BT_HCIUART=m、CONFIG_BT_QCA=m，
#    但【系统里没有这些 .ko 文件】—— Android 分区和模块目录里都找不到。
#    结果就是：settings get global bluetooth_on = 1（开关是开的），
#    但 /sys/class/bluetooth/ 是空的、/sys/module/bluetooth 不存在。
#    也就是说内核侧蓝牙根本没起来。
#
#    这些 .ko 现在随模块包提供（bluetooth/ 目录），本脚本负责加载。
#
#  【为什么默认不自动加载】
#    btqca + hci_uart 会接管 /dev/ttyMSM0 串口并给 BT 芯片上电。
#    Android 的蓝牙 HAL 也可能在用那个串口，两边同时操作会打架。
#    所以默认关闭，要用时手动开。
#
#  【用法】（手机侧，需要 root）
#    su -c 'sh bluetooth.sh status'
#    su -c 'sh bluetooth.sh on'
#    su -c 'sh bluetooth.sh off'
#
#  【加载顺序】依赖关系，不能乱：
#    rfkill（通常已加载）
#      └─ bluetooth.ko
#           ├─ hci_uart.ko
#           │    └─ btqca.ko   （高通固件加载）
#           ├─ rfcomm.ko       （串口蓝牙 SPP）
#           └─ hidp.ko         （蓝牙 HID 键盘鼠标）
# ============================================================================

MODDIR=${0%/*}
BDIR="$MODDIR/bluetooth"
LOG=/data/local/tmp/bluetooth.log

log() { echo "$@"; echo "$@" >> "$LOG" 2>/dev/null; }

# 按依赖顺序加载。
#
# 【重要】顺序来自 `modinfo -F depends` 的权威数据，不能凭感觉排：
#   bluetooth    depends: rfkill
#   btbcm        depends: bluetooth
#   btqca        depends: bluetooth
#   rfcomm       depends: bluetooth
#   hidp         depends: bluetooth
#   hci_uart     depends: bluetooth,btbcm,btqca   ← 必须最后！
#
# 之前我把顺序写成 "bluetooth rfcomm hidp hci_uart btqca"，两个错误：
#   1. btqca 排在 hci_uart 后面 → hci_uart 找不到 qca_* 符号
#   2. btbcm 根本没在列表里     → hci_uart 找不到 btbcm_* 符号
# 症状：insmod 报 "No such file or directory"，但文件其实在 ——
# 真实错误是 Unknown symbol (err -2 = -ENOENT)，被 insmod 误报成文件不存在。
ORDER="bluetooth btbcm btqca rfcomm hidp hci_uart"

load_one() {
    m="$1"
    f="$BDIR/$m.ko"
    [ -f "$f" ] || { log "  [跳过] $m.ko 不存在"; return 1; }
    if grep -q "^$m " /proc/modules 2>/dev/null; then
        log "  [已加载] $m"; return 0
    fi
    if insmod "$f" 2>&1; then
        log "  [OK] $m"; return 0
    else
        log "  [失败] $m"; return 1
    fi
}

case "${1:-status}" in
  status)
    log "════════ 蓝牙状态 ════════"
    log ""
    log "【内核模块】"
    for m in rfkill bluetooth rfcomm hidp hci_uart btqca; do
        if grep -q "^$m " /proc/modules 2>/dev/null; then
            log "  [已加载] $m"
        else
            log "  [ 未加载] $m"
        fi
    done
    log ""
    log "【硬件】"
    log "  bt_peach 设备: $([ -d /sys/bus/platform/devices/soc:bt_peach ] && echo 存在 || echo 无)"
    log "  驱动: $(basename $(readlink -f /sys/bus/platform/devices/soc:bt_peach/driver 2>/dev/null) 2>/dev/null)"
    log "  rfkill0: type=$(cat /sys/class/rfkill/rfkill0/type 2>/dev/null) soft=$(cat /sys/class/rfkill/rfkill0/soft 2>/dev/null) hard=$(cat /sys/class/rfkill/rfkill0/hard 2>/dev/null)"
    log "  串口 /dev/ttyMSM0: $([ -e /dev/ttyMSM0 ] && echo 存在 || echo 无)"
    log ""
    log "【HCI 设备】"
    if [ -d /sys/class/bluetooth ] && [ -n "$(ls /sys/class/bluetooth/ 2>/dev/null)" ]; then
        for h in /sys/class/bluetooth/*; do
            log "  $(basename $h)"
        done
    else
        log "  (无 —— 内核蓝牙没起来)"
    fi
    ;;

  on)
    log "════════ 加载蓝牙模块 ════════"
    log ""

    # 1. rfkill 通常已加载（cfg80211 依赖它）
    if ! grep -q "^rfkill " /proc/modules 2>/dev/null; then
        log ">>> 先加载 rfkill"
        for f in "$MODDIR/system/lib/modules/rfkill.ko" "$MODDIR"/rfkill.ko; do
            [ -f "$f" ] && { insmod "$f" 2>&1 && log "  [OK] rfkill"; break; }
        done
    fi

    # 2. 打开蓝牙 rfkill 软开关
    log ""
    log ">>> 打开 rfkill 软开关"
    for r in /sys/class/rfkill/rfkill*; do
        [ -e "$r" ] || continue
        if [ "$(cat $r/type 2>/dev/null)" = "bluetooth" ]; then
            echo 0 > "$r/soft" 2>/dev/null && log "  [OK] $(basename $r) soft=0"
        fi
    done

    # 3. 按依赖顺序加载
    log ""
    log ">>> 加载模块（依赖顺序）"
    for m in $ORDER; do
        load_one "$m"
    done

    # 4. 触发 btqca 绑定串口
    log ""
    log ">>> 等待 HCI 设备出现（最多 10 秒）"
    i=0
    while [ $i -lt 20 ]; do
        if [ -n "$(ls /sys/class/bluetooth/ 2>/dev/null)" ]; then
            log "  ✅ 出现: $(ls /sys/class/bluetooth/ | tr '\n' ' ')"
            break
        fi
        sleep 0.5
        i=$((i + 1))
    done

    if [ -z "$(ls /sys/class/bluetooth/ 2>/dev/null)" ]; then
        log "  ⚠️  仍无 HCI 设备"
        log ""
        log "  可能原因："
        log "    1. Android 蓝牙 HAL 占着 /dev/ttyMSM0"
        log "       → 先在系统设置里【关闭蓝牙】，再跑本脚本"
        log "    2. btqca 固件缺失（需要 qca 的 .bin）"
        log "    3. BT 芯片未上电"
        log ""
        log "  排查:"
        log "    dmesg | grep -iE 'bluetooth|hci|btqca|ttyMSM'"
        log "    ls -la /dev/ttyMSM0"
    else
        log ""
        log "  下一步（容器内）:"
        log "    sudo hciconfig -a        # 看 HCI 设备"
        log "    sudo bluetoothctl        # 交互式蓝牙控制"
    fi
    log ""
    log "--- dmesg 最近蓝牙相关 ---"
    dmesg 2>/dev/null | grep -aiE "bluetooth|hci|btqca" | tail -8 | sed 's/^/  /'
    ;;

  off)
    log "════════ 卸载蓝牙模块 ════════"
    # 逆序卸载
    for m in btqca hci_uart hidp rfcomm bluetooth; do
        if grep -q "^$m " /proc/modules 2>/dev/null; then
            rmmod "$m" 2>/dev/null && log "  已卸载 $m" || log "  !! 卸载 $m 失败（可能被占用）"
        fi
    done
    log ""
    log "  剩余: $(grep -cE '^bluetooth|^hci_uart|^btqca' /proc/modules) 个蓝牙模块"
    ;;

  *)
    echo "用法: sh bluetooth.sh [status|on|off]"
    echo
    echo "  status  查看蓝牙状态"
    echo "  on      加载蓝牙内核模块并打开 rfkill"
    echo "  off     卸载蓝牙模块"
    echo
    echo "提示：加载前最好先在系统设置里【关闭蓝牙】，"
    echo "      避免 Android 蓝牙 HAL 与内核驱动抢 /dev/ttyMSM0"
    exit 1
    ;;
esac
