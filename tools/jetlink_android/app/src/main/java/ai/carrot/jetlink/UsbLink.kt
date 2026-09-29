package ai.carrot.jetlink

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import java.io.Closeable
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.ArrayDeque
import java.util.concurrent.atomic.AtomicBoolean

class UsbLink private constructor(
    private val manager: UsbManager, private val device: UsbDevice,
    private val connection: UsbDeviceConnection, private val input: UsbEndpoint, private val output: UsbEndpoint,
) : Closeable {
    private val closed = AtomicBoolean(false)
    private val decoder = UsbFraming()
    private val pending = ArrayDeque<ByteArray>()
    private val readBuffer = ByteArray(64 * 1024)

    fun read(deadlineNs: Long): ByteArray {
        if (pending.isNotEmpty()) return pending.removeFirst()
        while (!closed.get()) {
            if (System.nanoTime() >= deadlineNs) {
                if (decoder.pendingBytes != 0) throw IOException("Incomplete USB frame timed out")
                throw SocketTimeoutException("Waiting for comma")
            }
            val count = connection.bulkTransfer(input, readBuffer, readBuffer.size, 1000)
            if (count > 0) {
                pending.addAll(decoder.feed(readBuffer.copyOf(count)))
                if (pending.isNotEmpty()) return pending.removeFirst()
            } else if (!manager.deviceList.containsKey(device.deviceName)) throw IOException("USB disconnected")
        }
        throw IOException("USB link closed")
    }

    fun write(message: ByteArray, deadlineNs: Long) {
        val bytes = UsbFraming.encodeHost(message)
        var offset = 0
        while (offset < bytes.size && !closed.get()) {
            val remainingMs = (deadlineNs - System.nanoTime()) / 1_000_000
            if (remainingMs <= 0) throw SocketTimeoutException("USB write timed out")
            val length = minOf(64 * 1024, bytes.size - offset)
            val count = connection.bulkTransfer(output, bytes, offset, length, minOf(remainingMs, 1000).toInt().coerceAtLeast(1))
            if (count <= 0) throw IOException("USB write failed")
            offset += count
        }
        if (offset != bytes.size) throw IOException("USB write interrupted")
    }

    override fun close() { if (closed.compareAndSet(false, true)) connection.close() }

    companion object {
        fun supported(device: UsbDevice) = device.vendorId == 0x1209 && device.productId == 0x0001
        fun open(manager: UsbManager, device: UsbDevice): UsbLink {
            require(supported(device) && manager.hasPermission(device)) { "Jetlink USB permission required" }
            var selected: UsbInterface? = null
            var input: UsbEndpoint? = null
            var output: UsbEndpoint? = null
            for (index in 0 until device.interfaceCount) {
                val candidate = device.getInterface(index)
                if (candidate.interfaceClass != UsbConstants.USB_CLASS_VENDOR_SPEC) continue
                val endpoints = (0 until candidate.endpointCount).map { candidate.getEndpoint(it) }.filter { it.type == UsbConstants.USB_ENDPOINT_XFER_BULK }
                val ins = endpoints.filter { it.direction == UsbConstants.USB_DIR_IN }
                val outs = endpoints.filter { it.direction == UsbConstants.USB_DIR_OUT }
                if (ins.size == 1 && outs.size == 1) { selected = candidate; input = ins.single(); output = outs.single(); break }
            }
            require(selected != null && input != null && output != null) { "Jetlink bulk interface not found" }
            val connection = manager.openDevice(device) ?: throw IOException("USB device cannot be opened")
            if (!connection.claimInterface(selected, true)) { connection.close(); throw IOException("USB interface is busy") }
            return UsbLink(manager, device, connection, input, output)
        }
    }
}
