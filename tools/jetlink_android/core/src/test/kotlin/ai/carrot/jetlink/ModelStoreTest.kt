package ai.carrot.jetlink

import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ModelStoreTest {
    internal fun fixture(root: File, content: ByteArray = byteArrayOf(1, 2, 3)): String {
        root.mkdirs()
        File(root, "model.onnx").writeBytes(content)
        val sha = MessageDigest.getInstance("SHA-256").digest(content).joinToString("") { "%02x".format(it) }
        return """{"schema_version":1,"prepare_version":1,"frame_skip":4,
          "source":{"checkpoint":"test","sha256":"$sha","bytes":${content.size}},
          "artifact":{"path":"model.onnx","sha256":"$sha","bytes":${content.size}},
          "runtime":{"name":"onnxruntime","version":"1.22.0","backend":"cpu"},
          "inputs":{"new_img":{"dtype":"uint8","shape":[2,6,2,2]},"desire":{"dtype":"float32","shape":[8]},
             "traffic_convention":{"dtype":"float32","shape":[1,2]},"action_t":{"dtype":"float32","shape":[1,2]},
             "state_x":{"dtype":"float32","shape":[1,4]}},
          "outputs":{"outputs":{"dtype":"float32","shape":[1,4]},"next_state_x":{"dtype":"float32","shape":[1,4]}},
          "output_slices":{"plan":[0,4]},"state_pairs":{"state_x":"next_state_x"}}""".trimIndent()
    }

    @Test fun validPackageAndAtomicUpload() {
        val root = Files.createTempDirectory("jetlink-store").toFile()
        try {
            val text = fixture(root)
            val pkg = ModelPackage.parse(text, root)
            assertEquals(48, pkg.warpedBytes)
            assertEquals(12, pkg.packedCount)
            ModelStore(root, text).use { store ->
                store.begin(pkg.sourceSha, 3)
                store.append(0, byteArrayOf(1))
                store.append(1, byteArrayOf(2, 3))
                assertEquals(pkg.artifactSha, store.finish().artifactSha)
            }
        } finally { root.deleteRecursively() }
    }

    @Test fun wrongHashCannotReplaceGoodModel() {
        val root = Files.createTempDirectory("jetlink-store").toFile()
        try {
            val text = fixture(root)
            val pkg = ModelPackage.parse(text, root)
            ModelStore(root, text).use { store ->
                store.begin(pkg.sourceSha, 3)
                store.append(0, byteArrayOf(9, 9, 9))
                assertThrows(IllegalArgumentException::class.java) { store.finish() }
            }
            assertArrayEquals(byteArrayOf(1, 2, 3), File(root, "model.onnx").readBytes())
        } finally { root.deleteRecursively() }
    }

    @Test fun cancelledAndOutOfOrderUploadKeepExistingModel() {
        val root = Files.createTempDirectory("jetlink-store").toFile()
        try {
            val text = fixture(root)
            val pkg = ModelPackage.parse(text, root)
            ModelStore(root, text).use { store ->
                store.begin(pkg.sourceSha, 3)
                assertThrows(IllegalArgumentException::class.java) { store.append(1, byteArrayOf(1)) }
                store.cancel()
            }
            assertArrayEquals(byteArrayOf(1, 2, 3), File(root, "model.onnx").readBytes())
        } finally { root.deleteRecursively() }
    }

    @Test fun invalidShapeHashPathAndStateRejected() {
        val root = Files.createTempDirectory("jetlink-manifest").toFile()
        try {
            val text = fixture(root)
            for (case in listOf("path", "hash", "shape", "state", "slice")) {
                val json = JSONObject(text)
                when (case) {
                    "path" -> json.getJSONObject("artifact").put("path", "../model.onnx")
                    "hash" -> json.getJSONObject("artifact").put("sha256", "0".repeat(64))
                    "shape" -> json.getJSONObject("inputs").getJSONObject("new_img").put("shape", org.json.JSONArray("[2147483647,2147483647]"))
                    "state" -> json.getJSONObject("state_pairs").put("state_x", "outputs")
                    "slice" -> json.getJSONObject("output_slices").put("extra", org.json.JSONArray("[1,3]"))
                }
                assertThrows(IllegalArgumentException::class.java) { ModelPackage.parse(json.toString(), root) }
            }
        } finally { root.deleteRecursively() }
    }

    @Test fun reservedUploadFileCannotBeAModelArtifact() {
        val root = Files.createTempDirectory("jetlink-reserved").toFile()
        try {
            val json = JSONObject(fixture(root))
            File(root, "model.onnx").copyTo(File(root, ".upload.part"))
            json.getJSONObject("artifact").put("path", ".upload.part")
            assertThrows(IllegalArgumentException::class.java) { ModelPackage.parse(json.toString(), root) }
        } finally { root.deleteRecursively() }
    }

    @Test fun invalidQueuedImageShapeCannotWrapWireByteCount() {
        val root = Files.createTempDirectory("jetlink-overflow").toFile()
        try {
            val json = JSONObject(fixture(root))
            val inputs = json.getJSONObject("inputs")
            inputs.remove("new_img")
            inputs.put("img", JSONObject("""{"dtype":"uint8","shape":[1,1,16384,16384]}"""))
            inputs.put("big_img", JSONObject("""{"dtype":"uint8","shape":[1,1,16384,16384]}"""))
            inputs.put("desire_pulse", JSONObject("""{"dtype":"float32","shape":[1,1,8]}"""))
            inputs.put("features_buffer", JSONObject("""{"dtype":"float32","shape":[1,1,1]}"""))
            assertThrows(IllegalArgumentException::class.java) { ModelPackage.parse(json.toString(), root) }
        } finally { root.deleteRecursively() }
    }
}
