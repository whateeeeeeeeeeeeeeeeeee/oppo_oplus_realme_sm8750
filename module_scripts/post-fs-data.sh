#!/system/bin/sh
# ============================================================================
#  post-fs-data.sh — 开机早期执行（在 /data 挂载后、系统服务启动前）
#
#  只做两件事，都是「放文件 + 设一个搜索路径」，不修改任何系统文件：
#    1. 把固件放到内核能找到的地方
#    2. 准备模块目录权限
#
#  注意：这里【故意不做】任何清理/恢复动作 —— 因为 USB 网卡通常是
#        开机后才插上的，固件要到那时候才会被请求。开机就撤掉路径
#        会导致插上网卡时找不到固件。
# ============================================================================

MODDIR=${0%/*}
LOG=/data/local/tmp/wifi_usb_drivers.log

# 日志轮转：超过 256KB 就改名，避免长期占用 /data
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 262144 ]; then
    mv -f "$LOG" "$LOG.old" 2>/dev/null
fi
exec >>"$LOG" 2>&1
echo "===== $(date) post-fs-data ====="

FW_SRC="$MODDIR/firmware/rtlwifi/rtl8188eufw.bin"
FW_DONE=""

if [ ! -f "$FW_SRC" ]; then
    echo "!! 固件缺失: $FW_SRC"
else
    chmod 644 "$FW_SRC" 2>/dev/null

    # ---- 方式一：复制到内核【无条件】搜索的默认路径 --------------------
    # 内核固件搜索路径（源码 drivers/base/firmware_loader/main.c）末尾固定为：
    #   /lib/firmware/updates/<内核版本>
    #   /lib/firmware/updates
    #   /lib/firmware/<内核版本>
    #   /lib/firmware
    # 所以放进 /lib/firmware/rtlwifi/ 就能被找到，无需改任何内核参数。
    for base in /lib/firmware; do
        if [ -d "$base" ] && [ -w "$base" ]; then
            mkdir -p "$base/rtlwifi" 2>/dev/null
            if cp -f "$FW_SRC" "$base/rtlwifi/" 2>/dev/null; then
                chmod 644 "$base/rtlwifi/rtl8188eufw.bin" 2>/dev/null
                echo "[固件] 已复制到 $base/rtlwifi/ (内核默认路径)"
                FW_DONE=1
            fi
        fi
    done
    [ -n "$FW_DONE" ] || echo "[固件] /lib/firmware 不可写，改用搜索路径参数"

    # ---- 方式二：把本模块目录加进 firmware_class.path ------------------
    # 这是【全局】内核参数，会影响所有驱动的固件查找，所以：
    #   * 用「追加」而不是「覆盖」，保留原值（逗号分隔，最多 10 个路径）
    #   * 只增加一个搜索位置，不删除任何已有位置 → 不会让别的驱动失效
    #   * 重启后该参数自动清空，不做持久化修改
    PARAM=/sys/module/firmware_class/parameters/path
    if [ -w "$PARAM" ]; then
        OLD="$(cat "$PARAM" 2>/dev/null)"
        case ",$OLD," in
            *",$MODDIR/firmware,"*)
                echo "[固件] 搜索路径已包含本模块目录，跳过"
                ;;
            *)
                if [ -n "$OLD" ]; then
                    NEW="$MODDIR/firmware,$OLD"
                else
                    NEW="$MODDIR/firmware"
                fi
                if echo -n "$NEW" > "$PARAM" 2>/dev/null; then
                    echo "[固件] firmware_class.path: '$OLD' -> '$(cat "$PARAM")'"
                    echo "[固件] (追加式，原路径全部保留)"
                else
                    echo "!! 写入 firmware_class.path 失败"
                fi
                ;;
        esac
    fi
fi

# ---- 模块文件权限（只动本模块自己的目录）-------------------------------
chmod 755 "$MODDIR" 2>/dev/null
chmod 755 "$MODDIR/system" "$MODDIR/system/lib" "$MODDIR/system/lib/modules" 2>/dev/null
chmod 644 "$MODDIR"/system/lib/modules/*.ko 2>/dev/null

echo "===== post-fs-data 结束 ====="
