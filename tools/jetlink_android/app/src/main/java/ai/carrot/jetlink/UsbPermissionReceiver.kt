package ai.carrot.jetlink

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build

class UsbPermissionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val device = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
            else @Suppress("DEPRECATION") intent.getParcelableExtra(UsbManager.EXTRA_DEVICE) as? UsbDevice
        if (intent.action == UsbManager.ACTION_USB_DEVICE_DETACHED) {
            if (device?.deviceName == AppState.deviceName) AppState.stop()
            return
        }
        if (intent.action != ACTION || device == null) return
        val token = intent.getLongExtra("token", -1)
        if (!AppState.permissionGate.consume(token, device.deviceId) || token != AppState.connectionToken) return
        val manager = context.getSystemService(UsbManager::class.java)
        if (!intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) || !manager.hasPermission(device) ||
            !manager.deviceList.containsKey(device.deviceName)) {
            AppState.busy = false; AppState.fail(context.getString(R.string.usb_denied)); return
        }
        AppState.phase = "connecting"
        try {
            context.startForegroundService(Intent(context, LinkService::class.java).putExtra("device", device.deviceName).putExtra("token", token))
        } catch (e: Exception) { AppState.busy = false; AppState.fail(e.message ?: context.getString(R.string.usb_denied)) }
    }
    companion object { const val ACTION = "ai.carrot.jetlink.USB_PERMISSION" }
}
