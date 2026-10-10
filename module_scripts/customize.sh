#!/system/bin/sh
# ============================================================================
#  customize.sh — 安装时显示信息（由 KernelSU/Magisk 在刷入阶段调用）
#  只打印文字，不做任何改动。
# ============================================================================

ui_print "*********************************************"
ui_print " 外置 USB 无线网卡驱动"
ui_print "*********************************************"
ui_print " "
ui_print "- 内核要求: 6.6.89-android15-8-o-4k"
ui_print "- 驱动列表:"
for f in "$MODPATH"/system/lib/modules/*.ko; do
    [ -f "$f" ] || continue
    ui_print "    $(basename "$f")"
done
ui_print " "
ui_print "- 本模块【不修改内核、不写入任何分区】"
ui_print "- 安装日志: /data/local/tmp/wifi_usb_drivers.log"
ui_print "- 失败最坏情况仅网卡不认，不会导致重启"
ui_print " "
