package ai.carrot.jetlink

import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.json.JSONArray
import org.json.JSONObject

/** Identical identities and binary layout to tools.jetlink_model.runner; synthetic inputs only. */
class OutputRecording(private val modelSha: String, private val sourceSha: String, inputs: ByteArray,
    private val frames: Int, private val outputCount: Int, private val slices: JSONObject, private val runtime: String) {
    init {
        require(Regex("[0-9a-f]{64}").matches(modelSha) && Regex("[0-9a-f]{64}").matches(sourceSha))
        require(frames in 1..4096 && outputCount in 1..1048576 && frames.toLong() * outputCount * 4 <= 64L * 1024 * 1024)
    }
    private val inputSha = MessageDigest.getInstance("SHA-256").digest(inputs).joinToString("") { "%02x".format(it) }
    private val output = ByteArrayOutputStream()
    private var count = 0
    fun append(values: FloatArray) {
        require(count < frames && values.size == outputCount && values.all { it.isFinite() })
        val buffer = ByteBuffer.allocate(outputCount * 4).order(ByteOrder.LITTLE_ENDIAN)
        values.forEach { buffer.putFloat(it) }
        output.write(buffer.array()); count++
    }
    fun writeZip(destination: OutputStream) {
        require(count == frames) { "Incomplete output recording" }
        val meta = JSONObject().apply {
            put("model_sha256", modelSha); put("source_sha256", sourceSha); put("executed_model_sha256", modelSha)
            put("inputs_sha256", inputSha); put("frame_ids", JSONArray((0 until count).toList()))
            put("output_count", outputCount); put("output_slices", slices); put("runtime", "onnxruntime-1.22.0-android-$runtime")
            put("scope", "Synthetic Android inference recording; excludes USB, camera warp and vehicle control")
        }
        ZipOutputStream(destination).use { zip ->
            zip.putNextEntry(ZipEntry("run.json")); zip.write(meta.toString(2).toByteArray(Charsets.UTF_8)); zip.closeEntry()
            zip.putNextEntry(ZipEntry("outputs.bin")); output.writeTo(zip); zip.closeEntry()
        }
    }
}
