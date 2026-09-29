package ai.carrot.jetlink

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import kotlin.math.abs
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OrtEngineTest {
    private fun fixture(root: File): ModelPackage {
        File(root, "model.onnx").writeBytes(javaClass.getResourceAsStream("/stateful/model.onnx")!!.use { it.readBytes() })
        val manifest = javaClass.getResourceAsStream("/stateful/manifest.json")!!.bufferedReader().use { it.readText() }
        return ModelPackage.parse(manifest, root)
    }

    @Test fun actualStatefulOnnxMatchesEightGoldenFramesAndResets() {
        val root = Files.createTempDirectory("ort-stateful").toFile()
        try {
            val model = fixture(root)
            val fixtureRoot = File(System.getProperty("jetlink.fixtures"))
            val frames = File(fixtureRoot, "tiny_stateful.frames.bin").readBytes()
            val golden = ByteBuffer.wrap(File(fixtureRoot, "tiny_stateful.expected.bin").readBytes()).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
            val stride = model.warpedBytes + model.packedCount * 4
            var first: FloatArray? = null
            OrtEngine().use { engine ->
                engine.prepare(model)
                for (frame in 0 until 8) {
                    val base = frame * stride
                    val packedBuffer = ByteBuffer.wrap(frames, base + model.warpedBytes, model.packedCount * 4).order(ByteOrder.LITTLE_ENDIAN)
                    val packed = FloatArray(model.packedCount) { packedBuffer.float }
                    val actual = engine.run(frames.copyOfRange(base, base + model.warpedBytes), packed, frame == 0)
                    if (frame == 0) first = actual.copyOf()
                    for (value in actual) {
                        val expected = golden.get()
                        assertTrue("frame=$frame expected=$expected actual=$value", abs(value - expected) <= 1e-5f + abs(expected) * 1e-4f)
                    }
                }
                val packedBuffer = ByteBuffer.wrap(frames, model.warpedBytes, model.packedCount * 4).order(ByteOrder.LITTLE_ENDIAN)
                assertArrayEquals(first, engine.run(frames.copyOfRange(0, model.warpedBytes), FloatArray(model.packedCount) { packedBuffer.float }, true), 0f)
            }
        } finally { root.deleteRecursively() }
    }

    @Test fun actualRuntimeIoMustMatchManifest() {
        val root = Files.createTempDirectory("ort-shape").toFile()
        try {
            val original = fixture(root)
            val changed = JSONObject(original.manifestText)
            changed.getJSONObject("inputs").getJSONObject("action_t").put("shape", JSONArray("[1,3]"))
            val model = ModelPackage.parse(changed.toString(), root)
            OrtEngine().use { engine -> assertThrows(IllegalArgumentException::class.java) { engine.prepare(model) } }
        } finally { root.deleteRecursively() }
    }
}
