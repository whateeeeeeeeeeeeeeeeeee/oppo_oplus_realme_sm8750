#!/system/bin/sh
# ============================================================================
#  service.sh — 开机完成后执行（late_start service）
#
#  只做一件事：按依赖顺序 insmod 我们的驱动。
#  不做任何清理动作 —— 固件搜索路径要一直有效，因为网卡是热插拔的。
# ============================================================================

MODDIR=${0%/*}
LOG=/data/local/tmp/wifi_usb_drivers.log

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 262144 ]; then
    mv -f "$LOG" "$LOG.old" 2>/dev/null
fi
exec >>"$LOG" 2>&1
echo "===== $(date) service: 加载驱动 ====="

# 等 USB 子系统就绪（最多 30 秒）
i=0
while [ $i -lt 30 ]; do
    [ -d /sys/bus/usb/devices ] && break
    sleep 1
    i=$((i + 1))
done
sleep 3

# ---------------------------------------------------------------------------
# 加载顺序 = 依赖顺序。
# insmod 不像 modprobe 会自动装依赖，被依赖的模块必须先加载，
# 否则后加载的会因 "Unknown symbol" 失败。
#
#   eeprom_93cx6  被 rtl8187 依赖            → 必须最先
#   rtl8xxxu      依赖 mac80211/cfg80211     → 系统已加载
#   rtl8187       依赖 mac80211/cfg80211/rfkill/eeprom_93cx6
#   mt7601u       依赖 mac80211/cfg80211
# ---------------------------------------------------------------------------
for m in eeprom_93cx6 rtl8xxxu rtl8187 mt7601u; do
    f="$MODDIR/system/lib/modules/$m.ko"
    [ -f "$f" ] || continue

    if grep -q "^$m " /proc/modules; then
        echo "  [已加载] $m"
    elif insmod "$f" 2>&1; then
        echo "  [OK]     $m"
    else
        echo "  [失败]   $m"
    fi
done

echo "--- 已加载的相关模块 ---"
grep -E 'rtl8|8187|mt7601|eeprom|cfg80211|mac80211' /proc/modules || echo "  (无)"

echo "--- 网络接口 ---"
ls /sys/class/net/

echo "--- USB 设备（若有 lsusb）---"
command -v lsusb >/dev/null 2>&1 && lsusb || echo "  (无 lsusb)"

echo "--- 固件搜索路径 ---"
cat /sys/module/firmware_class/parameters/path 2>/dev/null || echo "  (读不到)"

echo "===== service 结束 ====="
