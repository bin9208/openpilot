package ai.carrot.jetlink

import java.io.File
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WireTest {
    private val fixtures = File(System.getProperty("jetlink.fixtures"))
    private fun fixture() = JSONObject(File(fixtures, "conformance/wire.json").readText())
    private fun String.unhex() = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    @Test fun goldenHeadersMatch() {
        val cases = fixture().getJSONArray("headers")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val h = Header(c.getInt("msg_type"), c.getLong("seq"), c.getInt("flags"), c.getLong("length"),
                c.get("reserved").toString().toBigInteger().toLong())
            val expected = c.getString("hex").unhex()
            assertArrayEquals(expected, Wire.encode(h))
            assertEquals(h, Wire.decode(expected))
        }
    }

    @Test fun corruptVersionAndOversizeRejected() {
        val raw = Wire.encode(Header(15, 7, 0, 0))
        raw[4] = 3
        assertThrows(IllegalArgumentException::class.java) { Wire.decode(raw) }
        assertThrows(IllegalArgumentException::class.java) { Wire.encode(Header(8, 1, 0, 16L * 1024 * 1024 + 1)) }
    }

    @Test fun hostEncodingMatchesCompleteGoldenStream() {
        val all = ArrayList<Byte>()
        val cases = fixture().getJSONArray("messages")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val seq = c.getLong("seq")
            val payload = if (!c.isNull("json")) c.getString("json").toByteArray(Charsets.UTF_8) else {
                val parts = c.getJSONArray("parts")
                val length = (0 until parts.length()).sumOf { parts.getInt(it) }
                ByteArray(length) { ((seq * 31 + it * 7) % 251).toByte() }
            }
            all.addAll(UsbFraming.encodeHost(Wire.message(c.getInt("type"), seq, payload, c.getInt("flags"))).toList())
        }
        assertArrayEquals(File(fixtures, "conformance/wire.usb_host.bin").readBytes(), all.toByteArray())
    }

    @Test fun fragmentedGadgetBurstsReassembleEveryMessage() {
        val source = File(fixtures, "conformance/wire.usb_gadget.bin").readBytes()
        for (chunk in listOf(1, 31, 1024, 16383, 65536)) {
            val decoder = UsbFraming()
            val messages = ArrayList<ByteArray>()
            var at = 0
            while (at < source.size) {
                val end = minOf(at + chunk, source.size)
                messages.addAll(decoder.feed(source.copyOfRange(at, end)))
                at = end
            }
            val expected = fixture().getJSONArray("messages")
            assertEquals(expected.length(), messages.size)
            for ((i, message) in messages.withIndex()) {
                val h = Wire.decode(message.copyOfRange(0, 32))
                assertEquals(expected.getJSONObject(i).getLong("seq"), h.sequence)
                assertEquals(expected.getJSONObject(i).getInt("type"), h.type)
                assertEquals(32 + h.length.toInt(), message.size)
            }
            assertEquals(0, decoder.pendingBytes)
        }
    }
}
