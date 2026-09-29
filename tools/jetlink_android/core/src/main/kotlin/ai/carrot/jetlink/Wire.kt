package ai.carrot.jetlink

import java.nio.ByteBuffer
import java.nio.ByteOrder

data class Header(val type: Int, val sequence: Long, val flags: Int, val length: Long, val reserved: Long = 0)

object Wire {
    const val HEADER_SIZE = 32
    const val MAX_PAYLOAD = 16 * 1024 * 1024
    const val PADDED = 128
    private val messageTypes = (1..9).toSet() + (12..18).toSet()

    private fun validate(h: Header) {
        require(h.type in messageTypes) { "Unsupported Jetlink message" }
        require(h.sequence in 0..0xffffffffL && h.length in 0..MAX_PAYLOAD.toLong()) { "Invalid Jetlink size or sequence" }
        require(h.flags and 0x83 == h.flags) { "Unsupported Jetlink flags" }
    }

    fun encode(header: Header): ByteArray {
        validate(header)
        return ByteBuffer.allocate(HEADER_SIZE).order(ByteOrder.LITTLE_ENDIAN).apply {
            putInt(0x4B4E4C4A); putShort(2); putShort(header.type.toShort())
            putInt(header.sequence.toInt()); putInt(header.flags); putInt(header.length.toInt())
            putLong(header.reserved); putInt(0)
        }.array()
    }

    fun decode(bytes: ByteArray): Header {
        require(bytes.size >= HEADER_SIZE) { "Incomplete Jetlink header" }
        val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        require(b.int == 0x4B4E4C4A && b.short.toInt() == 2) { "Jetlink protocol mismatch" }
        return Header(b.short.toInt() and 0xffff, b.int.toLong() and 0xffffffffL,
            b.int, b.int.toLong() and 0xffffffffL, b.long).also(::validate)
    }

    fun message(type: Int, sequence: Long, payload: ByteArray = byteArrayOf(), flags: Int = 0): ByteArray =
        encode(Header(type, sequence, flags, payload.size.toLong())) + payload
}
