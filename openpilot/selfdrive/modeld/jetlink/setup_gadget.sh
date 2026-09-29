#!/usr/bin/env bash
# USB-only configfs setup based on MIT Jetlink f10f470. No caller arguments.
set -euo pipefail
[[ $# -eq 0 && $EUID -eq 0 ]] || exit 1
[[ "$(cat /data/params/d/IsOffroad 2>/dev/null)" == 1 ]] || { echo 'Jetlink setup requires offroad' >&2; exit 1; }
for key in UsbGpuActive UsbGpuLoading; do
  [[ "$(cat "/data/params/d/$key" 2>/dev/null || true)" != 1 ]] || { echo 'eGPU owns USB' >&2; exit 1; }
done
for device in /sys/bus/usb/devices/*; do
  vendor=$(cat "$device/idVendor" 2>/dev/null || true)
  product=$(cat "$device/idProduct" 2>/dev/null || true)
  [[ "$product" != 0001 || ( "$vendor" != add1 && "$vendor" != 3801 ) ]] || { echo 'eGPU USB bridge present' >&2; exit 1; }
done
config=/sys/kernel/config
gadget=$config/usb_gadget/carrot_jetlink
mountpoint -q "$config" || mount -t configfs none "$config"
[[ -d "$config/usb_gadget" && -d /sys/class/udc ]] || exit 1
for other in "$config"/usb_gadget/*/UDC; do
  [[ ! -e "$other" ]] && continue
  [[ -z "$(cat "$other")" ]] || { echo 'A USB gadget already owns the controller' >&2; exit 1; }
done
mkdir -p "$gadget"
echo 0x1209 > "$gadget/idVendor"
echo 0x0001 > "$gadget/idProduct"
echo 0x0320 > "$gadget/bcdUSB"
echo 0x0100 > "$gadget/bcdDevice"
echo 0 > "$gadget/bDeviceClass"
mkdir -p "$gadget/strings/0x409" "$gadget/configs/c.1/strings/0x409"
echo carrotpilot > "$gadget/strings/0x409/manufacturer"
echo jetlink > "$gadget/strings/0x409/product"
cat /etc/machine-id > "$gadget/strings/0x409/serialnumber"
echo 'Jetlink inference' > "$gadget/configs/c.1/strings/0x409/configuration"
echo 0xC0 > "$gadget/configs/c.1/bmAttributes"
echo 8 > "$gadget/configs/c.1/MaxPower"
mkdir -p "$gadget/functions/ffs.carrot_jetlink"
[[ -L "$gadget/configs/c.1/ffs.carrot_jetlink" ]] || ln -s "$gadget/functions/ffs.carrot_jetlink" "$gadget/configs/c.1/ffs.carrot_jetlink"
mkdir -p /dev/ffs-carrot-jetlink
uid=$(id -u comma)
gid=$(id -g comma)
mountpoint -q /dev/ffs-carrot-jetlink || mount -t functionfs -o "uid=$uid,gid=$gid" carrot_jetlink /dev/ffs-carrot-jetlink
[[ -e /dev/ffs-carrot-jetlink/ep0 ]] || exit 1
chown comma "$gadget/UDC"
