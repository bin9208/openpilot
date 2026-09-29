package ai.carrot.jetlink

/** C3X gadget sends whole 16KiB bursts; the Android host sends a short-packet terminator. */
class UsbFraming {
    private var storage = ByteArray(64 * 1024)
    private var start = 0
    private var end = 0
    val pendingBytes get() = end - start

    fun feed(bytes: ByteArray): List<ByteArray> {
        require(pendingBytes.toLong() + bytes.size <= Wire.MAX_PAYLOAD + 2L * BURST) { "USB receive budget exceeded" }
        if (end + bytes.size > storage.size) {
            storage.copyInto(storage, 0, start, end)
            end -= start
            start = 0
            if (end + bytes.size > storage.size) storage = storage.copyOf(maxOf(storage.size * 2, end + bytes.size))
        }
        bytes.copyInto(storage, end)
        end += bytes.size
        val result = ArrayList<ByteArray>()
        while (pendingBytes >= Wire.HEADER_SIZE) {
            val header = Wire.decode(storage.copyOfRange(start, start + Wire.HEADER_SIZE))
            val messageSize = Wire.HEADER_SIZE + header.length.toInt()
            val transferSize = ((messageSize + BURST - 1) / BURST) * BURST
            if (pendingBytes < transferSize) break
            result.add(storage.copyOfRange(start, start + messageSize))
            start += transferSize
        }
        if (start == end) { start = 0; end = 0 }
        return result
    }

    companion object {
        const val BURST = 16 * 1024
        fun encodeHost(message: ByteArray): ByteArray {
            val header = Wire.decode(message)
            require(message.size == Wire.HEADER_SIZE + header.length.toInt()) { "USB message size mismatch" }
            val needsPadding = message.size % 1024 == 0
            val flags = (header.flags and Wire.PADDED.inv()) or if (needsPadding) Wire.PADDED else 0
            val result = message.copyOf(message.size + if (needsPadding) 1 else 0)
            Wire.encode(header.copy(flags = flags)).copyInto(result)
            return result
        }
    }
}
