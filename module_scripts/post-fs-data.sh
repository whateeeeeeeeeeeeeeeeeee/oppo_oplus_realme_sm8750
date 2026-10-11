#!/system/bin/sh
# ============================================================================
#  post-fs-data.sh — 开机早期执行（在 /data 挂载后、系统服务启动前）
#
#  只做一件事：准备模块目录权限。
#
#  ---------------------------------------------------------------------------
#  【重要历史教训】这里以前会去改内核的 firmware_class.path 参数，把模块私有
#  目录加进固件搜索路径。那是个 bug，已移除，原因如下：
#
#   1. firmware_class.path 是【全局】参数，对设备上【所有】驱动生效。
#      一旦指向 /data/adb/...，其它驱动请求固件时也会先走那条路径。
#
#   2. /data/adb 在 Android SELinux 策略下是 adb_data_file，
#      内核固件加载线程（kworker）没有权限读 → 每次都是 -EACCES(-13)。
#      dmesg 实证：
#        rtl8xxxu: loading /data/adb/modules/.../rtl8188eufw.bin failed with error -13
#        r8152:    loading /data/adb/modules/.../rtl8153b-2.fw   failed with error -13
#      ↑ 连【有线网卡】的固件都被这条路径拖累，纯属副作用。
#
#   3. 而且 8188eu 驱动【根本不需要外部固件】—— 它内置了固件
#      （hal/rtl8188e/hal8188e_s_fw.c、hal8188e_t_fw.c），
#      源码里 request_firmware 调用次数为 0，CONFIG_FILE_FWIMG 也是注释掉的。
#      真正需要 rtl8188eufw.bin 的是内核自带的 rtl8xxxu。
#
#  现在固件随模块放在 system/etc/firmware/rtlwifi/，由 KernelSU 的
#  overlay 机制挂到 /system/etc/firmware/rtlwifi/。Android 上 /etc -> /system/etc，
#  而 ueventd 的固件搜索列表第一项就是 /etc/firmware，所以能被找到，
#  且【完全不碰】任何全局内核参数。
#  ---------------------------------------------------------------------------
# ============================================================================

MODDIR=${0%/*}
LOG=/data/local/tmp/wifi_usb_drivers.log

# 日志轮转：超过 256KB 就改名，避免长期占用 /data
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 262144 ]; then
    mv -f "$LOG" "$LOG.old" 2>/dev/null
fi
exec >>"$LOG" 2>&1
echo "===== $(date) post-fs-data ====="

# ---- 模块文件权限（只动本模块自己的目录）-------------------------------
chmod 755 "$MODDIR" 2>/dev/null
chmod 755 "$MODDIR/system" 2>/dev/null
chmod 755 "$MODDIR/system/lib" "$MODDIR/system/lib/modules" 2>/dev/null
chmod 644 "$MODDIR"/system/lib/modules/*.ko 2>/dev/null

# ---- 固件权限 -----------------------------------------------------------
# overlay 之后路径是 /system/etc/firmware/rtlwifi/，但模块源目录也要设对，
# 否则 KernelSU 挂载过去的文件权限可能继承成不可读。
FW="$MODDIR/system/etc/firmware/rtlwifi/rtl8188eufw.bin"
if [ -f "$FW" ]; then
    chmod 755 "$MODDIR/system/etc" "$MODDIR/system/etc/firmware" \
               "$MODDIR/system/etc/firmware/rtlwifi" 2>/dev/null
    chmod 644 "$FW" 2>/dev/null
    echo "[固件] $FW 已就位（经 KernelSU overlay 暴露为 /etc/firmware/rtlwifi/）"
else
    echo "[固件] 未找到 $FW（不影响 8188eu，它用内置固件）"
fi

# ---- 兼容清理：把旧版本残留的全局参数撤掉 -------------------------------
# 旧版本（v1~v4）会把模块私有目录写进 firmware_class.path。
# 参数本身重启就清空，但如果是通过模块脚本在同一次开机内升级，
# 这里主动纠正一次，避免残留继续干扰其它驱动。
PARAM=/sys/module/firmware_class/parameters/path
if [ -w "$PARAM" ]; then
    OLD="$(cat "$PARAM" 2>/dev/null)"
    case "$OLD" in
        *wifi_usb_drivers*)
            # 只保留非本模块的条目
            NEW=""
            _ifs="$IFS"; IFS=','
            for p in $OLD; do
                case "$p" in
                    *wifi_usb_drivers*) : ;;
                    *) [ -n "$p" ] && NEW="${NEW:+$NEW,}$p" ;;
                esac
            done
            IFS="$_ifs"
            if echo -n "$NEW" > "$PARAM" 2>/dev/null; then
                echo "[清理] firmware_class.path: '$OLD' -> '$NEW'"
            else
                echo "[清理] !! 纠正 firmware_class.path 失败（重启后自动恢复）"
            fi
            ;;
    esac
fi

echo "===== post-fs-data 结束 ====="
