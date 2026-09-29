package ai.carrot.jetlink

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.zip.ZipInputStream
import org.json.JSONObject

class RecordingTest {
    @Test fun writesIdentifiedFiniteFramesAndRejectsPartialRecording() {
        val recorder = OutputRecording("a".repeat(64), "b".repeat(64), byteArrayOf(1, 2), 2, 2,
            JSONObject().put("plan", org.json.JSONArray(listOf(0, 2))), "cpu")
        recorder.append(floatArrayOf(1f, 2f))
        assertThrows(IllegalArgumentException::class.java) { recorder.writeZip(ByteArrayOutputStream()) }
        assertThrows(IllegalArgumentException::class.java) { recorder.append(floatArrayOf(Float.NaN, 0f)) }
        recorder.append(floatArrayOf(3f, 4f))
        val archive = ByteArrayOutputStream(); recorder.writeZip(archive)
        ZipInputStream(archive.toByteArray().inputStream()).use {
            assertEquals("run.json", it.nextEntry.name)
            val meta = JSONObject(String(it.readBytes(), Charsets.UTF_8))
            assertEquals(2, meta.getJSONArray("frame_ids").length())
            assertEquals(64, meta.getString("inputs_sha256").length)
            assertEquals("outputs.bin", it.nextEntry.name)
            assertEquals(16, it.readBytes().size)
        }
    }
}
